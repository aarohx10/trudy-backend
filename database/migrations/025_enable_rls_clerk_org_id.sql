-- Migration 025: Enable RLS on core tables with clerk_org_id policies
-- Ensures database-level isolation: rows visible only when app.current_org_id matches.
-- Backend must call set_org_context(org_id) at start of each request (already in get_db_client).

-- ============================================
-- 1. Ensure set_org_context exists (from 021)
-- ============================================
CREATE OR REPLACE FUNCTION set_org_context(org_id TEXT)
RETURNS void AS $$
BEGIN
    PERFORM set_config('app.current_org_id', COALESCE(org_id, ''), true);
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 2. Enable RLS on core tables
-- ============================================
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE voices ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_bases ENABLE ROW LEVEL SECURITY;
ALTER TABLE tools ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_endpoints ENABLE ROW LEVEL SECURITY;

-- ============================================
-- 3. Policies: allow access only when clerk_org_id matches session
-- ============================================

-- Agents
DROP POLICY IF EXISTS agents_clerk_org_policy ON agents;
CREATE POLICY agents_clerk_org_policy ON agents
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Calls
DROP POLICY IF EXISTS calls_clerk_org_policy ON calls;
CREATE POLICY calls_clerk_org_policy ON calls
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Voices
DROP POLICY IF EXISTS voices_clerk_org_policy ON voices;
CREATE POLICY voices_clerk_org_policy ON voices
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Campaigns
DROP POLICY IF EXISTS campaigns_clerk_org_policy ON campaigns;
CREATE POLICY campaigns_clerk_org_policy ON campaigns
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Knowledge bases
DROP POLICY IF EXISTS knowledge_bases_clerk_org_policy ON knowledge_bases;
CREATE POLICY knowledge_bases_clerk_org_policy ON knowledge_bases
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Tools
DROP POLICY IF EXISTS tools_clerk_org_policy ON tools;
CREATE POLICY tools_clerk_org_policy ON tools
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Contact folders
DROP POLICY IF EXISTS contact_folders_clerk_org_policy ON contact_folders;
CREATE POLICY contact_folders_clerk_org_policy ON contact_folders
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Contacts
DROP POLICY IF EXISTS contacts_clerk_org_policy ON contacts;
CREATE POLICY contacts_clerk_org_policy ON contacts
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- Webhook endpoints
DROP POLICY IF EXISTS webhook_endpoints_clerk_org_policy ON webhook_endpoints;
CREATE POLICY webhook_endpoints_clerk_org_policy ON webhook_endpoints
  FOR ALL USING (clerk_org_id = current_setting('app.current_org_id', true));

-- ============================================
-- Notes
-- ============================================
-- 1. Backend must call set_org_context(org_id) with Clerk org_id from JWT for every request.
-- 2. get_db_client(org_id) in app/core/database.py calls client.rpc("set_org_context", {"org_id": org_id}).
-- 3. When using Supabase with anon key, RLS applies. Service role key bypasses RLS.
-- 4. If a table is accessed without setting context, current_setting returns '' and no rows match (safe).
