-- Migration: Harden Team Collaboration - Single Client ID Policy
-- This ensures team members share the same client_id via Clerk Org metadata

-- 1. Ensure clerk_user_id is unique to prevent duplicate users
ALTER TABLE users 
ADD CONSTRAINT IF NOT EXISTS unique_clerk_user_id UNIQUE (clerk_user_id);

-- 2. Add index for faster organization/client lookups
CREATE INDEX IF NOT EXISTS idx_users_client_id ON users(client_id);

-- 3. Add comment for documentation
COMMENT ON CONSTRAINT unique_clerk_user_id ON users IS 'Ensures each Clerk user maps to exactly one database user record';
