-- Migration: Fix audit trigger function to handle organization-first tables
-- This migration updates the audit_trigger_func() to work with tables that no longer have client_id
-- For tables without client_id (agents, calls, voices, knowledge_bases, tools, contacts, contact_folders, campaigns, webhook_endpoints),
-- it will look up client_id from the clients table using clerk_org_id

CREATE OR REPLACE FUNCTION audit_trigger_func()
RETURNS TRIGGER AS $$
DECLARE
    v_diff JSONB;
    v_user_id TEXT;
    v_client_id UUID;
    v_clerk_org_id TEXT;
    v_table_has_client_id BOOLEAN;
BEGIN
    -- Fallback to "system" user when request.jwt.claims is not populated
    v_user_id := COALESCE(jwt_user_id(), 'system');

    -- Check if the table has a client_id column by checking information_schema
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' 
        AND table_name = TG_TABLE_NAME 
        AND column_name = 'client_id'
    ) INTO v_table_has_client_id;

    -- Get client_id based on whether table has the column
    IF v_table_has_client_id THEN
        -- Table has client_id column - use it (with JWT fallback)
        IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
            v_client_id := COALESCE(jwt_client_id(), (NEW).client_id);
        ELSIF TG_OP = 'DELETE' THEN
            v_client_id := COALESCE(jwt_client_id(), (OLD).client_id);
        END IF;
    ELSE
        -- Table doesn't have client_id - use clerk_org_id to look it up from clients table
        IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
            BEGIN
                v_clerk_org_id := (NEW).clerk_org_id;
            EXCEPTION
                WHEN undefined_column THEN
                    -- Table doesn't have clerk_org_id either - use NULL
                    v_clerk_org_id := NULL;
            END;
        ELSIF TG_OP = 'DELETE' THEN
            BEGIN
                v_clerk_org_id := (OLD).clerk_org_id;
            EXCEPTION
                WHEN undefined_column THEN
                    -- Table doesn't have clerk_org_id either - use NULL
                    v_clerk_org_id := NULL;
            END;
        END IF;
        
        -- Look up client_id from clients table using clerk_org_id
        IF v_clerk_org_id IS NOT NULL THEN
            SELECT id INTO v_client_id 
            FROM clients 
            WHERE clerk_organization_id = v_clerk_org_id 
            LIMIT 1;
            
            -- If client doesn't exist, create one (for organization-first approach)
            -- This ensures audit_log always has a valid client_id
            -- Note: We skip this if we're already auditing the clients table to prevent recursion
            IF v_client_id IS NULL AND TG_TABLE_NAME != 'clients' THEN
                BEGIN
                    -- Create a new client record for this organization
                    INSERT INTO clients (
                        id,
                        clerk_organization_id,
                        name,
                        email,
                        subscription_status,
                        credits_balance,
                        credits_ceiling,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        gen_random_uuid(),
                        v_clerk_org_id,
                        'Organization ' || SUBSTRING(v_clerk_org_id, 1, 8),
                        'org_' || SUBSTRING(v_clerk_org_id, 1, 8) || '@truedy.ai',
                        'active',
                        0,
                        10000,
                        NOW(),
                        NOW()
                    )
                    RETURNING id INTO v_client_id;
                EXCEPTION
                    WHEN unique_violation THEN
                        -- Client was created by another concurrent transaction - try lookup again
                        SELECT id INTO v_client_id 
                        FROM clients 
                        WHERE clerk_organization_id = v_clerk_org_id 
                        LIMIT 1;
                    WHEN OTHERS THEN
                        -- Other error creating client - log and continue (will use fallback)
                        RAISE WARNING 'Failed to create client for org %: %', v_clerk_org_id, SQLERRM;
                END;
            END IF;
        END IF;
        
        -- Final fallback to JWT client_id if everything failed
        v_client_id := COALESCE(v_client_id, jwt_client_id());
        
        -- Last resort: If still NULL, raise an error (shouldn't happen, but better than violating NOT NULL)
        IF v_client_id IS NULL THEN
            RAISE EXCEPTION 'Cannot determine client_id for audit log. clerk_org_id: %, table: %', v_clerk_org_id, TG_TABLE_NAME;
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        v_diff := jsonb_build_object(
            'before', row_to_json(OLD),
            'after', row_to_json(NEW)
        );
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('update', TG_TABLE_NAME, NEW.id, v_user_id, v_client_id, v_diff);
        RETURN NEW;
    ELSIF TG_OP = 'INSERT' THEN
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('create', TG_TABLE_NAME, NEW.id, v_user_id, v_client_id, jsonb_build_object('after', row_to_json(NEW)));
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('delete', TG_TABLE_NAME, OLD.id, v_user_id, v_client_id, jsonb_build_object('before', row_to_json(OLD)));
        RETURN OLD;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
