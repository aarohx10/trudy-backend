-- Migration: Fix existing agents with empty clerk_org_id
-- This migration identifies and handles agents that were created with empty clerk_org_id
-- Note: We cannot automatically determine which org these belong to, so we'll mark them for review

-- ============================================
-- Step 1: Identify agents with empty clerk_org_id
-- ============================================
DO $$
DECLARE
    empty_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO empty_count
    FROM agents
    WHERE clerk_org_id IS NULL OR clerk_org_id = '';
    
    IF empty_count > 0 THEN
        RAISE WARNING 'Found % agents with empty clerk_org_id. These need manual review.', empty_count;
        
        -- Log the agent IDs for manual review
        RAISE NOTICE 'Agent IDs with empty clerk_org_id:';
        FOR rec IN 
            SELECT id, name, created_at 
            FROM agents 
            WHERE clerk_org_id IS NULL OR clerk_org_id = ''
            ORDER BY created_at DESC
        LOOP
            RAISE NOTICE '  - Agent ID: %, Name: %, Created: %', rec.id, rec.name, rec.created_at;
        END LOOP;
    ELSE
        RAISE NOTICE 'No agents with empty clerk_org_id found.';
    END IF;
END $$;

-- ============================================
-- Step 2: Add constraint to prevent future empty clerk_org_id
-- ============================================
-- Add a check constraint to ensure clerk_org_id is never empty
ALTER TABLE agents 
DROP CONSTRAINT IF EXISTS agents_clerk_org_id_not_empty;

ALTER TABLE agents 
ADD CONSTRAINT agents_clerk_org_id_not_empty 
CHECK (clerk_org_id IS NOT NULL AND clerk_org_id != '');

-- ============================================
-- Step 3: Add index on clerk_org_id if it doesn't exist
-- ============================================
CREATE INDEX IF NOT EXISTS idx_agents_clerk_org_id ON agents(clerk_org_id);

-- ============================================
-- Notes
-- ============================================
-- 1. Existing agents with empty clerk_org_id need manual review
-- 2. The constraint prevents future agents from being created with empty clerk_org_id
-- 3. If you need to delete orphaned agents, run:
--    DELETE FROM agents WHERE clerk_org_id IS NULL OR clerk_org_id = '';
