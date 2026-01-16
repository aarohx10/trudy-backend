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
        
        # Get client identifier (from JWT or IP)
        client_id = self._get_client_id(request)
        
        if client_id:
            # Check rate limit
            if not self._check_rate_limit(client_id, request.url.path):
                logger.warning(f"Rate limit exceeded for client: {client_id}")
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
    
    def _get_client_id(self, request: Request) -> Optional[str]:
        """Get client identifier from request"""
        # Try to get from JWT token (if available in request state)
        if hasattr(request.state, "client_id"):
            return request.state.client_id
        
        # Fallback to IP address
        client_host = request.client.host if request.client else "unknown"
        return client_host
    
    def _check_rate_limit(self, client_id: str, path: str) -> bool:
        """Check if request is within rate limit"""
        # Get current window
        current_window = int(time.time() / 60)  # 1-minute windows
        
        # Get or create client rate limit data
        client_data = _rate_limit_store[client_id]
        
        # Clean old windows (older than 1 minute)
        current_time = time.time()
        client_data = {
            k: v for k, v in client_data.items()
            if current_time - v.get("timestamp", 0) < 60
        }
        _rate_limit_store[client_id] = client_data
        
        # Get or create window data
        window_key = f"{current_window}:{path}"
        if window_key not in client_data:
            client_data[window_key] = {"count": 0, "timestamp": current_time}
        
        # Increment count
        client_data[window_key]["count"] += 1
        
        # Check limit
        limit = settings.RATE_LIMIT_PER_MINUTE
        return client_data[window_key]["count"] <= limit


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
        logger.error(f"Error checking client quota: {e}")
        return True  # Fail open

