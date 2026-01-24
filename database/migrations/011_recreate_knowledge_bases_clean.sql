-- Migration: Recreate Knowledge Base Tables - Clean Single Table
-- Purpose: Drop all existing KB tables and create a single, clean table with all required columns
-- Date: 2026-01-24

-- ============================================================
-- STEP 1: Drop existing knowledge base tables and related objects
-- ============================================================

-- Drop triggers first (if they exist)
DROP TRIGGER IF EXISTS update_knowledge_base_chunks_updated_at ON knowledge_base_chunks;
DROP TRIGGER IF EXISTS update_knowledge_base_documents_updated_at ON knowledge_base_documents;
DROP TRIGGER IF EXISTS update_knowledge_documents_updated_at ON knowledge_documents;

-- Drop RLS policies (if they exist)
DROP POLICY IF EXISTS "Service role full access" ON knowledge_base_chunks;
DROP POLICY IF EXISTS "Users can query own KB chunks" ON knowledge_base_chunks;
DROP POLICY IF EXISTS knowledge_documents_policy ON knowledge_documents;
DROP POLICY IF EXISTS knowledge_base_documents_policy ON knowledge_base_documents;

-- Drop functions (if they exist)
DROP FUNCTION IF EXISTS match_kb_documents(VECTOR, UUID, FLOAT, INT);

-- Drop tables (in reverse dependency order)
DROP TABLE IF EXISTS knowledge_base_chunks CASCADE;
DROP TABLE IF EXISTS knowledge_base_documents CASCADE;
DROP TABLE IF EXISTS knowledge_documents CASCADE;

-- ============================================================
-- STEP 2: Create new clean knowledge_bases table
-- ============================================================

CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    content TEXT, -- Extracted text content from uploaded document
    file_name TEXT, -- Original filename
    file_type TEXT, -- File type: pdf, txt, docx, md
    file_size INTEGER, -- File size in bytes
    status TEXT NOT NULL DEFAULT 'creating' CHECK (status IN ('creating', 'ready', 'failed')),
    ultravox_tool_id TEXT, -- Ultravox tool ID for fetching content during calls
    language TEXT NOT NULL DEFAULT 'en-US',
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================
-- STEP 3: Create indexes for performance
-- ============================================================

CREATE INDEX knowledge_bases_client_id_idx ON knowledge_bases(client_id);
CREATE INDEX knowledge_bases_status_idx ON knowledge_bases(status);
CREATE INDEX knowledge_bases_file_type_idx ON knowledge_bases(file_type);
CREATE INDEX knowledge_bases_created_at_idx ON knowledge_bases(created_at DESC);

-- ============================================================
-- STEP 4: Enable Row Level Security
-- ============================================================

ALTER TABLE knowledge_bases ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only access their own client's knowledge bases (same pattern as other tables)
CREATE POLICY knowledge_bases_policy ON knowledge_bases
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

-- ============================================================
-- STEP 5: Create updated_at trigger
-- ============================================================

-- Ensure the function exists (it should from initial schema, but be safe)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_knowledge_bases_updated_at 
    BEFORE UPDATE ON knowledge_bases 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- STEP 6: Add comments for documentation
-- ============================================================

COMMENT ON TABLE knowledge_bases IS 'Knowledge bases with extracted text content and Ultravox tool integration';
COMMENT ON COLUMN knowledge_bases.content IS 'Extracted text content from uploaded document (PDF, TXT, DOCX, MD)';
COMMENT ON COLUMN knowledge_bases.ultravox_tool_id IS 'Ultravox tool ID that fetches this KB content during calls';
COMMENT ON COLUMN knowledge_bases.status IS 'Status: creating (processing), ready (available), failed (error occurred)';
