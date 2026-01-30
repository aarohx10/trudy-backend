"""
Idempotency Key Checking
"""
import hashlib
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import Request, Header
from app.core.database import DatabaseService, DatabaseAdminService
from app.core.config import settings

logger = logging.getLogger(__name__)


def calculate_request_hash(request: Request, body: Any = None) -> str:
    """Calculate SHA256 hash of request for idempotency checking"""
    # Build hash components
    method = request.method
    path = str(request.url.path)
    query = str(request.url.query)
    
    # Sort headers for consistent hashing
    headers_dict = {}
    for key, value in request.headers.items():
        # Skip headers that shouldn't affect idempotency
        if key.lower() not in ["x-request-id", "x-forwarded-for", "user-agent", "host", "authorization", "x-idempotency-key"]:
            headers_dict[key.lower()] = value
    
    # Sort headers by key
    sorted_headers = sorted(headers_dict.items())
    headers_str = json.dumps(sorted_headers, sort_keys=True)
    
    # Handle body (can be bytes, dict, or other JSON-serializable)
    if body is None:
        body_str = ""
    elif isinstance(body, bytes):
        body_str = body.decode('utf-8', errors='ignore')
    else:
        # Serialize dict/object to JSON string
        body_str = json.dumps(body, sort_keys=True)
    
    # Combine components
    hash_input = f"{method}:{path}:{query}:{headers_str}:{body_str}"
    
    # Calculate hash
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()


async def check_idempotency_key(
    org_id: str,
    idempotency_key: str,
    request: Request,
    body: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    Check if idempotency key exists and return cached response.
    
    Args:
        org_id: Organization ID (organization-first approach) - used as client_id for backward compatibility
        idempotency_key: Idempotency key from header
        request: FastAPI request object
        body: Request body for hash calculation
    
    Note: Uses org_id as client_id for idempotency_keys table (backward compatibility)
    """
    if not idempotency_key:
        return None
    
    # Calculate request hash
    request_hash = calculate_request_hash(request, body)
    
    # Check database
    admin_db = DatabaseAdminService()
    
    try:
        # Query idempotency_keys table
        # Note: Using org_id as client_id for backward compatibility with existing table structure
        existing = admin_db.select_one(
            "idempotency_keys",
            {
                "client_id": org_id,  # Using org_id as client_id for idempotency
                "key": idempotency_key,
                "request_hash": request_hash,
            },
        )
        
        if existing:
            # Check if expired
            ttl_at = datetime.fromisoformat(existing["ttl_at"].replace("Z", "+00:00"))
            if datetime.now(ttl_at.tzinfo) > ttl_at:
                # Expired, delete it
                admin_db.delete("idempotency_keys", {"id": existing["id"]})
                return None
            
            # Return cached response
            logger.info(
                f"Idempotency key hit: {idempotency_key} for org {org_id}",
                extra={"request_hash": request_hash},
            )
            
            return {
                "response_body": existing["response_body"],
                "status_code": existing["status_code"],
            }
        
        return None
        
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "org_id": org_id,
            "idempotency_key": idempotency_key,
        }
        logger.error(f"[IDEMPOTENCY] Error checking idempotency key (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        # On error, continue without idempotency (fail open)
        return None


async def store_idempotency_response(
    org_id: str,
    idempotency_key: str,
    request: Request,
    body: Any,
    response_body: Dict[str, Any],
    status_code: int,
) -> None:
    """
    Store idempotency key response for future use.
    
    Args:
        org_id: Organization ID (organization-first approach) - used as client_id for backward compatibility
        idempotency_key: Idempotency key from header
        request: FastAPI request object
        body: Request body for hash calculation
        response_body: Response body to cache
        status_code: Response status code
    
    Note: Uses org_id as client_id for idempotency_keys table (backward compatibility)
    """
    if not idempotency_key:
        return
    
    # Calculate request hash
    request_hash = calculate_request_hash(request, body)
    
    # Calculate TTL
    ttl_at = datetime.utcnow() + timedelta(days=settings.IDEMPOTENCY_TTL_DAYS)
    
    # Store in database
    admin_db = DatabaseAdminService()
    
    try:
        admin_db.insert(
            "idempotency_keys",
            {
                "client_id": org_id,  # Using org_id as client_id for idempotency (backward compatibility)
                "key": idempotency_key,
                "request_hash": request_hash,
                "response_body": response_body,
                "status_code": status_code,
                "ttl_at": ttl_at.isoformat(),
            },
        )
        
        logger.info(
            f"Stored idempotency key: {idempotency_key} for org {org_id}",
            extra={"request_hash": request_hash, "status_code": status_code},
        )
        
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "org_id": org_id,
            "idempotency_key": idempotency_key,
        }
        # Handle unique constraint violation (key already exists)
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            logger.warning(f"[IDEMPOTENCY] Idempotency key already exists (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}")
        else:
            logger.error(f"[IDEMPOTENCY] Error storing idempotency key (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)


async def get_idempotency_key_header(
    request: Request,
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
) -> Optional[str]:
    """Extract idempotency key from header"""
    return x_idempotency_key



