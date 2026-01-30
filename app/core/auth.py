"""
JWT Authentication and Authorization - Clerk ONLY
"""
import jwt  # PyJWT library
from typing import Optional, Dict, Any
from fastapi import Header, HTTPException, Request
import httpx
import logging
import secrets
from app.core.config import settings
from app.core.exceptions import UnauthorizedError, ForbiddenError
from app.core.debug_logging import debug_logger
from uuid import UUID

logger = logging.getLogger(__name__)

# Cache for Clerk JWKs
_clerk_jwks_cache: Optional[Dict[str, Any]] = None
_clerk_jwks_cache_expiry: Optional[float] = None


async def get_clerk_jwks() -> Dict[str, Any]:
    """Fetch JWKs from Clerk"""
    global _clerk_jwks_cache, _clerk_jwks_cache_expiry
    import time
    
    # Check cache
    if _clerk_jwks_cache and _clerk_jwks_cache_expiry and time.time() < _clerk_jwks_cache_expiry:
        debug_logger.log_auth("JWKS_FETCH", "Using cached Clerk JWKs")
        return _clerk_jwks_cache
    
    # Fetch from Clerk - HARD-CODED to use custom Clerk domain (FORCE - ignores env vars)
    jwks_url = 'https://clerk.truedy.sendora.ai/.well-known/jwks.json'
    debug_logger.log_auth("JWKS_FETCH", f"Fetching Clerk JWKs from {jwks_url}")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url, timeout=5.0)
            response.raise_for_status()
            _clerk_jwks_cache = response.json()
            _clerk_jwks_cache_expiry = time.time() + 3600  # Cache for 1 hour
            debug_logger.log_auth("JWKS_FETCH", "Clerk JWKs fetched successfully", {
                "keys_count": len(_clerk_jwks_cache.get("keys", [])),
                "cached_until": _clerk_jwks_cache_expiry
            })
            return _clerk_jwks_cache
    except Exception as e:
        logger.error(f"Failed to fetch Clerk JWKs: {e}")
        debug_logger.log_error("JWKS_FETCH", e, {"service": "clerk", "url": jwks_url})
        if _clerk_jwks_cache:
            debug_logger.log_auth("JWKS_FETCH", "Using stale cached Clerk JWKs as fallback")
            return _clerk_jwks_cache  # Use stale cache as fallback
        raise UnauthorizedError("Failed to fetch Clerk authentication keys")


