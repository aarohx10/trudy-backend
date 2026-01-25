-- Migration: Disable RLS and Fix Constraints for Development
-- Purpose: Remove all RLS restrictions and fix status constraints to allow faster development
-- WARNING: This removes all security restrictions. Use only for development!

-- ============================================
-- 1. Fix Agents Status Constraint
-- ============================================
-- Drop the old constraint
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_status_check;

-- Add new constraint that includes 'draft'
ALTER TABLE agents ADD CONSTRAINT agents_status_check 
    CHECK (status IN ('creating', 'active', 'failed', 'draft'));

-- ============================================
-- 2. Disable RLS on ALL Tables
-- ============================================
ALTER TABLE clients DISABLE ROW LEVEL SECURITY;
ALTER TABLE users DISABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys DISABLE ROW LEVEL SECURITY;
ALTER TABLE voices DISABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_documents DISABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_bases DISABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base_documents DISABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base_chunks DISABLE ROW LEVEL SECURITY;
ALTER TABLE tools DISABLE ROW LEVEL SECURITY;
ALTER TABLE agents DISABLE ROW LEVEL SECURITY;
ALTER TABLE calls DISABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns DISABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_contacts DISABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions DISABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_endpoints DISABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries DISABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_logs DISABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys DISABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY;
ALTER TABLE application_logs DISABLE ROW LEVEL SECURITY;
ALTER TABLE tool_logs DISABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_tiers DISABLE ROW LEVEL SECURITY;
ALTER TABLE agent_templates DISABLE ROW LEVEL SECURITY;

-- ============================================
-- 3. Drop ALL RLS Policies
-- ============================================
-- Drop policies for clients
DROP POLICY IF EXISTS clients_select_policy ON clients;
DROP POLICY IF EXISTS clients_insert_policy ON clients;
DROP POLICY IF EXISTS clients_update_policy ON clients;
DROP POLICY IF EXISTS clients_delete_policy ON clients;

-- Drop policies for users
DROP POLICY IF EXISTS users_select_policy ON users;
DROP POLICY IF EXISTS users_insert_policy ON users;
DROP POLICY IF EXISTS users_update_policy ON users;
DROP POLICY IF EXISTS users_delete_policy ON users;

-- Drop policies for api_keys
DROP POLICY IF EXISTS api_keys_select_policy ON api_keys;
DROP POLICY IF EXISTS api_keys_insert_policy ON api_keys;
DROP POLICY IF EXISTS api_keys_update_policy ON api_keys;
DROP POLICY IF EXISTS api_keys_delete_policy ON api_keys;

-- Drop policies for voices
DROP POLICY IF EXISTS voices_select_policy ON voices;
DROP POLICY IF EXISTS voices_insert_policy ON voices;
DROP POLICY IF EXISTS voices_update_policy ON voices;
DROP POLICY IF EXISTS voices_delete_policy ON voices;

-- Drop policies for knowledge_documents
DROP POLICY IF EXISTS knowledge_documents_select_policy ON knowledge_documents;
DROP POLICY IF EXISTS knowledge_documents_insert_policy ON knowledge_documents;
DROP POLICY IF EXISTS knowledge_documents_update_policy ON knowledge_documents;
DROP POLICY IF EXISTS knowledge_documents_delete_policy ON knowledge_documents;

-- Drop policies for knowledge_bases
DROP POLICY IF EXISTS knowledge_bases_select_policy ON knowledge_bases;
DROP POLICY IF EXISTS knowledge_bases_insert_policy ON knowledge_bases;
DROP POLICY IF EXISTS knowledge_bases_update_policy ON knowledge_bases;
DROP POLICY IF EXISTS knowledge_bases_delete_policy ON knowledge_bases;

-- Drop policies for knowledge_base_documents
DROP POLICY IF EXISTS knowledge_base_documents_select_policy ON knowledge_base_documents;
DROP POLICY IF EXISTS knowledge_base_documents_insert_policy ON knowledge_base_documents;
DROP POLICY IF EXISTS knowledge_base_documents_update_policy ON knowledge_base_documents;
DROP POLICY IF EXISTS knowledge_base_documents_delete_policy ON knowledge_base_documents;

-- Drop policies for knowledge_base_chunks
DROP POLICY IF EXISTS knowledge_base_chunks_select_policy ON knowledge_base_chunks;
DROP POLICY IF EXISTS knowledge_base_chunks_insert_policy ON knowledge_base_chunks;
DROP POLICY IF EXISTS knowledge_base_chunks_update_policy ON knowledge_base_chunks;
DROP POLICY IF EXISTS knowledge_base_chunks_delete_policy ON knowledge_base_chunks;

-- Drop policies for tools
DROP POLICY IF EXISTS tools_select_policy ON tools;
DROP POLICY IF EXISTS tools_insert_policy ON tools;
DROP POLICY IF EXISTS tools_update_policy ON tools;
DROP POLICY IF EXISTS tools_delete_policy ON tools;

-- Drop policies for agents
DROP POLICY IF EXISTS agents_select_policy ON agents;
DROP POLICY IF EXISTS agents_insert_policy ON agents;
DROP POLICY IF EXISTS agents_update_policy ON agents;
DROP POLICY IF EXISTS agents_delete_policy ON agents;

