-- Simplify API Keys: Add 'custom' to allowed service values and update unique constraint
-- This allows API keys to be created without specifying a service

-- Add 'custom' to the allowed service values
ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_service_check;
ALTER TABLE api_keys ADD CONSTRAINT api_keys_service_check 
  CHECK (service IN ('ultravox', 'stripe', 'telnyx', 'google_cloud', 'aws', 'azure', 'openai', 'elevenlabs', 'custom'));

-- Update unique constraint to only check client_id and key_name (remove service)
ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_client_id_service_key_name_key;
ALTER TABLE api_keys ADD CONSTRAINT api_keys_client_id_key_name_key 
  UNIQUE (client_id, key_name);
