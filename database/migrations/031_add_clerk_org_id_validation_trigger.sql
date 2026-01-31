-- Migration: Add BEFORE INSERT trigger to validate clerk_org_id on agents table
-- This provides a database-level safety net to ensure clerk_org_id is never empty
-- Even if application code fails, the database will reject invalid inserts

-- ============================================
-- Create validation function for agents table
-- ============================================

CREATE OR REPLACE FUNCTION validate_agents_clerk_org_id()
RETURNS TRIGGER AS $$
BEGIN
    -- CRITICAL: Ensure clerk_org_id is never NULL or empty
    IF NEW.clerk_org_id IS NULL THEN
        RAISE EXCEPTION 'clerk_org_id cannot be NULL for agents table. Application must provide organization ID.';
    END IF;
    
    IF NEW.clerk_org_id = '' OR TRIM(NEW.clerk_org_id) = '' THEN
        RAISE EXCEPTION 'clerk_org_id cannot be empty for agents table. Application must provide valid organization ID.';
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Create BEFORE INSERT trigger
-- ============================================

DROP TRIGGER IF EXISTS agents_validate_clerk_org_id ON agents;

CREATE TRIGGER agents_validate_clerk_org_id
BEFORE INSERT ON agents
FOR EACH ROW
EXECUTE FUNCTION validate_agents_clerk_org_id();

-- ============================================
-- Grant necessary permissions
-- ============================================

GRANT EXECUTE ON FUNCTION validate_agents_clerk_org_id() TO authenticated;
GRANT EXECUTE ON FUNCTION validate_agents_clerk_org_id() TO anon;

-- ============================================
-- Notes
-- ============================================
-- 1. This trigger runs BEFORE INSERT, so it validates data before it's saved
-- 2. If clerk_org_id is NULL or empty, the INSERT will fail with a clear error message
-- 3. This provides database-level enforcement even if application code has bugs
-- 4. The error message guides developers to fix the application code
