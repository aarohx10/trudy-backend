-- Migration: Remove Google OAuth (auth0_sub) - Clerk ONLY
-- This migration removes all Google OAuth related constraints and makes auth0_sub nullable
-- Run this after confirming all users are migrated to Clerk

-- ============================================
-- Step 1: Update existing rows to have empty auth0_sub if null
-- ============================================
UPDATE users 
SET auth0_sub = '' 
WHERE auth0_sub IS NULL;

-- ============================================
-- Step 2: Remove unique constraint on auth0_sub
-- ============================================
-- Drop the unique constraint (if it exists)
ALTER TABLE users 
DROP CONSTRAINT IF EXISTS users_auth0_sub_key;

-- ============================================
-- Step 3: Make auth0_sub nullable (or set default empty string)
-- ============================================
-- Option A: Make it nullable (recommended - cleaner)
ALTER TABLE users 
ALTER COLUMN auth0_sub DROP NOT NULL;

-- Option B: Keep NOT NULL but set default to empty string (if you prefer)
-- ALTER TABLE users 
-- ALTER COLUMN auth0_sub SET DEFAULT '';

-- ============================================
-- Step 4: Update existing rows to have empty string (if making nullable)
-- ============================================
-- Set empty string for all existing rows (optional - only if you want consistency)
UPDATE users 
SET auth0_sub = '' 
WHERE auth0_sub IS NULL OR auth0_sub = '';

-- ============================================
-- Step 5: (Optional) Drop the index on auth0_sub
-- ============================================
-- Only drop if you're sure you won't need it
-- DROP INDEX IF EXISTS idx_users_auth0_sub;

-- ============================================
-- Step 6: (Optional) Drop the column entirely
-- ============================================
-- WARNING: Only run this if you're 100% sure you don't need auth0_sub anymore
-- This is irreversible! Make sure:
-- 1. All users have clerk_user_id
-- 2. No code references auth0_sub
-- 3. You have a database backup
-- 
-- ALTER TABLE users DROP COLUMN IF EXISTS auth0_sub;

-- ============================================
-- Verification queries (run after migration)
-- ============================================
-- Check that all users have clerk_user_id:
-- SELECT COUNT(*) as total_users, 
--        COUNT(clerk_user_id) as users_with_clerk_id,
--        COUNT(auth0_sub) as users_with_auth0_sub
-- FROM users;

-- Check for any NULL auth0_sub (should be 0 if you ran the UPDATE):
-- SELECT COUNT(*) FROM users WHERE auth0_sub IS NULL;

-- ============================================
-- Notes
-- ============================================
-- 1. This migration makes auth0_sub nullable to avoid constraint violations
-- 2. All new users will have auth0_sub = '' (empty string) for Clerk-only users
-- 3. The column is kept for now in case you need to rollback
-- 4. After verifying everything works, you can drop the column entirely (Step 6)
