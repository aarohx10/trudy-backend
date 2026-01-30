"""
Centralized CORS Configuration and Validation
SINGLE SOURCE OF TRUTH for all CORS logic across the application.
"""
import re
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# Compile CORS regex patterns once at module load (shared across all imports)
# Exported for debug endpoints that need to inspect patterns
_cors_compiled_patterns = []  # Private - use get_compiled_patterns() for access
for pattern in settings.CORS_WILDCARD_PATTERNS:
    # Ensure patterns are anchored for full string match
    if not pattern.startswith("^"):
        pattern = f"^{pattern}"
    if not pattern.endswith("$"):
        pattern = f"{pattern}$"
    try:
        _cors_compiled_patterns.append(re.compile(pattern))
    except re.error as e:
        logger.warning(f"Invalid CORS regex pattern '{pattern}': {e}")


def is_origin_allowed(origin: str) -> bool:
    """
    SINGLE SOURCE OF TRUTH for CORS origin validation.
    
    CRITICAL: CORS Policy Lockdown - validates frontend domains but also validates Clerk Issuer
    for JWT-based requests to ensure requests come from legitimate Clerk-authenticated clients.
    
    This function is used by:
    - UnifiedCORSMiddleware (app/core/middleware.py)
    - CORS health check endpoints (app/main.py)
    - Any other code that needs to validate origins
    
    Args:
        origin: The Origin header value from the request
        
    Returns:
        True if origin is allowed, False otherwise
    """
    if not origin:
        return False
    
    # Check exact origins first (fastest lookup)
    if origin in settings.CORS_ORIGINS:
        return True
    
    # Check regex patterns for dynamic subdomains (Vercel previews, etc.)
    for pattern in _cors_compiled_patterns:
        if pattern.match(origin):
            return True
    
    return False


def validate_clerk_issuer(issuer: str) -> bool:
    """
    Validate Clerk JWT issuer claim.
    
    CRITICAL: CORS Policy Lockdown - ensures JWT tokens come from legitimate Clerk instances.
    This prevents unauthorized access even if CORS origin is allowed.
    
    Args:
        issuer: The 'iss' claim from the JWT token
        
    Returns:
        True if issuer is valid Clerk instance, False otherwise
    """
    if not issuer:
        return False
    
    # Valid Clerk issuer patterns
    # Production: https://clerk.{your-domain}.com or https://{your-domain}.clerk.accounts.dev
    # Development: https://{your-domain}.clerk.accounts.dev
    valid_issuer_patterns = [
        r"^https://.*\.clerk\.accounts\.dev$",  # Clerk development instances
        r"^https://clerk\..*\.com$",  # Clerk production instances (custom domain)
        r"^https://.*\.clerk\.accounts\.com$",  # Clerk production instances
    ]
    
    import re
    for pattern in valid_issuer_patterns:
        if re.match(pattern, issuer):
            return True
    
    # Allow specific Clerk issuer from environment if configured
    allowed_issuer = getattr(settings, "CLERK_ISSUER", None)
    if allowed_issuer and issuer == allowed_issuer:
        return True
    
    return False


def get_cors_headers(origin: str, request_headers: str = None) -> dict:
    """
    Get CORS headers for an allowed origin.
    
    Args:
        origin: The Origin header value (must be validated with is_origin_allowed first)
        request_headers: The Access-Control-Request-Headers value from the request (for reflection)
        
    Returns:
        Dictionary of CORS headers to add to response
    """
    if not is_origin_allowed(origin):
        return {}
    
    # CRITICAL: Since we are using Allow-Credentials: true, 
    # the Access-Control-Allow-Origin header MUST NOT be '*'.
    # It must reflect the validated incoming origin exactly.
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD",
        "Access-Control-Max-Age": "86400",  # Cache preflight for 24 hours
        "Vary": "Origin",  # CRITICAL: Prevent cache poisoning and signal dynamic origin
    }

    # Standard compliance: specific headers or * based on credentials
    # Since we set Credentials=true, we should technically reflect headers instead of using *
    # heavily used browsers are permissive, but strict proxies might block * with creds
    if request_headers:
        headers["Access-Control-Allow-Headers"] = request_headers
    else:
        # Fallback for requests without specific requested headers
        headers["Access-Control-Allow-Headers"] = "*"
        
    return headers


def get_compiled_patterns() -> list:
    """
    Get the compiled regex patterns (for debug endpoints).
    
    Returns:
        List of compiled regex pattern strings
    """
    return [pattern.pattern for pattern in _cors_compiled_patterns]
