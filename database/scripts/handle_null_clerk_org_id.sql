-- Script: Handle NULL clerk_org_id values
-- This script helps identify and handle rows with NULL clerk_org_id
-- Run this BEFORE migration 032 if you see warnings about NULL values
--
-- IMPORTANT: Since client_id has been removed (migration 026), we cannot automatically
-- backfill clerk_org_id from the clients table. You must manually determine the correct
-- clerk_org_id for each orphaned record.

-- ============================================
-- Step 1: Identify NULL clerk_org_id values
-- ============================================

-- Show counts of NULL values per table
SELECT 
    'agents' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM agents
UNION ALL
SELECT 
    'calls' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM calls
UNION ALL
SELECT 
    'campaigns' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM campaigns
UNION ALL
SELECT 
    'contacts' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM contacts
UNION ALL
SELECT 
    'contact_folders' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM contact_folders
UNION ALL
SELECT 
    'knowledge_bases' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM knowledge_bases
UNION ALL
SELECT 
    'tools' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM tools
UNION ALL
SELECT 
    'voices' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM voices
UNION ALL
SELECT 
    'webhook_endpoints' as table_name,
    COUNT(*) as null_count,
    COUNT(*) FILTER (WHERE clerk_org_id IS NOT NULL) as non_null_count
FROM webhook_endpoints;

-- ============================================
-- Step 2: Show sample NULL records (for manual review)
-- ============================================

-- Sample NULL agents
SELECT 'agents' as table_name, id, name, created_at, clerk_org_id
FROM agents 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL calls
SELECT 'calls' as table_name, id, agent_id, phone_number, status, created_at, clerk_org_id
FROM calls 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL campaigns
SELECT 'campaigns' as table_name, id, name, status, created_at, clerk_org_id
FROM campaigns 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL contacts
SELECT 'contacts' as table_name, id, folder_id, phone_number, first_name, created_at, clerk_org_id
FROM contacts 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL contact_folders
SELECT 'contact_folders' as table_name, id, name, created_at, clerk_org_id
FROM contact_folders 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL knowledge_bases
SELECT 'knowledge_bases' as table_name, id, name, status, created_at, clerk_org_id
FROM knowledge_bases 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL tools
SELECT 'tools' as table_name, id, name, status, created_at, clerk_org_id
FROM tools 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL voices
SELECT 'voices' as table_name, id, name, provider, type, created_at, clerk_org_id
FROM voices 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- Sample NULL webhook_endpoints
SELECT 'webhook_endpoints' as table_name, id, url, enabled, created_at, clerk_org_id
FROM webhook_endpoints 
WHERE clerk_org_id IS NULL 
LIMIT 10;

-- ============================================
-- Step 3: Manual Update Template
-- ============================================
-- Use these templates to manually update NULL values
-- Replace 'YOUR_CLERK_ORG_ID_HERE' with the actual organization ID

/*
-- Example: Update NULL agents (replace with actual org_id)
UPDATE agents 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('agent-id-1', 'agent-id-2'); -- Specify which records to update

-- Example: Update NULL calls (replace with actual org_id)
UPDATE calls 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('call-id-1', 'call-id-2'); -- Specify which records to update

-- Example: Update NULL campaigns (replace with actual org_id)
UPDATE campaigns 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('campaign-id-1', 'campaign-id-2'); -- Specify which records to update

-- Example: Update NULL contacts (replace with actual org_id)
UPDATE contacts 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('contact-id-1', 'contact-id-2'); -- Specify which records to update

-- Example: Update NULL contact_folders (replace with actual org_id)
UPDATE contact_folders 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('folder-id-1', 'folder-id-2'); -- Specify which records to update

-- Example: Update NULL knowledge_bases (replace with actual org_id)
UPDATE knowledge_bases 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('kb-id-1', 'kb-id-2'); -- Specify which records to update

-- Example: Update NULL tools (replace with actual org_id)
UPDATE tools 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('tool-id-1', 'tool-id-2'); -- Specify which records to update

-- Example: Update NULL voices (replace with actual org_id)
UPDATE voices 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('voice-id-1', 'voice-id-2'); -- Specify which records to update

-- Example: Update NULL webhook_endpoints (replace with actual org_id)
UPDATE webhook_endpoints 
SET clerk_org_id = 'YOUR_CLERK_ORG_ID_HERE'
WHERE clerk_org_id IS NULL 
AND id IN ('webhook-id-1', 'webhook-id-2'); -- Specify which records to update
*/

-- ============================================
-- Step 4: Alternative - Delete orphaned records (USE WITH CAUTION)
-- ============================================
-- Only use this if you're certain these records should be deleted
-- This is a destructive operation - backup your database first!

/*
-- WARNING: These DELETE statements will permanently remove records
-- Only run if you're certain these records should be deleted

-- Delete orphaned agents
DELETE FROM agents WHERE clerk_org_id IS NULL;

-- Delete orphaned calls
DELETE FROM calls WHERE clerk_org_id IS NULL;

-- Delete orphaned campaigns
DELETE FROM campaigns WHERE clerk_org_id IS NULL;

-- Delete orphaned contacts
DELETE FROM contacts WHERE clerk_org_id IS NULL;

-- Delete orphaned contact_folders
DELETE FROM contact_folders WHERE clerk_org_id IS NULL;

-- Delete orphaned knowledge_bases
DELETE FROM knowledge_bases WHERE clerk_org_id IS NULL;

-- Delete orphaned tools
DELETE FROM tools WHERE clerk_org_id IS NULL;

-- Delete orphaned voices
DELETE FROM voices WHERE clerk_org_id IS NULL;

-- Delete orphaned webhook_endpoints
DELETE FROM webhook_endpoints WHERE clerk_org_id IS NULL;
*/
