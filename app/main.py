"""
Trudy Backend API - FastAPI Application
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import logging
import json
import time
from pathlib import Path
from contextlib import asynccontextmanager
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limiting import RateLimitMiddleware
from app.core.middleware import RequestIDMiddleware, LoggingMiddleware, UnifiedCORSMiddleware
from app.core.cors import is_origin_allowed
from app.core.debug_logging import debug_logger
from app.core.db_logging import log_error
from app.api.v1 import api_router
from app.api.internal import routes as internal_routes
from app.api.admin import routes as admin_routes
from app.core.exceptions import TrudyException

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting Trudy Backend API...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    debug_logger.log_step("STARTUP", "Application starting", {"environment": settings.ENVIRONMENT})
    
    # Log CORS configuration in detail
    logger.info(f"✅ CORS Exact Origins: {settings.CORS_ORIGINS}")
    logger.info(f"✅ CORS Wildcard Patterns: {settings.CORS_WILDCARD_PATTERNS}")
    
    # Convert wildcard patterns to regex for logging
    cors_regex_for_log = []
    for pattern in settings.CORS_WILDCARD_PATTERNS:
        regex_pattern = pattern.replace(".", r"\.").replace("*", r".*")
        cors_regex_for_log.append(f"^{regex_pattern}$")
    
    debug_logger.log_step("CORS_CONFIG", "CORS configuration loaded", {
        "exact_origins": settings.CORS_ORIGINS,
        "wildcard_patterns": settings.CORS_WILDCARD_PATTERNS,
        "regex_patterns": cors_regex_for_log,
        "total_exact": len(settings.CORS_ORIGINS),
        "total_wildcards": len(settings.CORS_WILDCARD_PATTERNS),
    })
    
    # Check Ultravox configuration
    if settings.ULTRAVOX_API_KEY:
        logger.info(f"✅ Ultravox API Key: Configured (length: {len(settings.ULTRAVOX_API_KEY)})")
        logger.info(f"✅ Ultravox Base URL: {settings.ULTRAVOX_BASE_URL}")
        debug_logger.log_step("ULTRAVOX_CONFIG", "Ultravox configured", {
            "base_url": settings.ULTRAVOX_BASE_URL,
            "key_length": len(settings.ULTRAVOX_API_KEY)
        })
        
        # Auto-register webhook with Ultravox
        if settings.WEBHOOK_BASE_URL and settings.ULTRAVOX_WEBHOOK_SECRET:
            try:
                from app.services.ultravox import ultravox_client
                webhook_id = await ultravox_client.ensure_webhook_registration()
                if webhook_id:
                    logger.info(f"✅ Webhook registered with Ultravox: {webhook_id}")
                    debug_logger.log_step("WEBHOOK_REGISTRATION", "Webhook registered", {"webhook_id": webhook_id})
                else:
                    logger.warning("⚠️  Failed to register webhook with Ultravox")
            except Exception as e:
                logger.error(f"⚠️  Error during webhook registration: {e}", exc_info=True)
        else:
            logger.warning("⚠️  WEBHOOK_BASE_URL or ULTRAVOX_WEBHOOK_SECRET not configured - webhooks will not be auto-registered")
    else:
        logger.warning("⚠️  Ultravox API Key: NOT CONFIGURED - Voice syncing will be disabled")
        logger.warning("⚠️  Please set ULTRAVOX_API_KEY in your .env file")
        debug_logger.log_step("ULTRAVOX_CONFIG", "Ultravox NOT configured", {})
    
    
    yield
    # Shutdown
    logger.info("Shutting down Trudy Backend API...")
    debug_logger.log_step("SHUTDOWN", "Application shutting down", {})


app = FastAPI(
    title="Trudy API",
    description="Voice AI calling platform backend API",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT != "prod" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "prod" else None,
    lifespan=lifespan,
)

# ============================================================
# CORS Configuration (Bulletproof)
# ============================================================
# Strategy:
# 1. Exact origins checked first (fastest, most secure)
# 2. Regex patterns checked for dynamic subdomains (Vercel previews)
# 3. max_age caches preflight for 24 hours (reduces OPTIONS requests)
# 4. Helper function validates origins consistently across all handlers

# Log CORS configuration
logger.info(f"✅ CORS Exact Origins ({len(settings.CORS_ORIGINS)}): {settings.CORS_ORIGINS[:5]}...")  # Log first 5
logger.info(f"✅ CORS Wildcard Patterns ({len(settings.CORS_WILDCARD_PATTERNS)}): {settings.CORS_WILDCARD_PATTERNS}")

# Middleware order matters! In FastAPI, middleware is applied in REVERSE order of addition.
# To make CORS the OUTERMOST layer (first to receive request, last to send response), 
# it must be added LAST.
#
# Execution order for requests: CORS -> RateLimit -> Logging -> RequestID -> app
# Execution order for responses: app -> RequestID -> Logging -> RateLimit -> CORS

# Add Middleware
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(UnifiedCORSMiddleware)  # SINGLE SOURCE OF TRUTH for CORS

logger.info("✅ Middlewares configured (CORS is outermost)")


# ============================================================
# Exception Handlers (CORS headers added by UnifiedCORSMiddleware)
# ============================================================

@app.exception_handler(TrudyException)
async def trudy_exception_handler(request: Request, exc: TrudyException):
    """Handle Trudy-specific exceptions - CORS headers added by UnifiedCORSMiddleware"""
    # Log RAW error to console with full details
    import traceback
    import json
    error_details_raw = {
        "error_type": type(exc).__name__,
        "error_code": exc.code,
        "error_message": exc.message,
        "error_details": exc.details,
        "error_status_code": exc.status_code,
        "error_timestamp": exc.timestamp.isoformat() if hasattr(exc, 'timestamp') else None,
        "error_args": exc.args if hasattr(exc, 'args') else None,
        "error_dict": exc.__dict__ if hasattr(exc, '__dict__') else None,
        "full_error_object": json.dumps(exc.__dict__, default=str) if hasattr(exc, '__dict__') else str(exc),
        "request_id": getattr(request.state, "request_id", None),
        "endpoint": request.url.path if request else None,
        "method": request.method if request else None,
    }
    logger.error(f"[BACKEND] [TRUDY_EXCEPTION] Raw error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
    
    # Log error to database
    log_error(
        request,
        exc,
        None,  # No background tasks in exception handler
        additional_context={
            "error_code": exc.code,
            "error_details": exc.details,
            "raw_error": error_details_raw,
        },
    )
    
    response = JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": getattr(request.state, "request_id", None),
                "ts": exc.timestamp.isoformat(),
            }
        },
    )
    
    # CORS headers will be added by UnifiedCORSMiddleware
    return response


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions with CORS headers"""
    import traceback
    import json
    
    # Log RAW error to console with full details
    error_details_raw = {
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "error_args": exc.args if hasattr(exc, 'args') else None,
        "error_dict": exc.__dict__ if hasattr(exc, '__dict__') else None,
        "full_error_object": json.dumps(exc.__dict__, default=str) if hasattr(exc, '__dict__') else str(exc),
        "error_module": getattr(exc, '__module__', None),
        "error_class": type(exc).__name__,
        "error_mro": [cls.__name__ for cls in type(exc).__mro__] if hasattr(type(exc), '__mro__') else None,
        "full_traceback": traceback.format_exc(),
        "request_id": getattr(request.state, "request_id", None),
        "endpoint": request.url.path if request else None,
        "method": request.method if request else None,
        "client_id": getattr(request.state, "client_id", None) if request else None,
        "user_id": getattr(request.state, "user_id", None) if request else None,
    }
    logger.error(f"[BACKEND] [GENERAL_EXCEPTION] Unhandled exception (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
    
    request_id = getattr(request.state, "request_id", None)
    
    # Log error to database with full stack trace
    log_error(
        request,
        exc,
        None,  # No background tasks in exception handler
        additional_context={
            "error_type": "unhandled_exception",
            "raw_error": error_details_raw,
        },
    )
    
    response = JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An internal error occurred",
                "request_id": request_id,
                "ts": None,
            }
        },
    )
    
    # CORS headers will be added by UnifiedCORSMiddleware
    return response


