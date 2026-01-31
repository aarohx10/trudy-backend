-- ============================================
-- TEMPORARILY REMOVE ALL RESTRICTIONS ON AGENTS TABLE
-- This allows testing if validation/RLS/policies are blocking inserts
-- Use this for debugging only - re-enable restrictions after testing
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

-- Step 5: Temporarily allow NULL clerk_org_id (remove NOT NULL constraint)
ALTER TABLE agents ALTER COLUMN clerk_org_id DROP NOT NULL;

-- Step 6: Drop CHECK constraint that prevents empty clerk_org_id (if exists)
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_clerk_org_id_not_empty;

-- Step 7: List all remaining triggers on agents table (for verification)
SELECT 
    trigger_name, 
    event_manipulation, 
    action_timing,
    action_statement 
FROM information_schema.triggers 
WHERE event_object_table = 'agents'
ORDER BY trigger_name;

-- Step 8: List all remaining constraints on agents table (for verification)
SELECT 
    conname AS constraint_name,
    contype AS constraint_type,
    pg_get_constraintdef(oid) AS constraint_definition
FROM pg_constraint
WHERE conrelid = 'agents'::regclass
ORDER BY conname;

-- ============================================
-- VERIFICATION QUERIES
-- ============================================

-- Check if RLS is disabled
SELECT 
    tablename, 
    rowsecurity,
    CASE WHEN rowsecurity THEN 'RLS ENABLED' ELSE 'RLS DISABLED' END AS rls_status
FROM pg_tables 
WHERE schemaname = 'public' AND tablename = 'agents';

-- List remaining policies (should be empty)
SELECT schemaname, tablename, policyname 
FROM pg_policies 
WHERE tablename = 'agents';

-- Check if clerk_org_id allows NULL
SELECT 
    column_name,
    is_nullable,
    data_type,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public' 
AND table_name = 'agents' 
AND column_name = 'clerk_org_id';

-- ============================================
-- NOTES
-- ============================================
-- 1. RLS is now disabled - no policies will block inserts/selects
-- 2. Validation trigger is removed - no BEFORE INSERT validation
-- 3. NOT NULL constraint is removed - clerk_org_id can be NULL temporarily
-- 4. CHECK constraint is removed - empty strings are allowed temporarily
-- 5. This allows testing if the issue is with validation/RLS or something else
-- 6. After testing, re-enable restrictions if needed
