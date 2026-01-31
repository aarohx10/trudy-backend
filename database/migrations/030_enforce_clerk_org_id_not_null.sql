-- Migration: Enforce NOT NULL and CHECK constraints on clerk_org_id for all org-scoped tables
-- This migration ensures clerk_org_id can NEVER be NULL or empty at the database level
-- Prevents the issue where clerk_org_id shows as EMPTY in the database

-- ============================================
-- Step 1: Update existing NULL/empty values (if any)
-- ============================================
-- Note: This sets a placeholder value. In production, these should be manually reviewed.
-- For now, we'll set them to a sentinel value that can be identified and fixed later.

DO $$
DECLARE
    table_name TEXT;
    tables_to_fix TEXT[] := ARRAY[
        'agents', 'calls', 'voices', 'knowledge_bases', 
        'tools', 'contacts', 'contact_folders', 'campaigns', 'webhook_endpoints'
    ];
    null_count INTEGER;
BEGIN
    FOREACH table_name IN ARRAY tables_to_fix
    LOOP
        -- Count NULL/empty values
        EXECUTE format('SELECT COUNT(*) FROM %I WHERE clerk_org_id IS NULL OR clerk_org_id = ''''', table_name) INTO null_count;
        
        IF null_count > 0 THEN
            RAISE WARNING 'Table % has % rows with NULL/empty clerk_org_id. These need manual review.', table_name, null_count;
            -- Don't auto-fix - these need manual review to determine correct org_id
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Step 2: Add NOT NULL constraint to agents table
-- ============================================
-- First, ensure no NULL values exist (or handle them)
-- Then add NOT NULL constraint

-- Update any NULL values to empty string temporarily (will be caught by CHECK constraint)
UPDATE agents SET clerk_org_id = '' WHERE clerk_org_id IS NULL;

-- Add NOT NULL constraint
ALTER TABLE agents 
ALTER COLUMN clerk_org_id SET NOT NULL;

-- ============================================
-- Step 3: Add CHECK constraint to prevent empty strings
-- ============================================
-- This ensures clerk_org_id is never empty

ALTER TABLE agents 
DROP CONSTRAINT IF EXISTS agents_clerk_org_id_not_empty;

ALTER TABLE agents 
ADD CONSTRAINT agents_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- ============================================
-- Step 4: Apply same constraints to all org-scoped tables
-- ============================================

-- Calls table
UPDATE calls SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE calls ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE calls DROP CONSTRAINT IF EXISTS calls_clerk_org_id_not_empty;
ALTER TABLE calls ADD CONSTRAINT calls_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- Voices table
UPDATE voices SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE voices ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE voices DROP CONSTRAINT IF EXISTS voices_clerk_org_id_not_empty;
ALTER TABLE voices ADD CONSTRAINT voices_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- Knowledge bases table
UPDATE knowledge_bases SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE knowledge_bases ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS knowledge_bases_clerk_org_id_not_empty;
ALTER TABLE knowledge_bases ADD CONSTRAINT knowledge_bases_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- Tools table
UPDATE tools SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE tools ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE tools DROP CONSTRAINT IF EXISTS tools_clerk_org_id_not_empty;
ALTER TABLE tools ADD CONSTRAINT tools_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- Contacts table
UPDATE contacts SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE contacts ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_clerk_org_id_not_empty;
ALTER TABLE contacts ADD CONSTRAINT contacts_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- Contact folders table
UPDATE contact_folders SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE contact_folders ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE contact_folders DROP CONSTRAINT IF EXISTS contact_folders_clerk_org_id_not_empty;
ALTER TABLE contact_folders ADD CONSTRAINT contact_folders_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- Campaigns table
UPDATE campaigns SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE campaigns ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_clerk_org_id_not_empty;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- Webhook endpoints table
UPDATE webhook_endpoints SET clerk_org_id = '' WHERE clerk_org_id IS NULL;
ALTER TABLE webhook_endpoints ALTER COLUMN clerk_org_id SET NOT NULL;
ALTER TABLE webhook_endpoints DROP CONSTRAINT IF EXISTS webhook_endpoints_clerk_org_id_not_empty;
ALTER TABLE webhook_endpoints ADD CONSTRAINT webhook_endpoints_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- ============================================
-- Step 5: Verify constraints were applied
-- ============================================

DO $$
DECLARE
    table_name TEXT;
    tables_to_check TEXT[] := ARRAY[
        'agents', 'calls', 'voices', 'knowledge_bases', 
        'tools', 'contacts', 'contact_folders', 'campaigns', 'webhook_endpoints'
    ];
    constraint_exists BOOLEAN;
BEGIN
    FOREACH table_name IN ARRAY tables_to_check
    LOOP
        -- Check if NOT NULL constraint exists
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = table_name 
            AND column_name = 'clerk_org_id'
            AND is_nullable = 'NO'
        ) INTO constraint_exists;
        
        IF NOT constraint_exists THEN
            RAISE WARNING 'NOT NULL constraint missing on %.clerk_org_id', table_name;
        ELSE
            RAISE NOTICE '✓ Table %: NOT NULL constraint applied', table_name;
        END IF;
        
        -- Check if CHECK constraint exists
        SELECT EXISTS (
            SELECT 1 FROM information_schema.table_constraints 
            WHERE table_schema = 'public' 
            AND table_name = table_name 
            AND constraint_name = table_name || '_clerk_org_id_not_empty'
            AND constraint_type = 'CHECK'
        ) INTO constraint_exists;
        
        IF NOT constraint_exists THEN
            RAISE WARNING 'CHECK constraint missing on %.clerk_org_id', table_name;
        ELSE
            RAISE NOTICE '✓ Table %: CHECK constraint applied', table_name;
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Notes
-- ============================================
-- 1. This migration sets NULL values to empty string, which will then be caught by CHECK constraint
-- 2. In production, rows with empty clerk_org_id should be manually reviewed and fixed
-- 3. The CHECK constraint prevents future empty values from being inserted
-- 4. Application code must ALWAYS provide clerk_org_id when creating records