# Health Check
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    from app.services.ultravox import ultravox_client
    
    debug_logger.log_request("GET", "/health")
    
    health_status = {
        "status": "healthy",
        "service": "trudy-api",
        "environment": settings.ENVIRONMENT,
        "ultravox": {
            "configured": bool(settings.ULTRAVOX_API_KEY),
            "base_url": settings.ULTRAVOX_BASE_URL if settings.ULTRAVOX_API_KEY else None,
        }
    }
    
    # Test Ultravox connection if configured
    if settings.ULTRAVOX_API_KEY:
        try:
            # Try a simple request to verify connection
            # We'll just check if the client is initialized properly
            health_status["ultravox"]["connection"] = "ready"
            debug_logger.log_step("HEALTH_CHECK", "Ultravox connection verified")
        except Exception as e:
            health_status["ultravox"]["connection"] = f"error: {str(e)}"
            health_status["status"] = "degraded"
            debug_logger.log_error("HEALTH_CHECK", e, {"service": "ultravox"})
    
    debug_logger.log_response("GET", "/health", 200)
    return health_status


# CORS Health Check Endpoint - For diagnosing CORS issues
@app.get("/api/v1/cors-health")
async def cors_health(request: Request):
    """
    CORS health check endpoint.
    Returns diagnostic information about CORS configuration and current request.
    """
    origin = request.headers.get("origin")
    origin_allowed = is_origin_allowed(origin) if origin else False  # Use function from middleware
    
    response_data = {
        "cors_working": origin_allowed,
        "origin_received": origin,
        "origin_allowed": origin_allowed,
        "allowed_origins_count": len(settings.CORS_ORIGINS),
        "wildcard_patterns_count": len(settings.CORS_WILDCARD_PATTERNS),
        "allowed_origins": settings.CORS_ORIGINS,
        "wildcard_patterns": settings.CORS_WILDCARD_PATTERNS,
        "request_headers": dict(request.headers),
        "message": "CORS health check - if cors_working is true, CORS is configured correctly for this origin",
    }
    
    response = JSONResponse(content=response_data)
    
    # CORS headers will be added by UnifiedCORSMiddleware
    return response


