-- Migration: Add Subscription Tiers & Entitlement Tracking
-- Purpose: Enable dynamic pricing and separate minutes (recurring) from credits (one-time)

-- 1. Create subscription_tiers table
CREATE TABLE IF NOT EXISTS subscription_tiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE, -- e.g., "Starter", "Professional", "Enterprise"
    display_name TEXT NOT NULL, -- e.g., "Starter Plan"
    description TEXT,
    price_usd DECIMAL(10, 2) NOT NULL, -- Monthly price in USD
    price_cents INTEGER NOT NULL, -- Price in cents for Stripe
    minutes_allowance INTEGER NOT NULL, -- Monthly minutes included
    initial_credits INTEGER NOT NULL DEFAULT 0, -- One-time credits granted on subscription
    stripe_price_id TEXT, -- Stripe Price ID for recurring subscriptions (optional)
    stripe_product_id TEXT, -- Stripe Product ID (optional)
    is_active BOOLEAN DEFAULT true NOT NULL,
    display_order INTEGER DEFAULT 0, -- For sorting in UI
    features JSONB DEFAULT '[]'::jsonb, -- Array of feature strings
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- 2. Insert default tiers ($50, $100, $150)
INSERT INTO subscription_tiers (name, display_name, description, price_usd, price_cents, minutes_allowance, initial_credits, display_order, features) VALUES
    ('starter', 'Starter Plan', 'Perfect for small teams getting started', 50.00, 5000, 500, 50, 1, '["500 minutes/month", "50 credits", "Basic support"]'::jsonb),
    ('professional', 'Professional Plan', 'For growing businesses', 100.00, 10000, 1200, 100, 2, '["1,200 minutes/month", "100 credits", "Priority support", "Advanced analytics"]'::jsonb),
    ('enterprise', 'Enterprise Plan', 'For large organizations', 150.00, 15000, 2000, 200, 3, '["2,000 minutes/month", "200 credits", "24/7 support", "Custom integrations", "Dedicated account manager"]'::jsonb)
ON CONFLICT (name) DO NOTHING;

-- 3. Add subscription fields to clients table
ALTER TABLE clients ADD COLUMN IF NOT EXISTS subscription_tier_id UUID REFERENCES subscription_tiers(id) ON DELETE SET NULL;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS minutes_balance INTEGER DEFAULT 0 NOT NULL;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS minutes_allowance INTEGER DEFAULT 0 NOT NULL; -- Monthly allowance from tier
ALTER TABLE clients ADD COLUMN IF NOT EXISTS renewal_date TIMESTAMPTZ; -- Next renewal date for monthly reset
ALTER TABLE clients ADD COLUMN IF NOT EXISTS subscription_started_at TIMESTAMPTZ; -- When subscription started

-- 4. Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS clients_subscription_tier_id_idx ON clients(subscription_tier_id);
CREATE INDEX IF NOT EXISTS subscription_tiers_is_active_idx ON subscription_tiers(is_active);
CREATE INDEX IF NOT EXISTS subscription_tiers_display_order_idx ON subscription_tiers(display_order);

-- 5. Add RLS policies for subscription_tiers (read-only for authenticated users)
ALTER TABLE subscription_tiers ENABLE ROW LEVEL SECURITY;

-- Policy: Anyone authenticated can view active tiers
CREATE POLICY "Authenticated users can view active subscription tiers" ON subscription_tiers
  FOR SELECT
  TO authenticated
  USING (is_active = true);

-- Policy: Service role can do everything (for admin operations)
CREATE POLICY "Service role full access to subscription_tiers" ON subscription_tiers
  FOR ALL
  USING (auth.role() = 'service_role');

-- 6. Add function to reset monthly minutes (to be called by scheduled job)
CREATE OR REPLACE FUNCTION reset_monthly_minutes()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  -- Reset minutes_balance to minutes_allowance for clients whose renewal_date has passed
  UPDATE clients
  SET 
    minutes_balance = minutes_allowance,
    renewal_date = renewal_date + INTERVAL '1 month'
  WHERE 
    subscription_tier_id IS NOT NULL
    AND renewal_date IS NOT NULL
    AND renewal_date <= now();
END;
$$;

-- 7. Add function to get tier details for a client
CREATE OR REPLACE FUNCTION get_client_tier(client_uuid UUID)
RETURNS TABLE (
    tier_id UUID,
    tier_name TEXT,
    display_name TEXT,
    price_usd DECIMAL,
    minutes_allowance INTEGER,
    initial_credits INTEGER
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        st.id,
        st.name,
        st.display_name,
        st.price_usd,
        st.minutes_allowance,
        st.initial_credits
    FROM clients c
    JOIN subscription_tiers st ON c.subscription_tier_id = st.id
    WHERE c.id = client_uuid;
END;
$$;
