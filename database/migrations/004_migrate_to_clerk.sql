-- Migration: Add Clerk support to database schema
-- This migration adds Clerk user and organization IDs while maintaining backward compatibility

-- ============================================
-- Add Clerk columns to existing tables
-- ============================================

-- Add clerk_user_id to users table (nullable for migration period)
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS clerk_user_id TEXT;

-- Add clerk_organization_id to clients table (nullable for migration period)
ALTER TABLE clients 
ADD COLUMN IF NOT EXISTS clerk_organization_id TEXT;

-- Create indexes for Clerk lookups
CREATE INDEX IF NOT EXISTS idx_users_clerk_user_id ON users(clerk_user_id);
CREATE INDEX IF NOT EXISTS idx_clients_clerk_organization_id ON clients(clerk_organization_id);

-- ============================================
-- Update RLS helper functions for Clerk JWT
-- ============================================

-- Update jwt_user_id() to read from Clerk JWT 'sub' claim
CREATE OR REPLACE FUNCTION jwt_user_id() RETURNS TEXT AS $$
BEGIN
    RETURN current_setting('request.jwt.claims', true)::json->>'sub';
END;
$$ LANGUAGE plpgsql STABLE;

-- Add helper function to get Clerk organization ID from JWT
CREATE OR REPLACE FUNCTION jwt_clerk_org_id() RETURNS TEXT AS $$
BEGIN
    RETURN current_setting('request.jwt.claims', true)::json->>'org_id';
END;
$$ LANGUAGE plpgsql STABLE;

-- Update jwt_client_id() to support both Clerk org_id and legacy client_id
CREATE OR REPLACE FUNCTION jwt_client_id() RETURNS UUID AS $$
DECLARE
    v_org_id TEXT;
    v_client_id TEXT;
BEGIN
    -- Try to get org_id from Clerk JWT first
    v_org_id := jwt_clerk_org_id();
    IF v_org_id IS NOT NULL THEN
        -- Look up client_id from clerk_organization_id
        SELECT id INTO v_client_id
        FROM clients
        WHERE clerk_organization_id = v_org_id
        LIMIT 1;
        IF v_client_id IS NOT NULL THEN
            RETURN v_client_id::UUID;
        END IF;
    END IF;
    
    -- Fallback to legacy client_id claim
    v_client_id := current_setting('request.jwt.claims', true)::json->>'client_id';
    IF v_client_id IS NOT NULL THEN
        RETURN v_client_id::UUID;
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- Migration data script (run separately after migration)
-- ============================================

-- This script copies auth0_sub to clerk_user_id for existing users
-- Run this AFTER users have been migrated to Clerk
-- DO NOT RUN THIS AUTOMATICALLY - requires manual verification

/*
-- Example migration script (commented out for safety):
-- UPDATE users 
-- SET clerk_user_id = auth0_sub 
-- WHERE clerk_user_id IS NULL AND auth0_sub IS NOT NULL;
*/

-- ============================================
-- Add unique constraint on clerk_user_id (after migration)
-- ============================================

-- Uncomment after migration is complete and all users have clerk_user_id:
-- ALTER TABLE users ADD CONSTRAINT users_clerk_user_id_unique UNIQUE (clerk_user_id);

-- ============================================
-- Notes
-- ============================================
-- 1. Keep auth0_sub column for backward compatibility during migration
-- 2. Support both Clerk and Google tokens during transition period
-- 3. After migration is complete, make clerk_user_id NOT NULL and add unique constraint
-- 4. Remove auth0_sub column only after all users are migrated and verified

