-- Migration: Add clerk_org_id to webhook_endpoints table
-- This migration adds clerk_org_id column to webhook_endpoints for organization scoping

-- Add clerk_org_id to webhook_endpoints table
ALTER TABLE webhook_endpoints 
ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_clerk_org_id ON webhook_endpoints(clerk_org_id);

-- ============================================
-- Notes
-- ============================================
-- 1. This enables organization-first data access for webhook endpoints
-- 2. All queries should filter by clerk_org_id instead of client_id
-- 3. Webhook endpoints are now shared across the organization
