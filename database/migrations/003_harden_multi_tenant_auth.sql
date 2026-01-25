-- Migration: Harden Multi-Tenant Authentication
-- Ensures no two users can "steal" the same account accidentally
-- Enforces single client_id per Clerk Organization

-- 1. Ensure clerk_user_id is the unique anchor for all users
ALTER TABLE users 
ADD CONSTRAINT IF NOT EXISTS unique_clerk_user_id UNIQUE (clerk_user_id);

-- 2. Ensure client_id is indexed for fast organization switching
CREATE INDEX IF NOT EXISTS idx_users_client_id ON users(client_id);

-- 3. Add index on clerk_organization_id for fast org lookups
CREATE INDEX IF NOT EXISTS idx_clients_clerk_organization_id ON clients(clerk_organization_id);

-- 4. Add comment for documentation
COMMENT ON COLUMN users.clerk_user_id IS 'Unique Clerk user ID - the primary anchor for user identity. Must be unique across all users.';
COMMENT ON COLUMN clients.clerk_organization_id IS 'Clerk Organization ID - links to Clerk org. The client_id should be stored in Clerk org public_metadata for single client_id policy.';
