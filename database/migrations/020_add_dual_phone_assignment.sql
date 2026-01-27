-- Add support for dual phone number assignment (inbound and outbound)
-- This enables a phone number to be assigned to different agents for inbound vs outbound calls

-- Add columns to phone_numbers table for dual assignment
ALTER TABLE phone_numbers
ADD COLUMN IF NOT EXISTS inbound_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS outbound_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL;

-- Migrate existing agent_id to inbound_agent_id for backward compatibility
UPDATE phone_numbers 
SET inbound_agent_id = agent_id 
WHERE agent_id IS NOT NULL AND inbound_agent_id IS NULL;

-- Add indexes for performance
CREATE INDEX IF NOT EXISTS idx_phone_numbers_inbound_agent ON phone_numbers(inbound_agent_id);
CREATE INDEX IF NOT EXISTS idx_phone_numbers_outbound_agent ON phone_numbers(outbound_agent_id);

-- Add agent phone number tracking (reverse lookup for fast queries)
ALTER TABLE agents
ADD COLUMN IF NOT EXISTS inbound_phone_number_id UUID REFERENCES phone_numbers(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS outbound_phone_number_id UUID REFERENCES phone_numbers(id) ON DELETE SET NULL;

-- Add indexes for reverse lookup
CREATE INDEX IF NOT EXISTS idx_agents_inbound_phone_number ON agents(inbound_phone_number_id);
CREATE INDEX IF NOT EXISTS idx_agents_outbound_phone_number ON agents(outbound_phone_number_id);
