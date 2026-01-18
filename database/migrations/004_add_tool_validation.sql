-- Migration: Add Tool Validation Fields
-- Purpose: Enable tool verification and runtime logging

-- 1. Add is_verified field to tools table
ALTER TABLE tools ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT false;
ALTER TABLE tools ADD COLUMN IF NOT EXISTS verification_error TEXT;

-- 2. Create tool_logs table for runtime logging
CREATE TABLE IF NOT EXISTS tool_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id UUID REFERENCES tools(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    call_id UUID REFERENCES calls(id) ON DELETE SET NULL,
    session_id TEXT, -- For grouping logs from a single test session
    request_url TEXT NOT NULL,
    request_method TEXT NOT NULL,
    request_headers JSONB,
    request_body JSONB,
    response_status INTEGER,
    response_headers JSONB,
    response_body JSONB,
    response_time_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Create indexes for fast lookups
CREATE INDEX IF NOT EXISTS tool_logs_tool_id_idx ON tool_logs(tool_id);
CREATE INDEX IF NOT EXISTS tool_logs_agent_id_idx ON tool_logs(agent_id);
CREATE INDEX IF NOT EXISTS tool_logs_call_id_idx ON tool_logs(call_id);
CREATE INDEX IF NOT EXISTS tool_logs_session_id_idx ON tool_logs(session_id);
CREATE INDEX IF NOT EXISTS tool_logs_created_at_idx ON tool_logs(created_at DESC);

-- 4. Add RLS policies
ALTER TABLE tool_logs ENABLE ROW LEVEL SECURITY;

-- Policy: Service role can do everything
CREATE POLICY "Service role full access" ON tool_logs
  FOR ALL
  USING (auth.role() = 'service_role');

-- Policy: Users can view logs for their own tools
CREATE POLICY "Users can view own tool logs" ON tool_logs
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM tools
      WHERE tools.id = tool_logs.tool_id
        AND tools.client_id = auth.uid()::text
    )
  );

-- 5. Add function to clean up old logs (older than 7 days)
CREATE OR REPLACE FUNCTION cleanup_old_tool_logs()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  DELETE FROM tool_logs
  WHERE created_at < now() - INTERVAL '7 days';
END;
$$;
