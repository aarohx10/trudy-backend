-- =============================================================================
-- WIPE ALL USER / TENANT DATA (Fresh start)
-- =============================================================================
-- Run this in Supabase SQL Editor when you want to reset the app so that
-- only new signups exist. This does NOT drop tables or change schema.
--
-- BEFORE RUNNING:
-- 1. Backup your database if you might need current data.
-- 2. Confirm you are connected to the correct project (e.g. dev).
-- 3. Optionally wipe Clerk users and organizations first (see FRESH_START_RESET_GUIDE.md).
--
-- TABLES NOT WIPED (kept on purpose):
--   subscription_tiers, agent_templates
-- =============================================================================

TRUNCATE TABLE
  webhook_deliveries,
  tool_logs,
  campaign_contacts,
  calls,
  campaigns,
  contacts,
  contact_folders,
  knowledge_bases,
  agents,
  tools,
  voices,
  webhook_endpoints,
  api_keys,
  idempotency_keys,
  application_logs,
  audit_log,
  credit_transactions,
  agent_assistance_messages,
  agent_assistance_sessions,
  phone_numbers,
  telephony_credentials,
  users,
  clients,
  admin_otps,
  webhook_logs
RESTART IDENTITY
CASCADE;

-- If your schema does not have one of these tables (e.g. agent_assistance_sessions,
-- admin_otps, webhook_logs), the TRUNCATE will fail for that table. Comment out
-- the missing table(s) in the list above and run again.
