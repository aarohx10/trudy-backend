-- Migration: Add content and file metadata fields to knowledge_documents
-- Purpose: Store extracted text content and file metadata for knowledge bases
-- Date: 2026-01-24

ALTER TABLE knowledge_documents 
ADD COLUMN IF NOT EXISTS content TEXT,
ADD COLUMN IF NOT EXISTS file_type TEXT,
ADD COLUMN IF NOT EXISTS file_size INTEGER,
ADD COLUMN IF NOT EXISTS file_name TEXT,
ADD COLUMN IF NOT EXISTS ultravox_tool_id TEXT;

-- Add index on file_type for filtering
CREATE INDEX IF NOT EXISTS knowledge_documents_file_type_idx ON knowledge_documents(file_type);

-- Add index on status for filtering
CREATE INDEX IF NOT EXISTS knowledge_documents_status_idx ON knowledge_documents(status);
