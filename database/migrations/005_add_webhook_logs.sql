-- Migration: Add Webhook Logs Table
-- Purpose: Log all incoming webhook events for debugging and audit

-- 1. Create webhook_logs table
CREATE TABLE IF NOT EXISTS webhook_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL, -- 'ultravox', 'stripe', 'telnyx', etc.
    event_type TEXT NOT NULL,
    event_id TEXT, -- Provider's event ID if available
    payload JSONB NOT NULL,
    headers JSONB,
    signature_valid BOOLEAN,
    processing_status TEXT DEFAULT 'pending', -- 'pending', 'processed', 'failed'
    error_message TEXT,
    processing_time_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Create indexes for fast lookups
CREATE INDEX IF NOT EXISTS webhook_logs_provider_idx ON webhook_logs(provider);
CREATE INDEX IF NOT EXISTS webhook_logs_event_type_idx ON webhook_logs(event_type);
CREATE INDEX IF NOT EXISTS webhook_logs_created_at_idx ON webhook_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS webhook_logs_processing_status_idx ON webhook_logs(processing_status);

-- 3. Add RLS policies
ALTER TABLE webhook_logs ENABLE ROW LEVEL SECURITY;

-- Policy: Service role can do everything
CREATE POLICY "Service role full access" ON webhook_logs
  FOR ALL
  USING (auth.role() = 'service_role');

-- Policy: Users can view webhook logs (read-only)
CREATE POLICY "Users can view webhook logs" ON webhook_logs
  FOR SELECT
  USING (true); -- Webhook logs are system-level, but allow viewing for debugging

-- 4. Add function to clean up old logs (older than 30 days)
CREATE OR REPLACE FUNCTION cleanup_old_webhook_logs()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  DELETE FROM webhook_logs
  WHERE created_at < now() - INTERVAL '30 days';
END;
$$;
