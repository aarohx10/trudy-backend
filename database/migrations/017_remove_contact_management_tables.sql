-- Migration: Remove Contact Management System Tables
-- Purpose: Drop contacts and contact_folders tables and all related database objects
-- Note: This removes the folder-based contact management system, NOT campaign_contacts

-- ============================================
-- 1. Drop RLS Policies (if they exist)
-- ============================================

-- Drop policies for contacts table
DROP POLICY IF EXISTS contacts_select_policy ON contacts;
DROP POLICY IF EXISTS contacts_insert_policy ON contacts;
DROP POLICY IF EXISTS contacts_update_policy ON contacts;
DROP POLICY IF EXISTS contacts_delete_policy ON contacts;

-- Drop policies for contact_folders table
DROP POLICY IF EXISTS contact_folders_select_policy ON contact_folders;
DROP POLICY IF EXISTS contact_folders_insert_policy ON contact_folders;
DROP POLICY IF EXISTS contact_folders_update_policy ON contact_folders;
DROP POLICY IF EXISTS contact_folders_delete_policy ON contact_folders;

-- Drop any other policies that might exist (catch-all)
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT schemaname, tablename, policyname 
              FROM pg_policies 
              WHERE schemaname = 'public' 
              AND tablename IN ('contacts', 'contact_folders')) LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', 
                       r.policyname, r.schemaname, r.tablename);
    END LOOP;
END $$;

-- ============================================
-- 2. Disable RLS (if enabled)
-- ============================================

ALTER TABLE IF EXISTS contacts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS contact_folders DISABLE ROW LEVEL SECURITY;

-- ============================================
-- 3. Drop Triggers
-- ============================================

DROP TRIGGER IF EXISTS update_contacts_updated_at ON contacts;
DROP TRIGGER IF EXISTS update_contact_folders_updated_at ON contact_folders;

-- ============================================
-- 4. Drop Indexes
-- ============================================

DROP INDEX IF EXISTS idx_contacts_client_id;
DROP INDEX IF EXISTS idx_contacts_folder_id;
DROP INDEX IF EXISTS idx_contact_folders_client_id;

-- ============================================
-- 5. Drop Tables (CASCADE will handle foreign keys)
-- ============================================

DROP TABLE IF EXISTS contacts CASCADE;
DROP TABLE IF EXISTS contact_folders CASCADE;

-- ============================================
-- Migration Complete
-- ============================================
-- Note: campaign_contacts table remains untouched as it's part of the campaign system
