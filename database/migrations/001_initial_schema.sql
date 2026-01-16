-- Trudy Backend Database Schema
-- Supabase Postgres with Row Level Security (RLS)

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable RLS on all tables
ALTER DATABASE postgres SET row_security = on;

-- ============================================
-- Core Tables
-- ============================================

-- Clients (tenants)
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    subscription_status TEXT NOT NULL DEFAULT 'active' CHECK (subscription_status IN ('active', 'suspended', 'cancelled')),
    credits_balance INTEGER NOT NULL DEFAULT 0 CHECK (credits_balance >= 0),
    credits_ceiling INTEGER NOT NULL DEFAULT 10000,
    stripe_customer_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Users (linked to Auth0)
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth0_sub TEXT NOT NULL UNIQUE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('agency_admin', 'client_admin', 'client_user')),
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- API Keys (encrypted storage)
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    service TEXT NOT NULL CHECK (service IN ('ultravox', 'stripe', 'telnyx', 'google_cloud', 'aws', 'azure', 'openai', 'elevenlabs')),
    key_name TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    settings JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE(client_id, service, key_name)
);

-- Voices
CREATE TABLE voices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'elevenlabs',
    provider_voice_id TEXT,
    type TEXT NOT NULL CHECK (type IN ('custom', 'reference')),
    language TEXT NOT NULL DEFAULT 'en-US',
    status TEXT NOT NULL DEFAULT 'training' CHECK (status IN ('training', 'active', 'failed')),
    training_info JSONB DEFAULT '{}'::jsonb,
    ultravox_voice_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Knowledge Bases (Corpora)
CREATE TABLE knowledge_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    language TEXT NOT NULL DEFAULT 'en-US',
    ultravox_corpus_id TEXT,
    status TEXT NOT NULL DEFAULT 'creating' CHECK (status IN ('creating', 'ready', 'processing', 'failed')),
    settings JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Knowledge Base Documents (files)
CREATE TABLE knowledge_base_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    s3_key TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_upload' CHECK (status IN ('pending_upload', 'uploaded', 'processing', 'indexed', 'failed')),
    ultravox_source_id TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Tools
CREATE TABLE tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    ultravox_tool_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('GET', 'POST', 'PUT', 'DELETE')),
    authentication JSONB DEFAULT '{}'::jsonb,
    parameters JSONB DEFAULT '{}'::jsonb,
    response_schema JSONB DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'creating' CHECK (status IN ('creating', 'active', 'failed')),
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Agents
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    ultravox_agent_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    voice_id UUID NOT NULL REFERENCES voices(id) ON DELETE RESTRICT,
    system_prompt TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'fixie-ai/ultravox-v0_4-8k',
    tools JSONB DEFAULT '[]'::jsonb,
    knowledge_bases JSONB DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'creating' CHECK (status IN ('creating', 'active', 'failed')),
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Calls
CREATE TABLE calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    ultravox_call_id TEXT,
    phone_number TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'ringing', 'in_progress', 'completed', 'failed')),
    context JSONB DEFAULT '{}'::jsonb,
    call_settings JSONB DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    cost_usd DECIMAL(10, 4),
    recording_url TEXT,
    transcript JSONB,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Campaigns
CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    schedule_type TEXT NOT NULL CHECK (schedule_type IN ('immediate', 'scheduled')),
    scheduled_at TIMESTAMPTZ,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    max_concurrent_calls INTEGER NOT NULL DEFAULT 10 CHECK (max_concurrent_calls > 0 AND max_concurrent_calls <= 100),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'scheduled', 'active', 'completed', 'failed')),
    ultravox_batch_ids JSONB DEFAULT '[]'::jsonb,
    stats JSONB DEFAULT '{"pending": 0, "calling": 0, "completed": 0, "failed": 0}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Campaign Contacts
CREATE TABLE campaign_contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    phone_number TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    custom_fields JSONB DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'calling', 'completed', 'failed')),
    call_id UUID REFERENCES calls(id),
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE(campaign_id, phone_number)
);

-- Credit Transactions
CREATE TABLE credit_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('purchased', 'spent', 'refunded')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    reference_type TEXT CHECK (reference_type IN ('voice_training', 'call', 'stripe_payment', 'manual_adjustment')),
    reference_id UUID,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Webhook Endpoints (egress)
