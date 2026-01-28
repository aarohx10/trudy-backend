-- Migration: Add org_id context support for organization-first data access
-- This migration adds a function to set org_id context for database operations

-- ============================================
-- Add function to set org_id context
-- ============================================

CREATE OR REPLACE FUNCTION set_org_context(org_id TEXT)
RETURNS void AS $$
BEGIN
    -- Set the org_id as a session variable for use in RLS policies
    PERFORM set_config('app.current_org_id', org_id, true);
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Add helper function to get current org_id from context
-- ============================================

CREATE OR REPLACE FUNCTION current_org_id() RETURNS TEXT AS $$
BEGIN
    RETURN current_setting('app.current_org_id', true);
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- Notes
-- ============================================
-- 1. This function sets a session variable that can be used in RLS policies
-- 2. The org_id is extracted from Clerk JWT claims in the application layer
-- 3. For personal workspaces (no org), user_id is used as org_id
-- 4. This ensures all database operations are scoped to the organization
