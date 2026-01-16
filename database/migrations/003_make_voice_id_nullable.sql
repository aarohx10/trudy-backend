-- Migration: Make voice_id nullable in agents table
-- This allows agents to be created without a voice

-- Drop the foreign key constraint first
ALTER TABLE agents 
    DROP CONSTRAINT IF EXISTS agents_voice_id_fkey;

-- Make voice_id nullable
ALTER TABLE agents 
    ALTER COLUMN voice_id DROP NOT NULL;

-- Re-add the foreign key constraint with ON DELETE SET NULL to handle voice deletions gracefully
ALTER TABLE agents 
    ADD CONSTRAINT agents_voice_id_fkey 
    FOREIGN KEY (voice_id) 
    REFERENCES voices(id) 
    ON DELETE SET NULL;