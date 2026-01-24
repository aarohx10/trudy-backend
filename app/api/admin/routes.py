"""
Admin API Routes
"""
from fastapi import APIRouter, Header, Depends, HTTPException, Query
from typing import Optional, List
from datetime import datetime, timedelta
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
    
    # Total revenue (removed - no payment processing)
    total_revenue_usd = 0.0
    
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


# ============================================
# Application Logs (Admin)
# ============================================

@router.get("/logs")
async def get_logs(
    current_user: dict = Depends(require_admin_role),
    source: Optional[str] = Query(None, description="Filter by source: 'frontend' or 'backend'"),
    level: Optional[str] = Query(None, description="Filter by level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'"),
    category: Optional[str] = Query(None, description="Filter by category"),
    client_id: Optional[str] = Query(None, description="Filter by client_id"),
    request_id: Optional[str] = Query(None, description="Filter by request_id"),
    endpoint: Optional[str] = Query(None, description="Filter by endpoint"),
    start_date: Optional[datetime] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="End date (ISO format)"),
    search: Optional[str] = Query(None, description="Search in message text"),
    limit: int = Query(100, ge=1, le=1000, description="Number of logs to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Get application logs with filtering and pagination (admin only)
    """
    db = DatabaseAdminService()
    
    # Build query
    query = db.client.table("application_logs").select("*", count="exact")
    
    # Apply filters
    if source:
        query = query.eq("source", source)
    if level:
        query = query.eq("level", level)
    if category:
        query = query.eq("category", category)
    if client_id:
        query = query.eq("client_id", client_id)
    if request_id:
        query = query.eq("request_id", request_id)
    if endpoint:
        query = query.ilike("endpoint", f"%{endpoint}%")
    if start_date:
        query = query.gte("created_at", start_date.isoformat())
    if end_date:
        query = query.lte("created_at", end_date.isoformat())
    if search:
        query = query.ilike("message", f"%{search}%")
    
    # Order by created_at descending (newest first)
    query = query.order("created_at", desc=True)
    
    # Apply pagination
    query = query.range(offset, offset + limit - 1)
    
    # Execute query
    response = query.execute()
    
    logs = response.data if response.data else []
    total_count = response.count if response.count else 0
    
    return {
        "data": logs,
        "meta": {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(logs) < total_count,
        },
    }


@router.get("/logs/statistics")
async def get_log_statistics(
    current_user: dict = Depends(require_admin_role),
    start_date: Optional[datetime] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="End date (ISO format)"),
):
    """
    Get log statistics (admin only)
    """
    db = DatabaseAdminService()
    
    # Default to last 24 hours if not specified
    if not start_date:
        start_date = datetime.utcnow() - timedelta(days=1)
    if not end_date:
        end_date = datetime.utcnow()
    
    # Call the database function
    stats_response = db.client.rpc(
        "get_log_statistics",
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
    ).execute()
    
    stats = stats_response.data[0] if stats_response.data else {}
    
    return {
        "data": stats,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/logs/{log_id}")
async def get_log_detail(
    log_id: str,
    current_user: dict = Depends(require_admin_role),
):
    """
    Get detailed log entry by ID (admin only)
    """
    db = DatabaseAdminService()
    
    log_entry = db.select_one("application_logs", {"id": log_id})
    if not log_entry:
        raise NotFoundError("log", log_id)
    
    # Get related logs (same request_id)
    related_logs = []
    if log_entry.get("request_id"):
        related_logs = db.select(
            "application_logs",
            {"request_id": log_entry["request_id"]},
            order_by="created_at"
        )
        # Exclude the current log
        related_logs = [log for log in related_logs if log["id"] != log_id]
    
    return {
        "data": {
            "log": log_entry,
            "related_logs": related_logs,
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
