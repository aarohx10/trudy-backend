"""
Admin API Routes
"""
from fastapi import APIRouter, Header, Depends, HTTPException
from typing import Optional
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import DatabaseAdminService
from app.core.exceptions import ForbiddenError, NotFoundError
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

