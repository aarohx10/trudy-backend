-- ============================================
-- DISABLE RLS AND REMOVE POLICIES FOR AGENTS TABLE
-- This simplifies the agents table to allow direct inserts/selects
-- Application code handles org_id filtering
-- ============================================

-- Step 1: Drop all RLS policies on agents table
DROP POLICY IF EXISTS agents_clerk_org_policy ON agents;
DROP POLICY IF EXISTS agents_select_policy ON agents;
DROP POLICY IF EXISTS agents_insert_policy ON agents;
DROP POLICY IF EXISTS agents_update_policy ON agents;
DROP POLICY IF EXISTS agents_delete_policy ON agents;
DROP POLICY IF EXISTS agents_policy ON agents;

-- Step 2: Disable RLS on agents table
ALTER TABLE agents DISABLE ROW LEVEL SECURITY;

-- Step 3: Drop validation trigger (if exists)
DROP TRIGGER IF EXISTS agents_validate_clerk_org_id ON agents;

-- Step 4: Drop validation function (if exists)
DROP FUNCTION IF EXISTS validate_agents_clerk_org_id();

-- Step 5: Check for any other triggers that might modify clerk_org_id
-- List all triggers on agents table
SELECT 
    trigger_name, 
    event_manipulation, 
    action_statement 
FROM information_schema.triggers 
WHERE event_object_table = 'agents';

-- Step 6: Drop set_org_context function (no longer needed for agents)
-- NOTE: Keep this function if other tables still use it
-- DROP FUNCTION IF EXISTS set_org_context(TEXT);

-- Step 7: Drop current_org_id helper function (if exists and not used elsewhere)
-- DROP FUNCTION IF EXISTS current_org_id();

-- ============================================
-- VERIFICATION
-- ============================================
-- Check if RLS is disabled
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname = 'public' AND tablename = 'agents';

-- List remaining policies (should be empty)
SELECT schemaname, tablename, policyname 
FROM pg_policies 
WHERE tablename = 'agents';

-- ============================================
-- NOTES
-- ============================================
-- 1. RLS is now disabled on agents table
-- 2. Application code must filter by clerk_org_id
-- 3. Database constraints (NOT NULL, CHECK) still enforce clerk_org_id presence
-- 4. This allows direct inserts/selects without RLS blocking
