-- Migration: Add Knowledge Base Chunks with Vector Support
-- Purpose: Enable Proxy Knowledge Base using Supabase pgvector
-- Run this in Supabase SQL Editor before deploying backend changes

-- 1. Enable Vector Support
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create Knowledge Base Chunks Table
CREATE TABLE IF NOT EXISTS knowledge_base_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding VECTOR(1536), -- Dimension for OpenAI text-embedding-3-small
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Create Index for Fast Similarity Search
CREATE INDEX IF NOT EXISTS knowledge_base_chunks_kb_id_idx ON knowledge_base_chunks(kb_id);
CREATE INDEX IF NOT EXISTS knowledge_base_chunks_embedding_idx ON knowledge_base_chunks USING hnsw (embedding vector_cosine_ops);

-- 4. Create Search Function (RPC)
CREATE OR REPLACE FUNCTION match_kb_documents (
  query_embedding VECTOR(1536),
  target_kb_id UUID,
  match_threshold FLOAT DEFAULT 0.7,
  match_count INT DEFAULT 3
)
RETURNS TABLE (
  id UUID,
  content TEXT,
  similarity FLOAT
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  RETURN QUERY
  SELECT
    knowledge_base_chunks.id,
    knowledge_base_chunks.content,
    1 - (knowledge_base_chunks.embedding <=> query_embedding) AS similarity
  FROM knowledge_base_chunks
  WHERE knowledge_base_chunks.kb_id = target_kb_id
    AND knowledge_base_chunks.embedding IS NOT NULL
    AND 1 - (knowledge_base_chunks.embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

-- 5. Add RLS Policies (if RLS is enabled)
-- Allow service role to insert/query chunks
-- Allow authenticated users to query chunks for their own KBs
ALTER TABLE knowledge_base_chunks ENABLE ROW LEVEL SECURITY;

-- Policy: Service role can do everything
CREATE POLICY "Service role full access" ON knowledge_base_chunks
  FOR ALL
  USING (auth.role() = 'service_role');

-- Policy: Users can query chunks for their own knowledge bases
CREATE POLICY "Users can query own KB chunks" ON knowledge_base_chunks
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM knowledge_documents
      WHERE knowledge_documents.id = knowledge_base_chunks.kb_id
        AND knowledge_documents.client_id = auth.uid()::text
    )
  );

-- 6. Add updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_knowledge_base_chunks_updated_at BEFORE UPDATE
    ON knowledge_base_chunks FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
