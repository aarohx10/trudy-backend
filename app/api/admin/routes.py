"""
Admin API Routes
"""
from fastapi import APIRouter, Header, Depends, HTTPException
from typing import Optional, List
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import DatabaseAdminService
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin_role(current_user: dict = Depends(get_current_user)):
    """Require agency_admin role"""
    if current_user.get("role") != "agency_admin":
        raise ForbiddenError("Admin access required")
    return current_user


@router.get("/users/{user_id}/export")
async def export_user_data(
    user_id: str,
    current_user: dict = Depends(require_admin_role),
):
    """Export user data (admin only)"""
    db = DatabaseAdminService()
    
    # Get user by Clerk user ID (Clerk ONLY)
    user = db.select_one("users", {"clerk_user_id": user_id})
    if not user:
        raise NotFoundError("user", user_id)
    
    # Get all user-related data
    client_id = user.get("client_id")
    
    # Collect all user data
    export_data = {
        "user": user,
        "client": db.select_one("clients", {"id": client_id}) if client_id else None,
        "audit_logs": db.select("audit_logs", {"user_id": user_id}, order_by="created_at"),
        "created_at": datetime.utcnow().isoformat(),
    }
    
    return {
        "data": export_data,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: dict = Depends(require_admin_role),
):
    """Delete user (admin only)"""
    db = DatabaseAdminService()
    
    # Get user by Clerk user ID (Clerk ONLY)
    user = db.select_one("users", {"clerk_user_id": user_id})
    if not user:
        raise NotFoundError("user", user_id)
    
    # Log audit event
    from app.core.audit import log_audit_event
    await log_audit_event(
        action="DELETE",
        table_name="users",
        record_id=user_id,
        user_id=current_user["user_id"],
        client_id=current_user["client_id"],
        metadata={"deleted_user": user_id},
    )
    
    # Soft delete (mark as deleted rather than hard delete)
    db.update(
        "users",
        {"clerk_user_id": user_id},
        {
            "deleted_at": datetime.utcnow().isoformat(),
            "is_active": False,
        },
    )
    
    return {
        "data": {"user_id": user_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


# ============================================
# Subscription Tier Management
# ============================================

from app.models.schemas import (
    SubscriptionTierCreate,
    SubscriptionTierUpdate,
    SubscriptionTierResponse,
)


@router.get("/subscription-tiers", response_model=List[SubscriptionTierResponse])
async def list_subscription_tiers(
    current_user: dict = Depends(require_admin_role),
    is_active: Optional[bool] = None,
):
    """List all subscription tiers (admin only)"""
    db = DatabaseAdminService()
    
    filters = {}
    if is_active is not None:
        filters["is_active"] = is_active
    
    tiers = db.select("subscription_tiers", filters, order_by="display_order")
    
    return [SubscriptionTierResponse(**tier) for tier in tiers]


@router.get("/subscription-tiers/{tier_id}", response_model=SubscriptionTierResponse)
async def get_subscription_tier(
    tier_id: str,
    current_user: dict = Depends(require_admin_role),
):
    """Get subscription tier by ID (admin only)"""
    db = DatabaseAdminService()
    
    tier = db.select_one("subscription_tiers", {"id": tier_id})
    if not tier:
        raise NotFoundError("subscription_tier", tier_id)
    
    return SubscriptionTierResponse(**tier)


@router.post("/subscription-tiers", response_model=SubscriptionTierResponse)
async def create_subscription_tier(
    tier_data: SubscriptionTierCreate,
    current_user: dict = Depends(require_admin_role),
):
    """Create new subscription tier (admin only)"""
    db = DatabaseAdminService()
    
    # Check if name already exists
    existing = db.select_one("subscription_tiers", {"name": tier_data.name})
    if existing:
        raise ValidationError(f"Tier with name '{tier_data.name}' already exists")
    
    tier_record = {
        "name": tier_data.name,
        "display_name": tier_data.display_name,
        "description": tier_data.description,
        "price_usd": float(tier_data.price_usd),
        "price_cents": tier_data.price_cents,
        "minutes_allowance": tier_data.minutes_allowance,
        "initial_credits": tier_data.initial_credits,
        "stripe_price_id": tier_data.stripe_price_id,
        "stripe_product_id": tier_data.stripe_product_id,
        "is_active": tier_data.is_active,
        "display_order": tier_data.display_order,
        "features": tier_data.features or [],
    }
    
    created_tier = db.insert("subscription_tiers", tier_record)
    
    return SubscriptionTierResponse(**created_tier)


@router.put("/subscription-tiers/{tier_id}", response_model=SubscriptionTierResponse)
async def update_subscription_tier(
    tier_id: str,
    tier_data: SubscriptionTierUpdate,
    current_user: dict = Depends(require_admin_role),
):
    """Update subscription tier (admin only)"""
    db = DatabaseAdminService()
    
    tier = db.select_one("subscription_tiers", {"id": tier_id})
    if not tier:
        raise NotFoundError("subscription_tier", tier_id)
    
    update_data = tier_data.dict(exclude_unset=True)
    if "price_usd" in update_data:
        update_data["price_usd"] = float(update_data["price_usd"])
    
    db.update("subscription_tiers", {"id": tier_id}, update_data)
    
    updated_tier = db.select_one("subscription_tiers", {"id": tier_id})
    return SubscriptionTierResponse(**updated_tier)


@router.delete("/subscription-tiers/{tier_id}")
async def delete_subscription_tier(
    tier_id: str,
    current_user: dict = Depends(require_admin_role),
):
    """Delete subscription tier (admin only)"""
    db = DatabaseAdminService()
    
    tier = db.select_one("subscription_tiers", {"id": tier_id})
    if not tier:
        raise NotFoundError("subscription_tier", tier_id)
    
    # Check if any clients are using this tier
    clients_with_tier = db.select("clients", {"subscription_tier_id": tier_id})
    if clients_with_tier:
        raise ValidationError(
            f"Cannot delete tier: {len(clients_with_tier)} client(s) are using this tier"
        )
    
    db.delete("subscription_tiers", {"id": tier_id})
    
    return {
        "data": {"tier_id": tier_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


# ============================================
# Global Stats (Admin Dashboard)
# ============================================

@router.get("/stats")
async def get_global_stats(
    current_user: dict = Depends(require_admin_role),
):
    """Get global statistics (admin only)"""
    db = DatabaseAdminService()
    
    # Total revenue (sum of all Stripe payments)
    credit_transactions = db.select(
        "credit_transactions",
        {"type": "purchased", "reference_type": "stripe_payment"},
    )
    total_revenue_usd = sum(
        float(t.get("amount", 0)) for t in credit_transactions
    )  # Credits = USD (1:1)
    
    # Total minutes used (sum of all call durations)
    all_calls = db.select("calls", {})
    total_minutes_used = sum(
        (c.get("duration_seconds", 0) or 0) // 60 for c in all_calls
    )
    
    # Total clients
    total_clients = len(db.select("clients", {}))
    
    # Active subscriptions
    active_subscriptions = len(
        db.select("clients", {"subscription_tier_id": {"is_not": None}})
    )
    
    # Tier distribution
    tier_distribution = {}
    clients_with_tiers = db.select("clients", {"subscription_tier_id": {"is_not": None}})
    for client in clients_with_tiers:
        tier_id = client.get("subscription_tier_id")
        if tier_id:
            tier = db.select_one("subscription_tiers", {"id": tier_id})
            if tier:
                tier_name = tier.get("name", "unknown")
                tier_distribution[tier_name] = tier_distribution.get(tier_name, 0) + 1
    
    return {
        "data": {
            "total_revenue_usd": round(total_revenue_usd, 2),
            "total_minutes_used": total_minutes_used,
            "total_clients": total_clients,
            "active_subscriptions": active_subscriptions,
            "tier_distribution": tier_distribution,
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
