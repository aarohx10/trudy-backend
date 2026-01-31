-- Migration: Enforce NOT NULL constraints and create indexes for clerk_org_id
-- This migration enforces data integrity by ensuring clerk_org_id is never null
-- and creates indexes for performance on organization-scoped queries
--
-- IMPORTANT: This migration assumes:
-- 1. Migration 026 has already removed client_id columns from main tables
-- 2. All existing rows should already have clerk_org_id populated
-- 3. If any rows have NULL clerk_org_id, they will need manual intervention before running this migration

-- ============================================
-- Step 1: Check for NULL clerk_org_id values
-- ============================================
-- This step will warn if there are any NULL values that need to be handled

DO $$
DECLARE
    null_count INTEGER;
BEGIN
    -- Check agents
    SELECT COUNT(*) INTO null_count FROM agents WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in agents table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check calls
    SELECT COUNT(*) INTO null_count FROM calls WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in calls table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check campaigns
    SELECT COUNT(*) INTO null_count FROM campaigns WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in campaigns table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check contacts
    SELECT COUNT(*) INTO null_count FROM contacts WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in contacts table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check contact_folders
    SELECT COUNT(*) INTO null_count FROM contact_folders WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in contact_folders table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check knowledge_bases
    SELECT COUNT(*) INTO null_count FROM knowledge_bases WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in knowledge_bases table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check tools
    SELECT COUNT(*) INTO null_count FROM tools WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in tools table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check voices
    SELECT COUNT(*) INTO null_count FROM voices WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in voices table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
    
    -- Check webhook_endpoints
    SELECT COUNT(*) INTO null_count FROM webhook_endpoints WHERE clerk_org_id IS NULL;
    IF null_count > 0 THEN
        RAISE WARNING 'Found % rows in webhook_endpoints table with NULL clerk_org_id. These need manual handling before enforcing NOT NULL.', null_count;
    END IF;
END $$;

-- ============================================
-- Step 2: Enforce NOT NULL Constraints
-- ============================================
-- Only enforce if no NULL values exist (check above will warn if they do)

ALTER TABLE agents 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE calls 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE campaigns 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE contacts 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE contact_folders 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE knowledge_bases 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE tools 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE voices 
ALTER COLUMN clerk_org_id SET NOT NULL;

ALTER TABLE webhook_endpoints 
ALTER COLUMN clerk_org_id SET NOT NULL;

-- ============================================
-- Step 3: Create Indexes for Performance
-- ============================================
-- Create indexes if they don't already exist (idempotent)

CREATE INDEX IF NOT EXISTS idx_agents_clerk_org_id ON agents(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_calls_clerk_org_id ON calls(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_clerk_org_id ON campaigns(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_contacts_clerk_org_id ON contacts(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_contact_folders_clerk_org_id ON contact_folders(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_clerk_org_id ON knowledge_bases(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_tools_clerk_org_id ON tools(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_voices_clerk_org_id ON voices(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_clerk_org_id ON webhook_endpoints(clerk_org_id);

-- ============================================
-- Step 4: Create Composite Indexes (Optional but Recommended)
-- ============================================
-- These indexes help with common query patterns

-- Agents: Filter by org and status
CREATE INDEX IF NOT EXISTS idx_agents_org_status ON agents(clerk_org_id, status);

-- Calls: Filter by org and status
CREATE INDEX IF NOT EXISTS idx_calls_org_status ON calls(clerk_org_id, status);

-- Calls: Filter by org and created_at (for date range queries)
CREATE INDEX IF NOT EXISTS idx_calls_org_created ON calls(clerk_org_id, created_at DESC);

-- Campaigns: Filter by org and status
CREATE INDEX IF NOT EXISTS idx_campaigns_org_status ON campaigns(clerk_org_id, status);

-- Contacts: Filter by org and folder
CREATE INDEX IF NOT EXISTS idx_contacts_org_folder ON contacts(clerk_org_id, folder_id);

-- ============================================
-- Notes
-- ============================================
-- 1. This migration enforces NOT NULL constraints on clerk_org_id for all main app tables
-- 2. If you see warnings about NULL values, you must handle them manually before running this migration
-- 3. Indexes are created for performance on organization-scoped queries
-- 4. Composite indexes are created for common query patterns
-- 5. All indexes use IF NOT EXISTS to be idempotent