CREATE TABLE webhook_endpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    event_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    secret TEXT NOT NULL,
    enabled BOOLEAN DEFAULT true,
    retry_config JSONB DEFAULT '{"max_attempts": 10, "backoff_strategy": "exponential"}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Webhook Deliveries (tracking)
CREATE TABLE webhook_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_endpoint_id UUID NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed', 'failed_permanently')),
    attempt INTEGER NOT NULL DEFAULT 1,
    response_code INTEGER,
    response_body TEXT,
    error_message TEXT,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Idempotency Keys
CREATE TABLE idempotency_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_body JSONB NOT NULL,
    status_code INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    ttl_at TIMESTAMPTZ NOT NULL,
    UNIQUE(client_id, key, request_hash)
);

-- Audit Log
CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
    table_name TEXT NOT NULL,
    record_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    diff JSONB,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================
-- Indexes
-- ============================================

CREATE INDEX idx_users_auth0_sub ON users(auth0_sub);
CREATE INDEX idx_users_client_id ON users(client_id);
CREATE INDEX idx_voices_client_id ON voices(client_id);
CREATE INDEX idx_voices_status ON voices(status);
CREATE INDEX idx_agents_client_id ON agents(client_id);
CREATE INDEX idx_agents_voice_id ON agents(voice_id);
CREATE INDEX idx_calls_client_id ON calls(client_id);
CREATE INDEX idx_calls_agent_id ON calls(agent_id);
CREATE INDEX idx_calls_ultravox_call_id ON calls(ultravox_call_id);
CREATE INDEX idx_campaigns_client_id ON campaigns(client_id);
CREATE INDEX idx_campaign_contacts_campaign_id ON campaign_contacts(campaign_id);
CREATE INDEX idx_campaign_contacts_status ON campaign_contacts(status);
CREATE INDEX idx_credit_transactions_client_id ON credit_transactions(client_id);
CREATE INDEX idx_credit_transactions_created_at ON credit_transactions(created_at);
CREATE INDEX idx_webhook_endpoints_client_id ON webhook_endpoints(client_id);
CREATE INDEX idx_webhook_deliveries_endpoint_id ON webhook_deliveries(webhook_endpoint_id);
CREATE INDEX idx_idempotency_lookup ON idempotency_keys(client_id, key, request_hash);
CREATE INDEX idx_idempotency_ttl ON idempotency_keys(ttl_at);
CREATE INDEX idx_audit_log_client_id ON audit_log(client_id);
CREATE INDEX idx_audit_log_table_record ON audit_log(table_name, record_id);
CREATE INDEX idx_audit_log_created_at ON audit_log(created_at);

-- ============================================
-- Row Level Security (RLS) Policies
-- ============================================

-- Enable RLS on all tables
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE voices ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE tools ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_endpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- Helper function to get client_id from JWT
CREATE OR REPLACE FUNCTION jwt_client_id() RETURNS UUID AS $$
BEGIN
    RETURN (current_setting('request.jwt.claims', true)::json->>'client_id')::UUID;
END;
$$ LANGUAGE plpgsql STABLE;

-- Helper function to get role from JWT
CREATE OR REPLACE FUNCTION jwt_role() RETURNS TEXT AS $$
BEGIN
    RETURN current_setting('request.jwt.claims', true)::json->>'role';
END;
$$ LANGUAGE plpgsql STABLE;

-- Helper function to get user_id from JWT
CREATE OR REPLACE FUNCTION jwt_user_id() RETURNS TEXT AS $$
BEGIN
    RETURN current_setting('request.jwt.claims', true)::json->>'sub';
END;
$$ LANGUAGE plpgsql STABLE;

-- RLS Policies: Clients
CREATE POLICY clients_select ON clients
    FOR SELECT
    USING (
        jwt_role() = 'agency_admin' OR
        id = jwt_client_id()
    );

CREATE POLICY clients_insert ON clients
    FOR INSERT
    WITH CHECK (jwt_role() = 'agency_admin');

