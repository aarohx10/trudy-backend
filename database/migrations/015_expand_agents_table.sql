-- Migration: Expand Agents Table
-- Purpose: Add all Ultravox callTemplate fields to support comprehensive agent configuration

-- Add new columns for callTemplate fields
ALTER TABLE agents ADD COLUMN IF NOT EXISTS call_template_name TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS greeting_settings JSONB DEFAULT '{}'::jsonb;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS inactivity_messages JSONB DEFAULT '[]'::jsonb;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS temperature DECIMAL(3, 2) DEFAULT 0.3 CHECK (temperature >= 0 AND temperature <= 1);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS language_hint TEXT DEFAULT 'en-US';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS time_exceeded_message TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS recording_enabled BOOLEAN DEFAULT false;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS join_timeout TEXT DEFAULT '30s';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_duration TEXT DEFAULT '3600s';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS initial_output_medium TEXT DEFAULT 'MESSAGE_MEDIUM_VOICE' CHECK (initial_output_medium IN ('MESSAGE_MEDIUM_VOICE', 'MESSAGE_MEDIUM_TEXT', 'MESSAGE_MEDIUM_UNSPECIFIED'));
ALTER TABLE agents ADD COLUMN IF NOT EXISTS vad_settings JSONB DEFAULT '{}'::jsonb;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS template_id UUID REFERENCES agent_templates(id) ON DELETE SET NULL;

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS agents_template_id_idx ON agents(template_id);
CREATE INDEX IF NOT EXISTS agents_status_idx ON agents(status);
CREATE INDEX IF NOT EXISTS agents_client_id_status_idx ON agents(client_id, status);

-- Add comments for documentation
COMMENT ON COLUMN agents.call_template_name IS 'Name of the call template in Ultravox';
COMMENT ON COLUMN agents.greeting_settings IS 'First speaker settings (agent/user, text, prompt, delay, uninterruptible) stored as JSONB';
COMMENT ON COLUMN agents.inactivity_messages IS 'Array of inactivity message configurations stored as JSONB';
COMMENT ON COLUMN agents.temperature IS 'LLM temperature setting (0-1)';
COMMENT ON COLUMN agents.language_hint IS 'BCP47 language code for the agent';
COMMENT ON COLUMN agents.time_exceeded_message IS 'Message to display when max duration is reached';
COMMENT ON COLUMN agents.recording_enabled IS 'Whether calls with this agent are recorded';
COMMENT ON COLUMN agents.join_timeout IS 'Timeout string for joining calls (e.g., "30s")';
COMMENT ON COLUMN agents.max_duration IS 'Maximum call duration string (e.g., "3600s")';
COMMENT ON COLUMN agents.initial_output_medium IS 'Initial output medium for agent responses';
COMMENT ON COLUMN agents.vad_settings IS 'Voice activity detection settings (turnEndpointDelay, minimumTurnDuration, minimumInterruptionDuration, frameActivationThreshold) stored as JSONB';
COMMENT ON COLUMN agents.template_id IS 'Reference to the agent template used to create this agent';
