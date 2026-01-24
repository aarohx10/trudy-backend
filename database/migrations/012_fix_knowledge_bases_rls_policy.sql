-- Migration: Fix Knowledge Bases RLS Policy
-- Purpose: Add WITH CHECK clause to allow INSERT operations
-- Date: 2026-01-24
-- Run this if you already ran 011_recreate_knowledge_bases_clean.sql without the WITH CHECK clause

-- Drop existing policy
DROP POLICY IF EXISTS knowledge_bases_policy ON knowledge_bases;

-- Recreate policy with both USING and WITH CHECK clauses
CREATE POLICY knowledge_bases_policy ON knowledge_bases
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    )
    WITH CHECK (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );
