-- Migration: Fix RLS to work with PostgREST HTTP + ensure set_org_context works
-- PostgREST uses HTTP requests, so SET LOCAL session variables don't persist across requests.
-- However, PostgREST DOES execute RPC calls and queries in the same HTTP request/transaction
-- when called sequentially, so set_org_context should work if called immediately before the query.

-- ============================================
-- Ensure set_org_context function is correct
-- ============================================
CREATE OR REPLACE FUNCTION set_org_context(org_id TEXT)
RETURNS void AS $$
BEGIN
    -- Set org context using SET LOCAL (works within a single PostgREST HTTP request)
    -- CRITICAL: This must be called immediately before each query in the same HTTP request
    PERFORM set_config('app.current_org_id', COALESCE(org_id, ''), true);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- Verify/Update RLS policy for agents table
-- ============================================
-- The policy checks if clerk_org_id matches the session context
-- This will work IF set_org_context is called before each query

DROP POLICY IF EXISTS agents_clerk_org_policy ON agents;
CREATE POLICY agents_clerk_org_policy ON agents
  FOR ALL USING (
    clerk_org_id = current_setting('app.current_org_id', true)
  );

-- ============================================
-- Grant necessary permissions
-- ============================================
GRANT EXECUTE ON FUNCTION set_org_context(TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION set_org_context(TEXT) TO anon;
