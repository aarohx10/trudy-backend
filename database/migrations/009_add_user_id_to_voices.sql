-- Add user_id column to voices table
-- This tracks which user created the voice

ALTER TABLE voices 
ADD COLUMN user_id TEXT;

-- Add index for faster lookups
CREATE INDEX idx_voices_user_id ON voices(user_id);

-- Add comment
COMMENT ON COLUMN voices.user_id IS 'Clerk user ID of the user who created this voice';
