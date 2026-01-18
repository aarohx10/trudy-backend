-- Migration: Add Post-Call Intelligence Fields
-- Purpose: Enable automated transcript analysis, sentiment scoring, and structured data extraction

-- 1. Add analysis fields to calls table
ALTER TABLE calls ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS sentiment TEXT CHECK (sentiment IN ('positive', 'neutral', 'negative')) DEFAULT 'neutral';
ALTER TABLE calls ADD COLUMN IF NOT EXISTS structured_data JSONB DEFAULT '{}'::jsonb;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS is_success BOOLEAN DEFAULT false;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS analysis_status TEXT DEFAULT 'pending' CHECK (analysis_status IN ('pending', 'processing', 'completed', 'failed'));
ALTER TABLE calls ADD COLUMN IF NOT EXISTS analysis_error TEXT;

-- 2. Add intelligence fields to agents table
ALTER TABLE agents ADD COLUMN IF NOT EXISTS success_criteria TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS extraction_schema JSONB DEFAULT '{}'::jsonb;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS crm_webhook_url TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS crm_webhook_secret TEXT;

-- 3. Create indexes for analytics queries
CREATE INDEX IF NOT EXISTS calls_sentiment_idx ON calls(sentiment);
CREATE INDEX IF NOT EXISTS calls_is_success_idx ON calls(is_success);
CREATE INDEX IF NOT EXISTS calls_analysis_status_idx ON calls(analysis_status);
CREATE INDEX IF NOT EXISTS calls_agent_id_status_idx ON calls(agent_id, status);

-- 4. Add function to calculate success rate for an agent
CREATE OR REPLACE FUNCTION calculate_agent_success_rate(agent_uuid UUID, date_from TIMESTAMPTZ DEFAULT NULL, date_to TIMESTAMPTZ DEFAULT NULL)
RETURNS TABLE (
    total_calls BIGINT,
    successful_calls BIGINT,
    success_rate DECIMAL(5, 2)
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT as total_calls,
        COUNT(*) FILTER (WHERE is_success = true)::BIGINT as successful_calls,
        CASE
            WHEN COUNT(*) > 0 THEN
                ROUND((COUNT(*) FILTER (WHERE is_success = true)::DECIMAL / COUNT(*)::DECIMAL) * 100, 2)
            ELSE 0
        END as success_rate
    FROM calls
    WHERE agent_id = agent_uuid
        AND status = 'completed'
        AND (date_from IS NULL OR created_at >= date_from)
        AND (date_to IS NULL OR created_at <= date_to);
END;
$$;
