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
        # NOTE: Clerk JWT tokens don't include org_id by default unless configured as custom claim
        # If org_id is missing, we fetch it from Clerk API to get the actual organization ID
        user_id = claims.get("sub")
        org_id = claims.get("org_id")
        
        logger.debug(f"[TOKEN_VERIFY] [STEP 1] Initial extraction | user_id={user_id} | org_id_from_token={org_id}")
        debug_logger.log_auth("TOKEN_VERIFY", "Initial org_id extraction", {
            "user_id": user_id,
            "org_id_from_token": org_id
        })
        
        # Validate user_id exists
        if not user_id:
            logger.error("[TOKEN_VERIFY] [ERROR] user_id (sub) is missing from token claims")
            raise UnauthorizedError("Invalid token: missing user ID")
        
        # If org_id is missing from token or is empty string, fetch it from Clerk API
        if (not org_id or org_id == "") and user_id:
            logger.debug(f"[TOKEN_VERIFY] [STEP 2] org_id not in token or is empty, fetching from Clerk API | user_id={user_id} | org_id_from_token={org_id}")
            debug_logger.log_auth("TOKEN_VERIFY", "Fetching org_id from Clerk API", {
                "user_id": user_id,
                "reason": "org_id missing or empty in token"
            })
            try:
                clerk_secret_key = getattr(settings, 'CLERK_SECRET_KEY', '')
                if clerk_secret_key:
                    # Fetch user's organization memberships from Clerk API
                    async with httpx.AsyncClient() as client:
                        response = await client.get(
                            f"https://api.clerk.dev/v1/users/{user_id}/organization_memberships",
                            headers={
                                "Authorization": f"Bearer {clerk_secret_key}",
                                "Content-Type": "application/json",
                            },
                            timeout=5.0,
                        )
                        if response.status_code == 200:
                            response_data = response.json()
                            # Clerk API returns paginated response with 'data' array
                            memberships = response_data.get("data", [])
                            logger.debug(f"[TOKEN_VERIFY] [STEP 2a] Clerk API response | memberships_count={len(memberships)}")
                            
                            # Get the first organization (or primary organization)
                            if memberships and len(memberships) > 0:
                                # Find primary org (admin role) or first one
                                primary_org = next(
                                    (m for m in memberships if m.get("role") == "org:admin"),
                                    memberships[0]
                                )
                                # Extract org_id from membership object
                                # OrganizationMembership has organization.id property
                                organization_obj = primary_org.get("organization", {})
                                fetched_org_id = organization_obj.get("id")
                                
                                # Validate fetched_org_id is not empty
                                if fetched_org_id and str(fetched_org_id).strip():
                                    org_id = str(fetched_org_id).strip()
                                    logger.info(f"[TOKEN_VERIFY] [STEP 2b] ✅ Fetched org_id from Clerk API | user_id={user_id} | org_id={org_id}")
                                    debug_logger.log_auth("TOKEN_VERIFY", "Fetched org_id from Clerk API", {
                                        "user_id": user_id,
                                        "org_id": org_id,
                                        "membership_role": primary_org.get("role")
                                    })
                                else:
                                    logger.warning(f"[TOKEN_VERIFY] [STEP 2b] Organization object has empty 'id' in membership | membership={primary_org}")
                                    debug_logger.log_auth("TOKEN_VERIFY", "Organization object has empty id", {
                                        "user_id": user_id,
                                        "membership": primary_org,
                                        "fetched_org_id": fetched_org_id
                                    })
                            else:
                                logger.debug(f"[TOKEN_VERIFY] [STEP 2b] No organization memberships found for user | user_id={user_id}")
                                debug_logger.log_auth("TOKEN_VERIFY", "No organization memberships found", {
                                    "user_id": user_id
                                })
                        else:
                            logger.warning(f"[TOKEN_VERIFY] [STEP 2a] Clerk API returned non-200 status | status={response.status_code} | response={response.text}")
                            debug_logger.log_auth("TOKEN_VERIFY", "Clerk API error", {
                                "user_id": user_id,
                                "status_code": response.status_code,
                                "response": response.text[:200]  # Truncate for logging
                            })
            except Exception as e:
                logger.warning(f"[TOKEN_VERIFY] [STEP 2] Failed to fetch org_id from Clerk API: {e}", exc_info=True)
                debug_logger.log_error("TOKEN_VERIFY", e, {
                    "user_id": user_id,
                    "step": "fetch_org_id_from_api"
                })
            
            # Only fallback to user_id if we still don't have an org_id (personal workspace)
            if not org_id or org_id == "":
                org_id = user_id
                logger.warning(f"[TOKEN_VERIFY] [STEP 3] ⚠️ No org_id found, using user_id as fallback (personal workspace) | user_id={user_id} | org_id={org_id}")
                debug_logger.log_auth("TOKEN_VERIFY", "org_id is null/empty, using user_id as org_id for personal workspace", {
                    "user_id": user_id,
                    "org_id": org_id
                })
        
        # CRITICAL VALIDATION: Ensure org_id is NEVER None or empty
        # This is the final safety check before storing in claims
        # Convert empty string to user_id fallback
        if not org_id or org_id == "":
            logger.error(f"[TOKEN_VERIFY] [ERROR] org_id is still None/empty after all fallback logic | user_id={user_id}")
            debug_logger.log_error("TOKEN_VERIFY", Exception("org_id cannot be determined"), {
                "user_id": user_id,
                "step": "final_validation"
            })
            raise UnauthorizedError("Cannot determine organization ID from token")
        
        # Strip whitespace and validate it's not empty after stripping
        org_id = str(org_id).strip()
        if not org_id:
            # Final fallback: use user_id if org_id is still empty after all processing
            logger.warning(f"[TOKEN_VERIFY] [WARNING] org_id is empty after stripping, using user_id as final fallback | user_id={user_id}")
            org_id = user_id
            debug_logger.log_auth("TOKEN_VERIFY", "org_id empty after stripping, using user_id as final fallback", {
                "user_id": user_id,
                "org_id": org_id
            })
        
        # Final check: if still empty, raise error (should never happen)
        if not org_id:
            logger.error(f"[TOKEN_VERIFY] [ERROR] org_id is still empty after all fallback logic | user_id={user_id}")
            debug_logger.log_error("TOKEN_VERIFY", Exception("org_id cannot be determined"), {
                "user_id": user_id,
                "step": "final_validation"
            })
            raise UnauthorizedError("Organization ID cannot be empty")
        
        logger.info(f"[TOKEN_VERIFY] [STEP 4] ✅ Final org_id validation passed | user_id={user_id} | org_id={org_id}")
        debug_logger.log_auth("TOKEN_VERIFY", "Final org_id validation passed", {
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
    - First user in organization (by clerk_org_id) → admin
    - First user in personal workspace (by clerk_org_id) → admin
    - Updates database immediately for consistency
    
    NOTE: Uses clerk_org_id for organization scoping (organization-first approach).
    client_id is only used for billing/audit tables, not for main app operations.
    
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
        
        # SIMPLIFIED LOGIC: Check if user is in an organization (not personal workspace)
        # If in organization, grant admin immediately
        is_personal_workspace = (clerk_org_id == user_id)
        
        if not is_personal_workspace:
            # User is in an organization - SIMPLIFIED: grant admin immediately
            if current_role != "client_admin":
                logger.info(f"[ROLE_DETERMINATION] User {user_id} is in organization {clerk_org_id} → upgrading to client_admin (simplified logic)")
                try:
                    admin_db.table("users").update({"role": "client_admin"}).eq("clerk_user_id", user_id).execute()
                    return "client_admin"
                except Exception as e:
                    logger.error(f"[ROLE_DETERMINATION] Failed to upgrade user role: {e}", exc_info=True)
                    # Return admin anyway (SIMPLIFIED LOGIC)
                    return "client_admin"
            return "client_admin"
        
        # Personal workspace case: Check if user is first/only user
        # NOTE: Use clerk_org_id for all role determination (organization-first approach)
        try:
            # Check users by clerk_org_id (organization-first approach)
            logger.debug(f"[ROLE_DETERMINATION] Checking users by clerk_org_id={clerk_org_id}")
            org_users = admin_db.table("users").select("id,role,clerk_user_id,clerk_org_id").eq("clerk_org_id", clerk_org_id).execute()
            
            if org_users.data:
                # Check if any other users are admins (excluding current user)
                other_admins = [
                    u for u in org_users.data 
                    if u.get("clerk_user_id") != user_id and u.get("role") == "client_admin"
                ]
                
                if not other_admins:
                    # This user is the first admin - upgrade them
                    logger.info(f"[ROLE_DETERMINATION] User {user_id} is first user in clerk_org_id={clerk_org_id} → upgrading to client_admin")
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
            # On error, grant admin to be safe (SIMPLIFIED LOGIC)
            logger.warning(f"[ROLE_DETERMINATION] Error checking users, granting admin as fallback")
            return "client_admin"
        
        # Return current role if no upgrade happened
        return current_role
    else:
        # New user not in database yet
        # SIMPLIFIED LOGIC: If user has an organization (not personal workspace), grant admin immediately
        # Personal workspace: clerk_org_id == user_id (fallback from verify_clerk_jwt)
        # Organization: clerk_org_id != user_id (actual org from Clerk)
        
        is_personal_workspace = (clerk_org_id == user_id)
        
        if not is_personal_workspace:
            # User is in an organization - grant admin immediately (SIMPLIFIED LOGIC)
            logger.info(f"[ROLE_DETERMINATION] User {user_id} is in organization {clerk_org_id} → granting client_admin (simplified logic)")
            return "client_admin"
        else:
            # Personal workspace - check if they're the first user
            try:
                org_users = admin_db.table("users").select("id,role,clerk_user_id,clerk_org_id").eq("clerk_org_id", clerk_org_id).execute()
                if not org_users.data or len(org_users.data) == 0:
                    # First user in personal workspace - grant admin
                    logger.info(f"[ROLE_DETERMINATION] User {user_id} is first user in personal workspace → granting client_admin")
                    return "client_admin"
                else:
                    # Not first user - default to client_user
                    logger.debug(f"[ROLE_DETERMINATION] User {user_id} not first user in personal workspace → defaulting to client_user")
                    return "client_user"
            except Exception as e:
                logger.error(f"[ROLE_DETERMINATION] Failed to check personal workspace users: {e}", exc_info=True)
                # On error, grant admin to be safe (SIMPLIFIED LOGIC)
                logger.warning(f"[ROLE_DETERMINATION] Error checking users, granting admin as fallback")
                return "client_admin"


async def get_current_user(
    authorization: Optional[str] = Header(None),
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
    # Handle empty strings explicitly - empty string should be treated as None
    effective_org_id = claims.get("_effective_org_id")
    org_id_from_token = claims.get("org_id")
    
    # Convert empty strings to None for proper fallback logic
    if effective_org_id == "":
        effective_org_id = None
    if org_id_from_token == "":
        org_id_from_token = None
    
    clerk_org_id = effective_org_id or org_id_from_token
    clerk_role = claims.get("org_role")  # Clerk organization role
    
    # ENHANCED DEBUG LOGGING: Log token claims
    logger.debug(
        f"[GET_USER] [STEP 1] Token claims extracted | "
        f"user_id={user_id} | "
        f"org_id_from_token={org_id_from_token} | "
        f"effective_org_id={effective_org_id} | "
        f"clerk_org_id={clerk_org_id} | "
        f"clerk_role={clerk_role}"
    )
    debug_logger.log_auth("GET_USER", "Token claims extracted", {
        "user_id": user_id,
        "org_id_from_token": org_id_from_token,
        "effective_org_id": effective_org_id,
        "clerk_org_id": clerk_org_id,
        "clerk_role": clerk_role
    })
    
    if not user_id:
        logger.error("[GET_USER] [ERROR] user_id is missing from token claims")
        raise UnauthorizedError("Invalid token: missing user ID")
    
    # CRITICAL VALIDATION: Ensure clerk_org_id is NEVER None or empty
    # If both are None/empty, use user_id as fallback (personal workspace)
    if not clerk_org_id:
        logger.warning(
            f"[GET_USER] [WARNING] clerk_org_id is None/empty, using user_id as fallback (personal workspace) | "
            f"user_id={user_id} | "
            f"effective_org_id={effective_org_id} | "
            f"org_id_from_token={org_id_from_token}"
        )
        clerk_org_id = user_id  # Personal workspace fallback
        debug_logger.log_auth("GET_USER", "Using user_id as org_id fallback for personal workspace", {
            "user_id": user_id,
            "clerk_org_id": clerk_org_id
        })
    
    # Strip whitespace and validate it's not empty after stripping
    clerk_org_id = str(clerk_org_id).strip()
    if not clerk_org_id:
        logger.error(
            f"[GET_USER] [ERROR] clerk_org_id is None/empty after verify_clerk_jwt | "
            f"user_id={user_id} | "
            f"effective_org_id={claims.get('_effective_org_id')} | "
            f"org_id_from_token={claims.get('org_id')}"
        )
        debug_logger.log_error("GET_USER", Exception("clerk_org_id cannot be determined"), {
            "user_id": user_id,
            "effective_org_id": claims.get('_effective_org_id'),
            "org_id_from_token": claims.get('org_id')
        })
        raise UnauthorizedError("Cannot determine organization ID from token")
    
    # Strip whitespace and validate it's not empty after stripping
    clerk_org_id = str(clerk_org_id).strip()
    if not clerk_org_id:
        logger.error(
            f"[GET_USER] [ERROR] clerk_org_id is empty string after stripping | "
            f"user_id={user_id}"
        )
        debug_logger.log_error("GET_USER", Exception("clerk_org_id is empty after stripping"), {
            "user_id": user_id
        })
        raise UnauthorizedError("Organization ID cannot be empty")
    
    logger.info(
        f"[GET_USER] [STEP 2] ✅ clerk_org_id validation passed | "
        f"user_id={user_id} | "
        f"clerk_org_id={clerk_org_id}"
    )
    debug_logger.log_auth("GET_USER", "clerk_org_id validation passed", {
        "user_id": user_id,
        "clerk_org_id": clerk_org_id
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
    # NOTE: client_id is kept only for billing/audit endpoints (clients, users, api_keys, credit_transactions)
    # Main app tables (agents, voices, calls, campaigns, etc.) use clerk_org_id only
    # client_id is populated from user_data if it exists (for billing endpoints), but not actively fetched
    result["client_id"] = user_data.get("client_id") if user_data else None
    result["token_type"] = "clerk"
    
    # ENHANCED DEBUG LOGGING: Log all critical values
    logger.info(
        f"[GET_USER] [DEBUG] User lookup completed | "
        f"clerk_user_id={user_id} | "
        f"clerk_org_id={clerk_org_id} | "
        f"role={role} | "
        f"clerk_role={clerk_role} | "
        f"client_id={result.get('client_id')} (billing only) | "
        f"user_in_db={'yes' if user_data else 'no'} | "
        f"token_type=clerk"
    )
    
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
        return await get_current_user(authorization)
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