-- Drop policies for calls
DROP POLICY IF EXISTS calls_select_policy ON calls;
DROP POLICY IF EXISTS calls_insert_policy ON calls;
DROP POLICY IF EXISTS calls_update_policy ON calls;
DROP POLICY IF EXISTS calls_delete_policy ON calls;

-- Drop policies for campaigns
DROP POLICY IF EXISTS campaigns_select_policy ON campaigns;
DROP POLICY IF EXISTS campaigns_insert_policy ON campaigns;
DROP POLICY IF EXISTS campaigns_update_policy ON campaigns;
DROP POLICY IF EXISTS campaigns_delete_policy ON campaigns;

-- Drop policies for campaign_contacts
DROP POLICY IF EXISTS campaign_contacts_select_policy ON campaign_contacts;
DROP POLICY IF EXISTS campaign_contacts_insert_policy ON campaign_contacts;
DROP POLICY IF EXISTS campaign_contacts_update_policy ON campaign_contacts;
DROP POLICY IF EXISTS campaign_contacts_delete_policy ON campaign_contacts;

-- Drop policies for credit_transactions
DROP POLICY IF EXISTS credit_transactions_select_policy ON credit_transactions;
DROP POLICY IF EXISTS credit_transactions_insert_policy ON credit_transactions;
DROP POLICY IF EXISTS credit_transactions_update_policy ON credit_transactions;
DROP POLICY IF EXISTS credit_transactions_delete_policy ON credit_transactions;

-- Drop policies for webhook_endpoints
DROP POLICY IF EXISTS webhook_endpoints_select_policy ON webhook_endpoints;
DROP POLICY IF EXISTS webhook_endpoints_insert_policy ON webhook_endpoints;
DROP POLICY IF EXISTS webhook_endpoints_update_policy ON webhook_endpoints;
DROP POLICY IF EXISTS webhook_endpoints_delete_policy ON webhook_endpoints;

-- Drop policies for webhook_deliveries
DROP POLICY IF EXISTS webhook_deliveries_select_policy ON webhook_deliveries;
DROP POLICY IF EXISTS webhook_deliveries_insert_policy ON webhook_deliveries;
DROP POLICY IF EXISTS webhook_deliveries_update_policy ON webhook_deliveries;
DROP POLICY IF EXISTS webhook_deliveries_delete_policy ON webhook_deliveries;

-- Drop policies for webhook_logs
DROP POLICY IF EXISTS webhook_logs_select_policy ON webhook_logs;
DROP POLICY IF EXISTS webhook_logs_insert_policy ON webhook_logs;
DROP POLICY IF EXISTS webhook_logs_update_policy ON webhook_logs;
DROP POLICY IF EXISTS webhook_logs_delete_policy ON webhook_logs;

-- Drop policies for idempotency_keys
DROP POLICY IF EXISTS idempotency_keys_select_policy ON idempotency_keys;
DROP POLICY IF EXISTS idempotency_keys_insert_policy ON idempotency_keys;
DROP POLICY IF EXISTS idempotency_keys_update_policy ON idempotency_keys;
DROP POLICY IF EXISTS idempotency_keys_delete_policy ON idempotency_keys;

-- Drop policies for audit_log
DROP POLICY IF EXISTS audit_log_select_policy ON audit_log;
DROP POLICY IF EXISTS audit_log_insert_policy ON audit_log;
DROP POLICY IF EXISTS audit_log_update_policy ON audit_log;
DROP POLICY IF EXISTS audit_log_delete_policy ON audit_log;

-- Drop policies for application_logs
DROP POLICY IF EXISTS application_logs_select_policy ON application_logs;
DROP POLICY IF EXISTS application_logs_insert_policy ON application_logs;
DROP POLICY IF EXISTS application_logs_update_policy ON application_logs;
DROP POLICY IF EXISTS application_logs_delete_policy ON application_logs;

-- Drop policies for tool_logs
DROP POLICY IF EXISTS tool_logs_select_policy ON tool_logs;
DROP POLICY IF EXISTS tool_logs_insert_policy ON tool_logs;
DROP POLICY IF EXISTS tool_logs_update_policy ON tool_logs;
DROP POLICY IF EXISTS tool_logs_delete_policy ON tool_logs;

-- Drop policies for subscription_tiers
DROP POLICY IF EXISTS subscription_tiers_select_policy ON subscription_tiers;
DROP POLICY IF EXISTS subscription_tiers_insert_policy ON subscription_tiers;
DROP POLICY IF EXISTS subscription_tiers_update_policy ON subscription_tiers;
DROP POLICY IF EXISTS subscription_tiers_delete_policy ON subscription_tiers;

-- Drop policies for agent_templates (if any exist)
DROP POLICY IF EXISTS agent_templates_select_policy ON agent_templates;
DROP POLICY IF EXISTS agent_templates_insert_policy ON agent_templates;
DROP POLICY IF EXISTS agent_templates_update_policy ON agent_templates;
DROP POLICY IF EXISTS agent_templates_delete_policy ON agent_templates;

-- Drop any other policies that might exist (catch-all)
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT schemaname, tablename, policyname 
              FROM pg_policies 
              WHERE schemaname = 'public') LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', 
                       r.policyname, r.schemaname, r.tablename);
    END LOOP;
END $$;
