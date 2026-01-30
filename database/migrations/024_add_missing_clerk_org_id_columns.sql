-- Migration 024: Add missing clerk_org_id and created_by_user_id columns
-- Use this when your live Supabase schema is missing these columns (e.g. 022/023 not fully applied).
-- Safe to run: uses IF NOT EXISTS so already-present columns are skipped.

-- ============================================
-- calls: add created_by_user_id if missing
-- ============================================
ALTER TABLE calls
ADD COLUMN IF NOT EXISTS created_by_user_id TEXT;

CREATE INDEX IF NOT EXISTS idx_calls_created_by_user_id ON calls(created_by_user_id);

-- ============================================
-- campaigns: add clerk_org_id if missing
-- ============================================
ALTER TABLE campaigns
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_campaigns_clerk_org_id ON campaigns(clerk_org_id);

-- ============================================
-- contact_folders: add clerk_org_id if missing
-- ============================================
ALTER TABLE contact_folders
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_contact_folders_clerk_org_id ON contact_folders(clerk_org_id);

-- ============================================
-- contacts: add clerk_org_id if missing
-- ============================================
ALTER TABLE contacts
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_clerk_org_id ON contacts(clerk_org_id);

-- ============================================
-- knowledge_bases: add clerk_org_id if missing
-- ============================================
ALTER TABLE knowledge_bases
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_clerk_org_id ON knowledge_bases(clerk_org_id);

-- ============================================
-- webhook_endpoints: add clerk_org_id if missing
-- ============================================
ALTER TABLE webhook_endpoints
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_clerk_org_id ON webhook_endpoints(clerk_org_id);

-- ============================================
-- Optional: Backfill clerk_org_id from clients
-- ============================================
-- Run these only if you have existing rows and want to set clerk_org_id from client_id.
-- Uncomment and run after the ALTERs above.

-- UPDATE campaigns c
-- SET clerk_org_id = cl.clerk_organization_id
-- FROM clients cl
-- WHERE c.client_id = cl.id AND (c.clerk_org_id IS NULL OR c.clerk_org_id = '');

-- UPDATE contact_folders cf
-- SET clerk_org_id = cl.clerk_organization_id
-- FROM clients cl
-- WHERE cf.client_id = cl.id AND (cf.clerk_org_id IS NULL OR cf.clerk_org_id = '');

-- UPDATE contacts co
-- SET clerk_org_id = cl.clerk_organization_id
-- FROM clients cl
-- WHERE co.client_id = cl.id AND (co.clerk_org_id IS NULL OR co.clerk_org_id = '');

-- UPDATE knowledge_bases kb
-- SET clerk_org_id = cl.clerk_organization_id
-- FROM clients cl
-- WHERE kb.client_id = cl.id AND (kb.clerk_org_id IS NULL OR kb.clerk_org_id = '');

-- UPDATE webhook_endpoints we
-- SET clerk_org_id = cl.clerk_organization_id
-- FROM clients cl
-- WHERE we.client_id = cl.id AND (we.clerk_org_id IS NULL OR we.clerk_org_id = '');