CREATE POLICY clients_update ON clients
    FOR UPDATE
    USING (
        jwt_role() = 'agency_admin' OR
        id = jwt_client_id()
    );

-- RLS Policies: Users
CREATE POLICY users_select ON users
    FOR SELECT
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY users_insert ON users
    FOR INSERT
    WITH CHECK (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

-- RLS Policies: All other tables (similar pattern)
CREATE POLICY api_keys_policy ON api_keys
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY voices_policy ON voices
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY knowledge_documents_policy ON knowledge_documents
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY knowledge_base_documents_policy ON knowledge_base_documents
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY tools_policy ON tools
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY agents_policy ON agents
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY calls_policy ON calls
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY campaigns_policy ON campaigns
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY campaign_contacts_policy ON campaign_contacts
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        campaign_id IN (SELECT id FROM campaigns WHERE client_id = jwt_client_id())
    );

CREATE POLICY credit_transactions_policy ON credit_transactions
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY webhook_endpoints_policy ON webhook_endpoints
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY webhook_deliveries_policy ON webhook_deliveries
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        webhook_endpoint_id IN (SELECT id FROM webhook_endpoints WHERE client_id = jwt_client_id())
    );

CREATE POLICY idempotency_keys_policy ON idempotency_keys
    FOR ALL
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

CREATE POLICY audit_log_policy ON audit_log
    FOR SELECT
    USING (
        jwt_role() = 'agency_admin' OR
        client_id = jwt_client_id()
    );

-- ============================================
-- Triggers for updated_at
-- ============================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_clients_updated_at BEFORE UPDATE ON clients FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_api_keys_updated_at BEFORE UPDATE ON api_keys FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_voices_updated_at BEFORE UPDATE ON voices FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_knowledge_documents_updated_at BEFORE UPDATE ON knowledge_documents FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_knowledge_base_documents_updated_at BEFORE UPDATE ON knowledge_base_documents FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_tools_updated_at BEFORE UPDATE ON tools FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_agents_updated_at BEFORE UPDATE ON agents FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_calls_updated_at BEFORE UPDATE ON calls FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_campaigns_updated_at BEFORE UPDATE ON campaigns FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_campaign_contacts_updated_at BEFORE UPDATE ON campaign_contacts FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_webhook_endpoints_updated_at BEFORE UPDATE ON webhook_endpoints FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- Audit Log Trigger
-- ============================================

CREATE OR REPLACE FUNCTION audit_trigger_func()
RETURNS TRIGGER AS $$
DECLARE
    v_diff JSONB;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        v_diff := jsonb_build_object(
            'before', row_to_json(OLD),
            'after', row_to_json(NEW)
        );
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('update', TG_TABLE_NAME, NEW.id, jwt_user_id(), jwt_client_id(), v_diff);
        RETURN NEW;
    ELSIF TG_OP = 'INSERT' THEN
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('create', TG_TABLE_NAME, NEW.id, jwt_user_id(), jwt_client_id(), jsonb_build_object('after', row_to_json(NEW)));
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO audit_log (action, table_name, record_id, user_id, client_id, diff)
        VALUES ('delete', TG_TABLE_NAME, OLD.id, jwt_user_id(), jwt_client_id(), jsonb_build_object('before', row_to_json(OLD)));
        RETURN OLD;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Apply audit triggers to sensitive tables
CREATE TRIGGER audit_agents AFTER INSERT OR UPDATE OR DELETE ON agents FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();
CREATE TRIGGER audit_voices AFTER INSERT OR UPDATE OR DELETE ON voices FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();
CREATE TRIGGER audit_campaigns AFTER INSERT OR UPDATE OR DELETE ON campaigns FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();
CREATE TRIGGER audit_calls AFTER INSERT OR UPDATE OR DELETE ON calls FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();
CREATE TRIGGER audit_webhook_endpoints AFTER INSERT OR UPDATE OR DELETE ON webhook_endpoints FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();
CREATE TRIGGER audit_api_keys AFTER INSERT OR UPDATE OR DELETE ON api_keys FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();
CREATE TRIGGER audit_credit_transactions AFTER INSERT OR UPDATE OR DELETE ON credit_transactions FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

