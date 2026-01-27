-- Telephony Integration: Add tables for carrier credentials and phone numbers
-- This enables Trudy to manage phone numbers from Telnyx and support BYO carrier imports

-- 1. Table for storing carrier credentials (encrypted at rest)
CREATE TABLE IF NOT EXISTS telephony_credentials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES clients(id) ON DELETE CASCADE,
    provider_type TEXT NOT NULL CHECK (provider_type IN ('telnyx', 'twilio', 'plivo', 'custom_sip')),
    friendly_name TEXT,
    
    -- Carrier specific keys (encrypted)
    api_key TEXT, -- Encrypted for Telnyx/Twilio API keys
    account_sid TEXT, -- For Twilio/Telnyx (encrypted)
    auth_token TEXT, -- Encrypted
    
    -- SIP specific fields (encrypted)
    sip_username TEXT, -- Encrypted
    sip_password TEXT, -- Encrypted
    sip_server TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Table for tracking purchased/imported numbers
CREATE TABLE IF NOT EXISTS phone_numbers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES clients(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL, -- Nullable if not assigned
    phone_number TEXT UNIQUE NOT NULL, -- E.164 format
    provider_id TEXT, -- The ID from Telnyx/Twilio
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'pending')),
    is_trudy_managed BOOLEAN DEFAULT true, -- True if purchased via Trudy, false if BYO
    telephony_credential_id UUID REFERENCES telephony_credentials(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Add telephony metadata to the agents table
ALTER TABLE agents 
ADD COLUMN IF NOT EXISTS inbound_regex TEXT,
ADD COLUMN IF NOT EXISTS telephony_provider_id UUID REFERENCES telephony_credentials(id) ON DELETE SET NULL;

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_telephony_credentials_org ON telephony_credentials(organization_id);
CREATE INDEX IF NOT EXISTS idx_phone_numbers_org ON phone_numbers(organization_id);
CREATE INDEX IF NOT EXISTS idx_phone_numbers_agent ON phone_numbers(agent_id);
CREATE INDEX IF NOT EXISTS idx_phone_numbers_number ON phone_numbers(phone_number);
CREATE INDEX IF NOT EXISTS idx_agents_telephony_provider ON agents(telephony_provider_id);

-- Updated_at triggers
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_telephony_credentials_updated_at BEFORE UPDATE ON telephony_credentials
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_phone_numbers_updated_at BEFORE UPDATE ON phone_numbers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
