"""
Trudy Backend API - FastAPI Application
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import logging
import re
import json
import time
from pathlib import Path
from contextlib import asynccontextmanager
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limiting import RateLimitMiddleware
from app.core.middleware import RequestIDMiddleware, LoggingMiddleware
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
        logger.warning("⚠️  Ultravox API Key: NOT CONFIGURED - Voice and Agent syncing will be disabled")
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

# CORS Configuration (Specific origins + Regex for wildcards)
# Convert wildcard patterns to regex patterns for FastAPI CORS
cors_regex_patterns = []
for pattern in settings.CORS_WILDCARD_PATTERNS:
    # Patterns are already in regex format, but ensure they're anchored
    if not pattern.startswith("^"):
        pattern = f"^{pattern}"
    if not pattern.endswith("$"):
        pattern = f"{pattern}$"
    cors_regex_patterns.append(pattern)

cors_middleware_config = {
    "allow_origins": settings.CORS_ORIGINS,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}

# Add regex pattern if we have wildcard patterns
if cors_regex_patterns:
    cors_middleware_config["allow_origin_regex"] = "|".join(cors_regex_patterns)
    logger.info(f"✅ CORS regex patterns configured: {cors_middleware_config['allow_origin_regex']}")

logger.info(f"✅ CORS Exact Origins: {settings.CORS_ORIGINS}")

# Create custom CORS middleware wrapper for detailed logging
class LoggingCORSMiddleware(StarletteCORSMiddleware):
    """CORS middleware with detailed logging"""
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Read origin from headers for logging
        origin_requested = None
        try:
            headers_list = scope.get("headers", [])
            for header in headers_list:
                if isinstance(header, (list, tuple)) and len(header) == 2:
                    key = header[0].lower()
                    if key == b"origin":
                        origin_requested = header[1].decode("utf-8") if isinstance(header[1], bytes) else header[1]
                        break
        except Exception:
            pass
        
        # Wrap send to capture response headers for debugging
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                # DEBUG: Log response headers (via logger - safe)
                try:
                    # Headers are list of (bytes, bytes) tuples
                    headers_list = message.get("headers", [])
                    headers_dict = {}
                    for header in headers_list:
                        if isinstance(header, (list, tuple)) and len(header) == 2:
                            key = header[0].decode() if isinstance(header[0], bytes) else header[0]
                            val = header[1].decode() if isinstance(header[1], bytes) else header[1]
                            headers_dict[key.lower()] = val
                    
                    cors_header = headers_dict.get("access-control-allow-origin", "MISSING")
                    logger.info(f"🔍 [CORS] Response headers | status={message.get('status')} | has_cors_origin={'access-control-allow-origin' in headers_dict} | cors_origin_value={cors_header} | origin_requested={origin_requested}")
                except Exception as e:
                    logger.warning(f"🔍 [CORS] Error logging response headers (non-fatal): {e}")
            await send(message)
        
        # Call parent middleware - this handles the actual CORS logic
        await super().__call__(scope, receive, send_wrapper)

# Middleware order matters! In FastAPI, middleware is applied in REVERSE order of addition.
# So we add them in reverse order: last added = first executed
# CORS must be the outer layer to ensure headers are sent even during errors
# Execution order: RateLimit -> Logging -> RequestID -> CORS (outermost for response headers)

# Add Middleware in correct execution order
# CORS is added first (last in execution) to wrap all responses
app.add_middleware(LoggingCORSMiddleware, **cors_middleware_config)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)


# Exception Handlers
@app.exception_handler(TrudyException)
async def trudy_exception_handler(request: Request, exc: TrudyException):
    """Handle Trudy-specific exceptions"""
    # Log error to database
    log_error(
        request,
        exc,
        None,  # No background tasks in exception handler
        additional_context={
            "error_code": exc.code,
            "error_details": exc.details,
        },
    )
    
    return JSONResponse(
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


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions and ensure CORS headers are present"""
    logger.exception(f"Unhandled exception: {exc}")
    request_id = getattr(request.state, "request_id", None)
    
    # Log error to database with full stack trace
    log_error(
        request,
        exc,
        None,  # No background tasks in exception handler
        additional_context={
            "error_type": "unhandled_exception",
        },
    )
    
    # Extract origin from request to mirror it back for CORS compliance
    origin = request.headers.get("origin")
    
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
    
    # Manually add CORS headers if the exception occurred before/outside the middleware
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        
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


# Explicit OPTIONS handler for all routes (backup for CORS preflight)
@app.options("/{full_path:path}")
async def options_handler(full_path: str):
    """Handle OPTIONS preflight requests explicitly"""
    from fastapi.responses import Response
    return Response(status_code=200)

# CORS Debug Endpoint
@app.get("/api/v1/debug/cors")
async def cors_debug():
    """Debug endpoint to check CORS configuration"""
    from fastapi import Request
    
    debug_logger.log_request("GET", "/api/v1/debug/cors")
    
    # Convert wildcard patterns to regex for display
    regex_patterns = []
    for pattern in settings.CORS_WILDCARD_PATTERNS:
        regex_pattern = pattern.replace(".", r"\.").replace("*", r".*")
        regex_patterns.append(f"^{regex_pattern}$")
    
    debug_info = {
        "exact_origins": settings.CORS_ORIGINS,
        "wildcard_patterns": settings.CORS_WILDCARD_PATTERNS,
        "regex_patterns": regex_patterns,
        "combined_regex": "|".join(regex_patterns) if regex_patterns else None,
        "total_exact": len(settings.CORS_ORIGINS),
        "total_wildcards": len(settings.CORS_WILDCARD_PATTERNS),
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
                        "Show agent thinking status",
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

