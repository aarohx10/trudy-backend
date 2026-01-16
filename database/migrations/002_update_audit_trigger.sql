-- Update audit trigger to tolerate missing JWT claims during local dev
CREATE OR REPLACE FUNCTION audit_trigger_func()
RETURNS TRIGGER AS $$
DECLARE
    v_diff JSONB;
    v_user_id TEXT;
    v_client_id UUID;
BEGIN
    -- Fallback to "system" user when request.jwt.claims is not populated
    v_user_id := COALESCE(jwt_user_id(), 'system');

    IF TG_OP = 'UPDATE' THEN
        v_client_id := COALESCE(jwt_client_id(), NEW.client_id);
        v_diff := jsonb_build_object(
            'before', row_to_json(OLD),
            'after', row_to_json(NEW)
        );
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('update', TG_TABLE_NAME, NEW.id, v_user_id, v_client_id, v_diff);
        RETURN NEW;
    ELSIF TG_OP = 'INSERT' THEN
        v_client_id := COALESCE(jwt_client_id(), NEW.client_id);
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('create', TG_TABLE_NAME, NEW.id, v_user_id, v_client_id, jsonb_build_object('after', row_to_json(NEW)));
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        v_client_id := COALESCE(jwt_client_id(), OLD.client_id);
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('delete', TG_TABLE_NAME, OLD.id, v_user_id, v_client_id, jsonb_build_object('before', row_to_json(OLD)));
        RETURN OLD;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

