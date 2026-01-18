-- Migration: Add Application Logs Table
-- Purpose: Comprehensive logging system for frontend and backend actions
-- Retention: 30 days (configurable via cleanup function)

-- 1. Create application_logs table
CREATE TABLE IF NOT EXISTS application_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL CHECK (source IN ('frontend', 'backend')),
    level TEXT NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    category TEXT NOT NULL, -- 'api_request', 'api_response', 'user_action', 'error', 'auth', 'database', etc.
    message TEXT NOT NULL,
    request_id TEXT, -- Request correlation ID for tracking related logs
    client_id UUID REFERENCES clients(id) ON DELETE SET NULL,
    user_id TEXT, -- User ID (Clerk sub or auth0_sub)
    endpoint TEXT, -- API endpoint or frontend route
    method TEXT, -- HTTP method (GET, POST, PUT, PATCH, DELETE, etc.)
    status_code INTEGER, -- HTTP status code
    duration_ms INTEGER, -- Request duration in milliseconds
    context JSONB DEFAULT '{}'::jsonb, -- Additional context data (request body, response data, etc.)
    error_details JSONB, -- Expanded error information (stack trace, error type, etc.)
    ip_address TEXT, -- Client IP address
    user_agent TEXT, -- User agent string
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- 2. Create indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON application_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_source_level ON application_logs(source, level);
CREATE INDEX IF NOT EXISTS idx_logs_request_id ON application_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_logs_client_id ON application_logs(client_id);
CREATE INDEX IF NOT EXISTS idx_logs_category ON application_logs(category);
CREATE INDEX IF NOT EXISTS idx_logs_endpoint ON application_logs(endpoint);
CREATE INDEX IF NOT EXISTS idx_logs_level ON application_logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_source ON application_logs(source);

-- 3. Add RLS policies
ALTER TABLE application_logs ENABLE ROW LEVEL SECURITY;

-- Policy: Service role can do everything
CREATE POLICY "Service role full access" ON application_logs
  FOR ALL
  USING (auth.role() = 'service_role');

-- Policy: Admin users can view all logs (read-only)
-- Note: Admin authentication is handled at application level, not RLS
-- This policy allows authenticated service role access
CREATE POLICY "Admin can view all logs" ON application_logs
  FOR SELECT
  USING (auth.role() = 'service_role');

-- 4. Add function to clean up old logs (older than 30 days by default)
CREATE OR REPLACE FUNCTION cleanup_old_application_logs(retention_days INTEGER DEFAULT 30)
RETURNS TABLE (
    deleted_count BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_rows BIGINT;
BEGIN
    DELETE FROM application_logs
    WHERE created_at < now() - (retention_days || ' days')::INTERVAL;
    
    GET DIAGNOSTICS deleted_rows = ROW_COUNT;
    
    RETURN QUERY SELECT deleted_rows;
END;
$$;

-- 5. Create function to get log statistics
CREATE OR REPLACE FUNCTION get_log_statistics(
    start_date TIMESTAMPTZ DEFAULT now() - INTERVAL '24 hours',
    end_date TIMESTAMPTZ DEFAULT now()
)
RETURNS TABLE (
    total_logs BIGINT,
    error_count BIGINT,
    warning_count BIGINT,
    frontend_logs BIGINT,
    backend_logs BIGINT,
    error_rate DECIMAL(5, 2)
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT as total_logs,
        COUNT(*) FILTER (WHERE level = 'ERROR' OR level = 'CRITICAL')::BIGINT as error_count,
        COUNT(*) FILTER (WHERE level = 'WARNING')::BIGINT as warning_count,
        COUNT(*) FILTER (WHERE source = 'frontend')::BIGINT as frontend_logs,
        COUNT(*) FILTER (WHERE source = 'backend')::BIGINT as backend_logs,
        CASE 
            WHEN COUNT(*) > 0 THEN 
                ROUND((COUNT(*) FILTER (WHERE level = 'ERROR' OR level = 'CRITICAL')::DECIMAL / COUNT(*)::DECIMAL) * 100, 2)
            ELSE 0
        END as error_rate
    FROM application_logs
    WHERE created_at >= start_date AND created_at <= end_date;
END;
$$;