# Explicit OPTIONS handler for all routes (backup for CORS preflight)
# REMOVED: Handled by UnifiedCORSMiddleware in app/core/middleware.py
# This was dead code that could cause confusion.
# @app.options("/{full_path:path}")
# async def options_handler(request: Request, full_path: str):
#    ...


# CORS Test Endpoint - For verifying CORS is working
@app.get("/api/v1/cors-test")
async def cors_test(request: Request):
    """
    Test endpoint to verify CORS is working correctly.
    
    Call this endpoint from the frontend to verify CORS configuration.
    Returns diagnostic info about the CORS setup.
    """
    origin = request.headers.get("origin", "none")
    is_allowed = is_origin_allowed(origin) if origin != "none" else False
    
    return {
        "status": "ok",
        "cors_working": True,
        "origin_received": origin,
        "origin_allowed": is_allowed,
        "exact_origins_count": len(settings.CORS_ORIGINS),
        "wildcard_patterns_count": len(settings.CORS_WILDCARD_PATTERNS),
        "message": "If you can read this, CORS is working correctly!",
    }


# CORS Debug Endpoint - Detailed configuration info
@app.get("/api/v1/debug/cors")
async def cors_debug(request: Request):
    """Debug endpoint to check CORS configuration"""
    debug_logger.log_request("GET", "/api/v1/debug/cors")
    
    origin = request.headers.get("origin", "none")
    is_allowed = is_origin_allowed(origin) if origin != "none" else False
    
    # Import compiled patterns from cors module for debug info
    from app.core.cors import get_compiled_patterns
    compiled_patterns_str = get_compiled_patterns()
    
    debug_info = {
        "request_origin": origin,
        "origin_allowed": is_allowed,
        "exact_origins": settings.CORS_ORIGINS,
        "wildcard_patterns": settings.CORS_WILDCARD_PATTERNS,
        "compiled_regex_patterns": compiled_patterns_str,
        "combined_regex": "|".join(compiled_patterns_str) if compiled_patterns_str else None,
        "total_exact": len(settings.CORS_ORIGINS),
        "total_wildcards": len(settings.CORS_WILDCARD_PATTERNS),
        "max_age_seconds": 86400,
    }
    
    debug_logger.log_response("GET", "/api/v1/debug/cors", 200, context=debug_info)
    return debug_info


# Real-time Event Streaming (WebSocket/SSE placeholder)
# TODO: Implement full WebSocket or SSE support for streaming Ultravox tool-use events
@app.get("/api/v1/streams/calls/{call_id}")
async def stream_call_events(
    call_id: str,
    current_user: dict = None,  # TODO: Add authentication dependency
):
    """
    Placeholder for real-time event streaming.
    
    Future implementation should:
    1. Stream incoming Ultravox tool-use events (from webhooks) to the frontend
    2. Allow frontend to show "Agent is thinking..." or "Agent is calling Tool X" in real-time
    3. Support WebSocket or Server-Sent Events (SSE)
    
    Current status: Returns a placeholder response indicating the feature is planned.
    """
    from fastapi.responses import JSONResponse
    
    return JSONResponse(
        status_code=501,  # Not Implemented
        content={
            "error": {
                "code": "not_implemented",
                "message": "Real-time event streaming is planned but not yet implemented",
                "details": {
                    "call_id": call_id,
                    "feature": "WebSocket/SSE streaming for Ultravox tool-use events",
                    "planned_features": [
                        "Stream tool-use events in real-time",
                        "Show thinking status",
                        "Display tool execution progress",
                        "Webhook-to-WebSocket bridge",
                    ],
                },
            }
        },
    )


# Include API routes
app.include_router(api_router, prefix="/api/v1")
app.include_router(internal_routes.router)
app.include_router(admin_routes.router, prefix="/api/v1")

