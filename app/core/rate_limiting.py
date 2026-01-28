"""
Rate Limiting Middleware
"""
import time
import logging
from typing import Dict, Optional
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)

# In-memory rate limit store (use Redis in production)
_rate_limit_store: Dict[str, Dict[str, any]] = defaultdict(dict)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware"""
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting if disabled
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)
        
        # CRITICAL: Get organization identifier (from JWT or IP)
        # Multi-tenant rate limiting: limit requests based on org_id to prevent one user from exhausting a whole team's quota
        org_id = self._get_org_id(request)
        
        if org_id:
            # Check rate limit
            if not self._check_rate_limit(org_id, request.url.path):
                logger.warning(f"Rate limit exceeded for organization: {org_id}")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": {
                            "code": "rate_limit_exceeded",
                            "message": "Rate limit exceeded",
                            "details": {
                                "limit": settings.RATE_LIMIT_PER_MINUTE,
                                "reset_at": (datetime.utcnow() + timedelta(minutes=1)).isoformat(),
                            },
                        },
                    },
                )
        
        response = await call_next(request)
        return response
    
    def _get_org_id(self, request: Request) -> Optional[str]:
        """
        Get organization ID from request.
        
        CRITICAL: Multi-tenant rate limiting - limit requests based on org_id.
        This prevents one user from exhausting a whole team's quota.
        """
        # Try to get from JWT token (if available in request state)
        # The auth middleware should set this from the JWT claims
        if hasattr(request.state, "org_id"):
            return request.state.org_id
        
        # Try to get from current_user if available (set by auth dependency)
        if hasattr(request.state, "current_user"):
            current_user = request.state.current_user
            if current_user and isinstance(current_user, dict):
                return current_user.get("clerk_org_id")
        
        # Fallback to IP address (for unauthenticated requests)
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"
    
    def _check_rate_limit(self, org_id: str, path: str) -> bool:
        """
        Check if request is within rate limit.
        
        CRITICAL: Multi-tenant rate limiting - limit requests based on org_id.
        This prevents one user from exhausting a whole team's quota.
        """
        # Get current window
        current_window = int(time.time() / 60)  # 1-minute windows
        
        # Get or create organization rate limit data
        org_data = _rate_limit_store[org_id]
        
        # Clean old windows (older than 1 minute)
        current_time = time.time()
        org_data = {
            k: v for k, v in org_data.items()
            if current_time - v.get("timestamp", 0) < 60
        }
        _rate_limit_store[org_id] = org_data
        
        # Get or create window data
        window_key = f"{current_window}:{path}"
        if window_key not in org_data:
            org_data[window_key] = {"count": 0, "timestamp": current_time}
        
        # Increment count
        org_data[window_key]["count"] += 1
        
        # Check limit
        limit = settings.RATE_LIMIT_PER_MINUTE
        return org_data[window_key]["count"] <= limit


# Per-client quota checking (for database-backed quotas)
async def check_client_quota(client_id: str, operation_type: str) -> bool:
    """
    Check if client has quota for operation
    
    Args:
        client_id: Client ID
        operation_type: Type of operation (e.g., "calls_per_day", "campaigns_per_month")
    
    Returns:
        True if quota available, False otherwise
    """
    from app.core.database import DatabaseAdminService
    
    try:
        db = DatabaseAdminService()
        client = db.select_one("clients", {"id": client_id})
        
        if not client:
            return False
        
        # Check quotas (implement based on your quota structure)
        # For now, return True (quotas not fully implemented)
        return True
        
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "client_id": client_id,
            "operation_type": operation_type,
        }
        logger.error(f"[RATE_LIMITING] Error checking client quota (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        return True  # Fail open

