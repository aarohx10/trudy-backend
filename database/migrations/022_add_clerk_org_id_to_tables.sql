-- Migration: Add clerk_org_id to tables for organization-first data access
-- This migration adds clerk_org_id columns to key tables for organization scoping

-- ============================================
-- Add clerk_org_id to agents table
-- ============================================

ALTER TABLE agents 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_agents_clerk_org_id ON agents(clerk_org_id);

-- ============================================
-- Add clerk_org_id and created_by_user_id to calls table
-- ============================================

ALTER TABLE calls 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT,
ADD COLUMN IF NOT EXISTS created_by_user_id TEXT;

CREATE INDEX IF NOT EXISTS idx_calls_clerk_org_id ON calls(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_calls_created_by_user_id ON calls(created_by_user_id);

-- ============================================
-- Add clerk_org_id to other key tables
-- ============================================

-- Voices table
ALTER TABLE voices 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_voices_clerk_org_id ON voices(clerk_org_id);

-- Knowledge bases table
ALTER TABLE knowledge_bases 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_clerk_org_id ON knowledge_bases(clerk_org_id);

-- Tools table
ALTER TABLE tools 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_tools_clerk_org_id ON tools(clerk_org_id);

-- Contacts table
ALTER TABLE contacts 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_clerk_org_id ON contacts(clerk_org_id);

-- Contact folders table
ALTER TABLE contact_folders 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_contact_folders_clerk_org_id ON contact_folders(clerk_org_id);

-- Campaigns table
ALTER TABLE campaigns 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_campaigns_clerk_org_id ON campaigns(clerk_org_id);

-- ============================================
-- Notes
-- ============================================
-- 1. These columns enable organization-first data access
-- 2. For personal workspaces (no org), user_id is used as org_id
-- 3. All queries should filter by clerk_org_id instead of client_id/user_id
-- 4. The created_by_user_id in calls table tracks which user initiated the call
