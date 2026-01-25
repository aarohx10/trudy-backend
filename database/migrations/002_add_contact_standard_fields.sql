-- Migration: Add Standard Fields to Contacts Table for Hybrid-JSONB Model
-- This enables dynamic lead import similar to Instantly/Smartlead
-- Standard fields for performance/filtering, metadata JSONB for flexibility

-- 1. Add missing standard columns to the contacts table
ALTER TABLE contacts 
ADD COLUMN IF NOT EXISTS company_name TEXT,
ADD COLUMN IF NOT EXISTS industry TEXT,
ADD COLUMN IF NOT EXISTS location TEXT,
ADD COLUMN IF NOT EXISTS pin_code TEXT,
ADD COLUMN IF NOT EXISTS keywords TEXT[]; -- Array type for easier searching

-- 2. Ensure metadata is a JSONB column (it exists but we want to ensure it handles merges well)
ALTER TABLE contacts 
ALTER COLUMN metadata SET DEFAULT '{}'::jsonb;

-- 3. Add an index for JSONB performance (Enterprise Scaling)
CREATE INDEX IF NOT EXISTS idx_contacts_metadata_gin ON contacts USING GIN (metadata);

-- 4. Add indexes for standard fields for better query performance
CREATE INDEX IF NOT EXISTS idx_contacts_company_name ON contacts(company_name);
CREATE INDEX IF NOT EXISTS idx_contacts_industry ON contacts(industry);
CREATE INDEX IF NOT EXISTS idx_contacts_location ON contacts(location);
CREATE INDEX IF NOT EXISTS idx_contacts_keywords ON contacts USING GIN(keywords);

-- 5. Add comment for documentation
COMMENT ON COLUMN contacts.metadata IS 'JSONB field for storing custom/dynamic fields not in standard schema. Standard fields: phone_number, email, first_name, last_name, company_name, industry, location, pin_code, keywords';
