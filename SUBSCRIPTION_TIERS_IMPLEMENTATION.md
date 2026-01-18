# Subscription Tiers Implementation Guide

## Overview
This document outlines the implementation of the tiered subscription system with dynamic pricing stored in Supabase.

## Completed Components

### 1. Database Schema ✅
- **Migration**: `007_add_subscription_tiers.sql`
- Created `subscription_tiers` table with default tiers ($50, $100, $150)
- Added `subscription_tier_id`, `minutes_balance`, `minutes_allowance`, `renewal_date` to `clients` table
- Added helper functions: `reset_monthly_minutes()`, `get_client_tier()`

### 2. Pydantic Models ✅
- `SubscriptionTierCreate`, `SubscriptionTierUpdate`, `SubscriptionTierResponse` in `schemas.py`

### 3. Admin API Routes ✅
- `GET /api/v1/admin/subscription-tiers` - List all tiers
- `GET /api/v1/admin/subscription-tiers/{tier_id}` - Get tier by ID
- `POST /api/v1/admin/subscription-tiers` - Create new tier
- `PUT /api/v1/admin/subscription-tiers/{tier_id}` - Update tier
- `DELETE /api/v1/admin/subscription-tiers/{tier_id}` - Delete tier
- `GET /api/v1/admin/stats` - Global statistics

## Remaining Implementation Tasks

### 1. Update Stripe Payment Intent (Frontend)
**File**: `frontend/src/app/api/stripe/create-payment-intent/route.ts`

**Changes Needed**:
- Accept `tier_id` in request body instead of `amount`
- Fetch tier details from backend API: `GET /api/v1/admin/subscription-tiers/{tier_id}`
- Use `tier.price_cents` for Stripe payment intent
- Include `tier_id` in Stripe metadata

**Example**:
```typescript
const { tier_id, client_id } = await request.json()

// Fetch tier from backend
const tierResponse = await fetch(`${BACKEND_URL}/api/v1/admin/subscription-tiers/${tier_id}`)
const tier = await tierResponse.json()

// Create payment intent with tier price
const paymentIntent = await stripe.paymentIntents.create({
  amount: tier.data.price_cents,
  currency: 'usd',
  metadata: {
    client_id,
    tier_id,
    user_id: userId || '',
  },
})
```

### 2. Update Stripe Webhook (Backend)
**File**: `z-backend/app/api/v1/webhooks.py`

**Changes Needed**:
- In `payment_intent.succeeded` handler:
  - Extract `tier_id` from metadata
  - Fetch tier details from database
  - Assign tier to client: `subscription_tier_id = tier_id`
  - Grant `minutes_allowance` and `initial_credits` from tier
  - Set `renewal_date` to next month
  - Update `minutes_balance = minutes_allowance`
  - Update `credits_balance += initial_credits`

**Example**:
```python
if event_type == "payment_intent.succeeded":
    metadata = payment_intent.get("metadata", {})
    client_id = metadata.get("client_id")
    tier_id = metadata.get("tier_id")
    
    if tier_id:
        tier = db.select_one("subscription_tiers", {"id": tier_id})
        if tier:
            from datetime import datetime, timedelta
            next_month = datetime.utcnow() + timedelta(days=30)
            
            db.update("clients", {"id": client_id}, {
                "subscription_tier_id": tier_id,
                "minutes_balance": tier["minutes_allowance"],
                "minutes_allowance": tier["minutes_allowance"],
                "credits_balance": client.get("credits_balance", 0) + tier["initial_credits"],
                "renewal_date": next_month.isoformat(),
                "subscription_started_at": datetime.utcnow().isoformat(),
            })
```

### 3. Update Call Deduction Logic
**File**: `z-backend/app/services/webhook_handlers.py`

**Changes Needed**:
- In `handle_call_ended`, deduct from `minutes_balance` instead of `credits_balance`
- Only deduct if call is not a test call
- Check if client has sufficient minutes before allowing call

**Example**:
```python
# Before creating call, check minutes
if not call.get("context", {}).get("is_test"):
    client = db.get_client(client_id)
    minutes_needed = max(1, (duration + 59) // 60)
    if client.get("minutes_balance", 0) < minutes_needed:
        raise ValidationError("Insufficient minutes balance")
    
    # Deduct minutes
    db.update("clients", {"id": client_id}, {
        "minutes_balance": client.get("minutes_balance", 0) - minutes_needed
    })
```

### 4. Update Credit Deduction Logic
**Files**: `z-backend/app/api/v1/knowledge_bases.py`, `z-backend/app/api/v1/voices.py`

**Changes Needed**:
- For premium features (KB indexing, voice training), deduct from `credits_balance`
- Keep existing credit deduction logic for these operations

### 5. Create Admin Frontend UI
**Location**: `frontend/src/app/(admin)/pricing/page.tsx`

**Features Needed**:
- Table showing all subscription tiers
- Edit buttons for each tier (price, minutes, credits)
- Global stats view (total revenue, total minutes used)
- Form to create new tiers

### 6. Public Tier Listing Endpoint
**File**: `z-backend/app/api/v1/subscription_tiers.py` (new file)

**Purpose**: Allow frontend to fetch active tiers for display in pricing page

**Endpoint**: `GET /api/v1/subscription-tiers` (public, no auth required)

### 7. Monthly Minutes Reset Job
**Implementation**: Scheduled job or cron task

**Purpose**: Call `reset_monthly_minutes()` function monthly to reset client minutes

**Options**:
- Supabase Edge Function (scheduled)
- External cron service
- Backend scheduled task

## Testing Checklist

- [ ] Create payment intent with tier_id
- [ ] Verify Stripe webhook assigns tier correctly
- [ ] Verify minutes are granted on subscription
- [ ] Verify credits are granted on subscription
- [ ] Test call deduction from minutes_balance
- [ ] Test credit deduction for premium features
- [ ] Test admin tier CRUD operations
- [ ] Test monthly minutes reset

## Migration Steps

1. Run SQL migration: `007_add_subscription_tiers.sql`
2. Deploy backend changes
3. Update frontend Stripe integration
4. Test end-to-end subscription flow
5. Deploy admin UI
6. Set up monthly reset job
