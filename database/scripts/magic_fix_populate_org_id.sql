-- MAGIC FIX: Automatically populate clerk_org_id from JWT if missing
-- This solves the issue where backend code hasn't been deployed yet.
-- The database will "self-heal" the missing data by extracting it from the authenticated user's token.

CREATE OR REPLACE FUNCTION populate_clerk_org_id_from_jwt()
RETURNS TRIGGER AS $$
DECLARE
    v_jwt_claims JSONB;
    v_org_id TEXT;
    v_user_id TEXT;
BEGIN
    -- If clerk_org_id is already set, do nothing
    IF NEW.clerk_org_id IS NOT NULL AND NEW.clerk_org_id != '' THEN
        RETURN NEW;
    END IF;

    -- Get JWT claims
    BEGIN
        v_jwt_claims := current_setting('request.jwt.claims', true)::jsonb;
    EXCEPTION WHEN OTHERS THEN
        v_jwt_claims := NULL;
    END;

    -- If no claims (e.g. service role or internal script), do nothing
    IF v_jwt_claims IS NULL THEN
        RETURN NEW;
    END IF;

    -- Try to extract org_id (Clerk Organization)
    v_org_id := v_jwt_claims->>'org_id';
    
    -- If org_id is missing, try _effective_org_id (custom claim)
    IF v_org_id IS NULL OR v_org_id = '' THEN
        v_org_id := v_jwt_claims->>'_effective_org_id';
    END IF;

    -- If still missing, fallback to sub (User ID - Personal Workspace)
    IF v_org_id IS NULL OR v_org_id = '' THEN
        v_user_id := v_jwt_claims->>'sub';
        IF v_user_id IS NOT NULL AND v_user_id != '' THEN
            v_org_id := v_user_id;
        END IF;
    END IF;

    -- If we found an ID, set it!
    IF v_org_id IS NOT NULL AND v_org_id != '' THEN
        NEW.clerk_org_id := v_org_id;
        RAISE NOTICE 'Auto-populated clerk_org_id from JWT: %', v_org_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if exists to ensure clean state
DROP TRIGGER IF EXISTS trg_populate_agents_clerk_org_id ON agents;

-- Create the trigger - MUST RUN BEFORE OTHER TRIGGERS
CREATE TRIGGER trg_populate_agents_clerk_org_id
    BEFORE INSERT ON agents
    FOR EACH ROW
    EXECUTE FUNCTION populate_clerk_org_id_from_jwt();

-- Also apply to other critical tables just in case
DROP TRIGGER IF EXISTS trg_populate_voices_clerk_org_id ON voices;
CREATE TRIGGER trg_populate_voices_clerk_org_id BEFORE INSERT ON voices FOR EACH ROW EXECUTE FUNCTION populate_clerk_org_id_from_jwt();

DROP TRIGGER IF EXISTS trg_populate_campaigns_clerk_org_id ON campaigns;
CREATE TRIGGER trg_populate_campaigns_clerk_org_id BEFORE INSERT ON campaigns FOR EACH ROW EXECUTE FUNCTION populate_clerk_org_id_from_jwt();

DROP TRIGGER IF EXISTS trg_populate_calls_clerk_org_id ON calls;
CREATE TRIGGER trg_populate_calls_clerk_org_id BEFORE INSERT ON calls FOR EACH ROW EXECUTE FUNCTION populate_clerk_org_id_from_jwt();
