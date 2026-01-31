-- Migration: Remove client_id from main application tables
-- This migration removes client_id columns, foreign keys, and indexes from main app tables
-- client_id remains only in billing/audit tables (clients, users, api_keys, credit_transactions, audit_log, application_logs, idempotency_keys)
--
-- Main app tables affected:
-- - agents, calls, voices, knowledge_bases, tools, contacts, contact_folders, campaigns, webhook_endpoints
--
-- IMPORTANT: Ensure all rows have clerk_org_id populated before running this migration
-- This migration assumes clerk_org_id columns already exist (from migrations 022, 023, 024)

-- ============================================
-- Step 1: Drop Foreign Key Constraints
-- ============================================

-- Agents table
ALTER TABLE agents 
DROP CONSTRAINT IF EXISTS agents_client_id_fkey;

-- Calls table
ALTER TABLE calls 
DROP CONSTRAINT IF EXISTS calls_client_id_fkey;

-- Voices table
ALTER TABLE voices 
DROP CONSTRAINT IF EXISTS voices_client_id_fkey;

-- Knowledge bases table
ALTER TABLE knowledge_bases 
DROP CONSTRAINT IF EXISTS knowledge_bases_client_id_fkey;

-- Tools table
ALTER TABLE tools 
DROP CONSTRAINT IF EXISTS tools_client_id_fkey;

-- Contacts table
ALTER TABLE contacts 
DROP CONSTRAINT IF EXISTS contacts_client_id_fkey;

-- Contact folders table
ALTER TABLE contact_folders 
DROP CONSTRAINT IF EXISTS contact_folders_client_id_fkey;

-- Campaigns table
ALTER TABLE campaigns 
DROP CONSTRAINT IF EXISTS campaigns_client_id_fkey;

-- Webhook endpoints table
ALTER TABLE webhook_endpoints 
DROP CONSTRAINT IF EXISTS webhook_endpoints_client_id_fkey;

-- ============================================
-- Step 2: Drop Indexes on client_id
-- ============================================

-- Drop indexes from main app tables
DROP INDEX IF EXISTS idx_agents_client_id;
DROP INDEX IF EXISTS idx_calls_client_id;
DROP INDEX IF EXISTS idx_voices_client_id;
DROP INDEX IF EXISTS idx_knowledge_bases_client_id;
DROP INDEX IF EXISTS idx_tools_client_id;
DROP INDEX IF EXISTS idx_contacts_client_id;
DROP INDEX IF EXISTS idx_contact_folders_client_id;
DROP INDEX IF EXISTS idx_campaigns_client_id;
DROP INDEX IF EXISTS idx_webhook_endpoints_client_id;

-- Drop composite indexes that include client_id
DROP INDEX IF EXISTS agents_client_id_status_idx;
DROP INDEX IF EXISTS knowledge_bases_client_id_idx;

-- ============================================
-- Step 3: Drop client_id Columns
-- ============================================

-- Agents table
ALTER TABLE agents 
DROP COLUMN IF EXISTS client_id;

-- Calls table
ALTER TABLE calls 
DROP COLUMN IF EXISTS client_id;

-- Voices table
ALTER TABLE voices 
DROP COLUMN IF EXISTS client_id;

-- Knowledge bases table
ALTER TABLE knowledge_bases 
DROP COLUMN IF EXISTS client_id;

-- Tools table
ALTER TABLE tools 
DROP COLUMN IF EXISTS client_id;

-- Contacts table
ALTER TABLE contacts 
DROP COLUMN IF EXISTS client_id;

-- Contact folders table
ALTER TABLE contact_folders 
DROP COLUMN IF EXISTS client_id;

-- Campaigns table
ALTER TABLE campaigns 
DROP COLUMN IF EXISTS client_id;

-- Webhook endpoints table
ALTER TABLE webhook_endpoints 
DROP COLUMN IF EXISTS client_id;

-- ============================================
-- Step 4: Verify clerk_org_id Columns Exist
-- ============================================
-- Note: These columns should already exist from migrations 022, 023, 024
-- This is just a verification step - if columns don't exist, migration will fail

DO $$
BEGIN
    -- Verify clerk_org_id columns exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'agents' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from agents table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'calls' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from calls table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'voices' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from voices table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'knowledge_bases' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from knowledge_bases table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'tools' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from tools table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'contacts' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from contacts table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'contact_folders' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from contact_folders table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'campaigns' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from campaigns table. Run migrations 022, 023, 024 first.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'webhook_endpoints' AND column_name = 'clerk_org_id'
    ) THEN
        RAISE EXCEPTION 'clerk_org_id column missing from webhook_endpoints table. Run migrations 022, 023, 024 first.';
    END IF;
END $$;

-- ============================================
-- Step 5: Verify Indexes on clerk_org_id Exist
-- ============================================
-- Note: These indexes should already exist from migrations 022, 023
-- This is just a verification step

DO $$
BEGIN
    -- Verify indexes exist
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'agents' AND indexname = 'idx_agents_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_agents_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'calls' AND indexname = 'idx_calls_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_calls_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'voices' AND indexname = 'idx_voices_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_voices_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'knowledge_bases' AND indexname = 'idx_knowledge_bases_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_knowledge_bases_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'tools' AND indexname = 'idx_tools_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_tools_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'contacts' AND indexname = 'idx_contacts_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_contacts_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'contact_folders' AND indexname = 'idx_contact_folders_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_contact_folders_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'campaigns' AND indexname = 'idx_campaigns_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_campaigns_clerk_org_id missing. Consider creating it for performance.';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'webhook_endpoints' AND indexname = 'idx_webhook_endpoints_clerk_org_id'
    ) THEN
        RAISE WARNING 'Index idx_webhook_endpoints_clerk_org_id missing. Consider creating it for performance.';
    END IF;
END $$;

-- ============================================
-- Notes
-- ============================================
-- 1. This migration removes client_id from main app tables only
-- 2. client_id remains in billing/audit tables: clients, users, api_keys, credit_transactions, audit_log, application_logs, idempotency_keys
-- 3. All main app data is now scoped by clerk_org_id (organization-first approach)
-- 4. Ensure all existing rows have clerk_org_id populated before running this migration
-- 5. After migration, all backend code should use clerk_org_id for filtering main app tables
