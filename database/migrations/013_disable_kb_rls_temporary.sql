-- Migration: Temporarily Disable RLS on Knowledge Bases
-- Purpose: Remove all RLS restrictions for testing/development
-- Date: 2026-01-24
-- WARNING: This removes security restrictions. Re-enable RLS in production!

-- Drop all existing policies
DROP POLICY IF EXISTS knowledge_bases_policy ON knowledge_bases;

-- Disable Row Level Security entirely
ALTER TABLE knowledge_bases DISABLE ROW LEVEL SECURITY;
