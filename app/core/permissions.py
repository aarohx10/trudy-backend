"""
Permission Helpers - Centralized permission checking
"""
from typing import Dict, Any
from fastapi import Depends
from app.core.auth import get_current_user
from app.core.exceptions import ForbiddenError
import logging

logger = logging.getLogger(__name__)


def require_admin_role(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Dependency function that ensures the current user has admin role.
    
    Can be used as a FastAPI dependency to replace inline permission checks:
    
    Before:
        current_user: dict = Depends(get_current_user)
        if current_user["role"] not in ["client_admin", "agency_admin"]:
            raise ForbiddenError("Insufficient permissions")
    
    After:
        current_user: dict = Depends(require_admin_role)
    
    Args:
        current_user: User context from get_current_user dependency
    
    Returns:
        User context dict (same as input, but guaranteed to have admin role)
    
    Raises:
        ForbiddenError: If user doesn't have admin role
    """
    role = current_user.get("role", "client_user")
    user_id = current_user.get("clerk_user_id") or current_user.get("user_id")
    
    if role not in ["client_admin", "agency_admin"]:
        # ENHANCED DEBUG LOGGING: Log all context for debugging
        clerk_org_id = current_user.get("clerk_org_id")
        client_id = current_user.get("client_id")  # Optional - only for billing endpoints
        clerk_role = current_user.get("claims", {}).get("org_role") if isinstance(current_user.get("claims"), dict) else None
        
        logger.error(
            f"[PERMISSION_CHECK] [DEBUG] Access denied for user | "
            f"clerk_user_id={user_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"client_id={client_id} (billing only) | "
            f"role={role} | "
            f"clerk_role={clerk_role} | "
            f"required_roles=['client_admin', 'agency_admin']"
        )
        
        raise ForbiddenError(
            f"Insufficient permissions. Required role: client_admin or agency_admin. "
            f"Current role: {role}. User ID: {user_id}. "
            f"Organization ID: {clerk_org_id}. "
            f"Please ensure you have called /auth/me to create your user account."
        )
    
    logger.debug(f"[PERMISSION_CHECK] Access granted for user {user_id} | role={role}")
    return current_user