def get_jwt_header(authorization: Optional[str] = Header(None)) -> str:
    """Extract JWT token from Authorization header"""
    if not authorization:
        debug_logger.log_auth("TOKEN_EXTRACT", "Missing Authorization header")
        raise UnauthorizedError("Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        debug_logger.log_auth("TOKEN_EXTRACT", "Invalid Authorization header format")
        raise UnauthorizedError("Invalid Authorization header format")
    
    token = authorization[7:]  # Remove "Bearer " prefix
    debug_logger.log_auth("TOKEN_EXTRACT", "Token extracted from header", {
        "token_length": len(token),
        "token_preview": token[:20] + "..." if len(token) > 20 else token
    })
    return token


def _jwk_to_rsa_public_key(jwk: Dict[str, Any]):
    """Convert JWK to RSA public key"""
    from cryptography.hazmat.primitives.asymmetric import rsa
    import base64
    
    def base64url_decode(value: str) -> bytes:
        """Decode base64url encoded string"""
        padding = 4 - len(value) % 4
        if padding != 4:
            value += "=" * padding
        return base64.urlsafe_b64decode(value)
    
    # Decode JWK values
    n_bytes = base64url_decode(jwk["n"])
    e_bytes = base64url_decode(jwk["e"])
    n_int = int.from_bytes(n_bytes, "big")
    e_int = int.from_bytes(e_bytes, "big")
    
    # Create RSA public key
    public_numbers = rsa.RSAPublicNumbers(e_int, n_int)
    return public_numbers.public_key()


async def verify_clerk_jwt(token: str) -> Dict[str, Any]:
    """Verify Clerk JWT token and return claims with org_id extraction logic
    
    CRITICAL: If org_id is null (user is in their personal workspace), 
    use user_id as the org_id to ensure solo users still have a data partition.
    """
    debug_logger.log_auth("TOKEN_VERIFY", "Starting Clerk JWT verification")
    try:
        # Get Clerk JWKs
        jwks = await get_clerk_jwks()
        
        # Decode header to find key ID
        unverified_header = jwt.get_unverified_header(token)
        debug_logger.log_auth("TOKEN_VERIFY", "Token header decoded", {
            "kid": unverified_header.get("kid"),
            "alg": unverified_header.get("alg")
        })
        
        # Find matching key
        matching_key = None
        for key in jwks.get("keys", []):
            if key["kid"] == unverified_header["kid"]:
                matching_key = key
                break
        
        if not matching_key:
            debug_logger.log_auth("TOKEN_VERIFY", "No matching key found for Clerk token")
            raise UnauthorizedError("Unable to find appropriate Clerk key")
        
        debug_logger.log_auth("TOKEN_VERIFY", "Matching key found for Clerk token")
        
        # Convert JWK to RSA public key
        public_key = _jwk_to_rsa_public_key(matching_key)
        
        # Decode and verify token with PyJWT
        # HARD-CODED: Use custom Clerk domain issuer (FORCE - ignores env vars)
        clerk_issuer = 'https://clerk.truedy.sendora.ai'
        debug_logger.log_auth("TOKEN_VERIFY", f"Verifying token with issuer: {clerk_issuer}")
        
        # CRITICAL: CORS Policy Lockdown - validate Clerk issuer
        from app.core.cors import validate_clerk_issuer
        if not validate_clerk_issuer(clerk_issuer):
            debug_logger.log_auth("TOKEN_VERIFY", f"Invalid Clerk issuer: {clerk_issuer}")
            raise UnauthorizedError("Invalid Clerk issuer")
        
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=clerk_issuer,
            options={"verify_aud": False},  # Clerk doesn't use standard audience
        )
        
        # CRITICAL LOGIC: Extract org_id and user_id, with fallback
        user_id = claims.get("sub")
        org_id = claims.get("org_id")
        
        # If org_id is null (user is in their personal workspace), use user_id as org_id
        if not org_id and user_id:
            org_id = user_id
            debug_logger.log_auth("TOKEN_VERIFY", "org_id is null, using user_id as org_id for personal workspace", {
                "user_id": user_id,
                "org_id": org_id
            })
        
        # Store the effective org_id in claims for downstream use
        claims["_effective_org_id"] = org_id
        
        debug_logger.log_auth("TOKEN_VERIFY", "Clerk JWT verified successfully", {
            "user_id": user_id,
            "org_id": org_id,
            "effective_org_id": claims.get("_effective_org_id"),
            "email": claims.get("email")
        })
        
        return claims
        
    except jwt.InvalidTokenError as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "provider": "clerk",
        }
        logger.warning(f"[AUTH] Clerk JWT verification failed (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        debug_logger.log_error("TOKEN_VERIFY", e, {"provider": "clerk", "raw_error": error_details_raw})
        raise UnauthorizedError("Invalid or expired Clerk token")
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "error_module": getattr(e, '__module__', None),
            "error_class": type(e).__name__,
            "full_traceback": traceback.format_exc(),
            "provider": "clerk",
        }
        logger.error(f"[AUTH] Clerk JWT verification error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        debug_logger.log_error("TOKEN_VERIFY", e, {"provider": "clerk", "raw_error": error_details_raw})
        raise UnauthorizedError("Clerk token verification failed")


async def verify_jwt(token: str) -> Dict[str, Any]:
    """Verify Clerk JWT token and return claims"""
    debug_logger.log_auth("TOKEN_VERIFY", "Starting Clerk JWT verification")
    try:
        claims = await verify_clerk_jwt(token)
        claims["_token_type"] = "clerk"
        debug_logger.log_auth("TOKEN_VERIFY", "JWT verified as Clerk token")
        return claims
    except Exception as clerk_error:
        logger.error(f"Clerk JWT verification failed: {clerk_error}")
        debug_logger.log_error("TOKEN_VERIFY", clerk_error if isinstance(clerk_error, Exception) else Exception(str(clerk_error)), {
            "provider": "clerk"
        })
        raise UnauthorizedError("Invalid or expired Clerk token")


async def ensure_admin_role_for_creator(
    user_id: str,
    clerk_org_id: str,
    clerk_role: Optional[str],
    user_data: Optional[Dict[str, Any]],
    admin_db: Any,
) -> str:
    """
    Enterprise-grade role determination: Ensure organization creators/admins always get admin role.
    
    This function handles all edge cases:
    - Clerk org admins → always admin
    - First user in organization (by client_id) → admin
    - First user in personal workspace (by clerk_org_id) → admin
    - Updates database immediately for consistency
    
    Args:
        user_id: Clerk user ID
        clerk_org_id: Effective organization ID (user_id for personal workspace)
        clerk_role: Clerk organization role (org:admin, org:member, etc.)
        user_data: User data from database (if exists)
        admin_db: Supabase admin client
    
    Returns:
        Determined role: "client_admin" or "client_user"
    """
    # Priority 1: Clerk org admin → always grant admin role
    if clerk_role == "org:admin":
        logger.info(f"[ROLE_DETERMINATION] User {user_id} is Clerk org admin → granting client_admin")
        if user_data and user_data.get("role") != "client_admin":
            try:
                admin_db.table("users").update({"role": "client_admin"}).eq("clerk_user_id", user_id).execute()
                logger.info(f"[ROLE_DETERMINATION] Updated user {user_id} role to client_admin (Clerk org admin)")
            except Exception as e:
                logger.warning(f"[ROLE_DETERMINATION] Failed to update user role in database: {e}")
        return "client_admin"
    
    # Priority 2: Check if user is first/only user in organization
    # This handles both organization users and personal workspace users
    if user_data:
        current_role = user_data.get("role", "client_user")
        client_id = user_data.get("client_id")
        
        # If already admin, no need to check
        if current_role == "client_admin":
            logger.debug(f"[ROLE_DETERMINATION] User {user_id} already has client_admin role")
            return "client_admin"
        
        # Check if user is first/only user in their organization
        try:
            if client_id:
                # Standard case: Check users by client_id
                logger.debug(f"[ROLE_DETERMINATION] Checking users by client_id={client_id}")
                org_users = admin_db.table("users").select("id,role,clerk_user_id").eq("client_id", client_id).execute()
                
                if org_users.data:
                    # Check if any other users are admins (excluding current user)
                    other_admins = [
                        u for u in org_users.data 
                        if u.get("clerk_user_id") != user_id and u.get("role") == "client_admin"
                    ]
                    
                    if not other_admins:
                        # This user is the first admin - upgrade them
                        logger.info(f"[ROLE_DETERMINATION] User {user_id} is first user in client_id={client_id} → upgrading to client_admin")
                        admin_db.table("users").update({"role": "client_admin"}).eq("clerk_user_id", user_id).execute()
                        return "client_admin"
                    else:
                        logger.debug(f"[ROLE_DETERMINATION] User {user_id} is not first user ({len(other_admins)} other admins exist)")
                else:
                    logger.warning(f"[ROLE_DETERMINATION] No users found with client_id={client_id} (unexpected)")
            else:
                # Personal workspace case: Check users by clerk_org_id
                logger.debug(f"[ROLE_DETERMINATION] client_id is None, checking users by clerk_org_id={clerk_org_id}")
                org_users = admin_db.table("users").select("id,role,clerk_user_id,clerk_org_id").eq("clerk_org_id", clerk_org_id).execute()
                
                if org_users.data:
                    # Check if any other users are admins (excluding current user)
                    other_admins = [
                        u for u in org_users.data 
                        if u.get("clerk_user_id") != user_id and u.get("role") == "client_admin"
                    ]
                    
                    if not other_admins:
                        # This user is the first admin in personal workspace - upgrade them
                        logger.info(f"[ROLE_DETERMINATION] User {user_id} is first user in personal workspace clerk_org_id={clerk_org_id} → upgrading to client_admin")
                        admin_db.table("users").update({"role": "client_admin"}).eq("clerk_user_id", user_id).execute()
                        return "client_admin"
                    else:
                        logger.debug(f"[ROLE_DETERMINATION] User {user_id} is not first user ({len(other_admins)} other admins exist)")
                else:
                    # No users found - this is a new user, upgrade them immediately
                    logger.info(f"[ROLE_DETERMINATION] No users found with clerk_org_id={clerk_org_id} → new user, upgrading to client_admin")
                    admin_db.table("users").update({"role": "client_admin"}).eq("clerk_user_id", user_id).execute()
                    return "client_admin"
        except Exception as e:
            logger.error(f"[ROLE_DETERMINATION] Failed to check/upgrade user role: {e}", exc_info=True)
        
        # Return current role if no upgrade happened
        return current_role
    else:
        # New user not in database yet - default to client_user
        # They will be upgraded to admin when they're created via /auth/me
        logger.debug(f"[ROLE_DETERMINATION] User {user_id} not in database yet → defaulting to client_user")
        return "client_user"


async def get_current_user(
    authorization: Optional[str] = Header(None),
    x_client_id: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    Get current user from Clerk JWT token.
    
    Returns a UserContext object containing:
    - clerk_user_id: The Clerk user ID
    - clerk_org_id: The effective organization ID (uses user_id as fallback for personal workspace)
    - role: User's role in the organization
    """
    from app.models.schemas import UserContext
    
    debug_logger.log_auth("GET_USER", "Starting user lookup")
    # Extract and verify token
    token = get_jwt_header(authorization)
    claims = await verify_jwt(token)
    
    # Extract user info from Clerk token
    user_id = claims.get("sub")  # Clerk user ID
    email = claims.get("email")
    name = claims.get("name", "") or claims.get("first_name", "") + " " + claims.get("last_name", "")
    picture = claims.get("picture", "") or claims.get("image_url", "")
    
    # Extract Clerk-specific claims
    # CRITICAL: Use _effective_org_id from verify_clerk_jwt (handles personal workspace fallback)
    clerk_org_id = claims.get("_effective_org_id") or claims.get("org_id")
    clerk_role = claims.get("org_role")  # Clerk organization role
    
    if not user_id:
        raise UnauthorizedError("Invalid token: missing user ID")
    
    if not clerk_org_id:
        # This should never happen after verify_clerk_jwt, but safety check
        clerk_org_id = user_id
        debug_logger.log_auth("GET_USER", "WARNING: No org_id found, using user_id as fallback", {
            "user_id": user_id,
            "org_id": clerk_org_id
        })
    
    # Try to get user from database
    # Use admin client to bypass RLS for this lookup
    from app.core.database import get_supabase_admin_client
    admin_db = get_supabase_admin_client()
    
    user_data = None
    role = "client_user"
    
    # Look up by clerk_user_id first
    debug_logger.log_auth("GET_USER", "Looking up user by clerk_user_id", {"user_id": user_id})
    if user_id:
        user_record = admin_db.table("users").select("*").eq("clerk_user_id", user_id).execute()
        if user_record.data:
            user_data = user_record.data[0]
            debug_logger.log_auth("GET_USER", "User found by clerk_user_id", {
                "user_id": user_data.get("id"),
                "client_id": user_data.get("client_id")
            })
    
    # ENTERPRISE-GRADE ROLE DETERMINATION
    # Use centralized function to ensure organization creators/admins always get admin role
    role = await ensure_admin_role_for_creator(
        user_id=user_id,
        clerk_org_id=clerk_org_id,
        clerk_role=clerk_role,
        user_data=user_data,
        admin_db=admin_db,
    )
    
    # Refresh user_data if role was upgraded
    if user_data and role == "client_admin" and user_data.get("role") != "client_admin":
        try:
            user = admin_db.table("users").select("*").eq("clerk_user_id", user_id).execute()
            if user.data:
                user_data = user.data[0]
        except Exception as e:
            logger.warning(f"Failed to refresh user_data after role upgrade: {e}")
    
    # Create UserContext object
    user_context = UserContext(
        clerk_user_id=user_id,
        clerk_org_id=clerk_org_id,  # Always set - uses user_id as fallback for personal workspace
        role=role,
        email=email,
        name=name.strip() if name else None,
        picture=picture,
        token=token,
        claims=claims,
    )
    
    # Return as dict for backward compatibility (many endpoints expect dict)
    result = user_context.dict()
    # Add legacy fields for backward compatibility
    result["user_id"] = user_id
    # CRITICAL: Populate client_id from DB so legacy fields (e.g. agent_record["client_id"]) and
    # auth endpoints (/clients, /users, api_keys) work. User is created with client_id by /auth/me.
    result["client_id"] = user_data.get("client_id") if user_data else None
    result["token_type"] = "clerk"
    
    debug_logger.log_auth("GET_USER", "User lookup completed", {
        "clerk_user_id": user_id,
        "clerk_org_id": clerk_org_id,
        "role": role,
        "token_type": "clerk",
    })
    
    return result


async def get_optional_current_user(
    authorization: Optional[str] = Header(None),
    x_client_id: Optional[str] = Header(None),
) -> Optional[Dict[str, Any]]:
    """
    Get current user from Clerk JWT token, or None if not authenticated.
    This is a non-raising version of get_current_user for endpoints that
    can work with or without authentication (like /logs).
    """
    if not authorization:
        return None
    
    try:
        return await get_current_user(authorization, x_client_id)
    except Exception:
        # Any auth error returns None instead of raising
        return None


def require_role(required_roles: list[str]):
    """Decorator to require specific roles"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            user = kwargs.get("current_user")
            if not user:
                raise UnauthorizedError("Authentication required")
            
            user_role = user.get("role")
            if user_role not in required_roles and user_role != "agency_admin":
                raise ForbiddenError(f"Requires one of: {', '.join(required_roles)}")
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator


async def verify_ultravox_signature(request: Request) -> bool:
    """
    Verify X-Tool-Secret header for Ultravox tool callbacks.
    Uses secrets.compare_digest to prevent timing attacks.
    
    Raises HTTPException(403) if verification fails.
    """
    tool_secret = request.headers.get("X-Tool-Secret")
    
    if not tool_secret:
        logger.warning("Missing X-Tool-Secret header in tool callback request")
        raise HTTPException(
            status_code=403,
            detail="Missing X-Tool-Secret header"
        )
    
    expected_secret = settings.ULTRAVOX_TOOL_SECRET
    
    if not expected_secret:
        logger.error("ULTRAVOX_TOOL_SECRET not configured in settings")
        raise HTTPException(
            status_code=500,
            detail="Tool secret not configured"
        )
    
    # Use secrets.compare_digest to prevent timing attacks
    if not secrets.compare_digest(tool_secret, expected_secret):
        logger.warning("Invalid X-Tool-Secret header in tool callback request")
        raise HTTPException(
            status_code=403,
            detail="Invalid X-Tool-Secret header"
        )
    
    return True

