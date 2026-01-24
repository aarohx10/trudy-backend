"""
Custom Middleware
"""
import uuid
import time
import json
from fastapi import Request, Response, BackgroundTasks
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
import logging
from app.core.debug_logging import debug_logger
from app.core.db_logging import log_request, log_response
from app.core.cors import is_origin_allowed, get_cors_headers

logger = logging.getLogger(__name__)


class UnifiedCORSMiddleware(BaseHTTPMiddleware):
    """
    Unified CORS middleware - SINGLE SOURCE OF TRUTH for all CORS handling.
    
    This middleware wraps ALL responses (success, error, streamed, exception handlers)
    and ensures CORS headers are ALWAYS added for allowed origins.
    
    MASTER FIX: Handles OPTIONS preflight requests instantly without processing.
    This prevents timeouts during file uploads when browsers send preflight requests.
    
    This replaces all manual CORS header injections throughout the codebase.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Extract origin from request headers
        origin = request.headers.get("origin")
        origin_allowed = is_origin_allowed(origin) if origin else False
        
        # Store origin in request state for potential use by other middleware
        request.state.cors_origin = origin
        request.state.cors_allowed = origin_allowed
        
        # MASTER FIX: Handle OPTIONS preflight requests instantly
        # Browsers send OPTIONS before POST/PUT with large files
        # If this takes too long, the browser times out and reports CORS error
        if request.method == "OPTIONS":
            request_headers = request.headers.get("access-control-request-headers")
            cors_headers = get_cors_headers(origin, request_headers) if origin else {}
            response = StarletteResponse(
                status_code=204,  # No Content
                headers=cors_headers
            )
            logger.debug(f"[CORS] OPTIONS preflight handled instantly | origin={origin} | allowed={origin_allowed}")
            return response
        
        try:
            # Process request
            response = await call_next(request)
        except Exception as e:
            # Even exceptions need CORS headers
            # Create a basic error response
            from fastapi.responses import JSONResponse
            response = JSONResponse(
                status_code=500,
                content={"error": {"code": "internal_error", "message": "An internal error occurred"}}
            )
            # We'll add CORS headers below
        
        # CRITICAL: Add CORS headers to ALL responses (success, error, streamed, etc.)
        # This ensures 413 Payload Too Large, 504 Gateway Timeout, etc. all have CORS headers
        if origin and origin_allowed:
            # For non-OPTIONS requests, we technically don't need to reflect Access-Control-Allow-Headers,
            # but getting them consistent doesn't hurt.
            # However, for simple requests, access-control-request-headers might not be present.
            # We'll rely on the default behavior or reflect if present (rare for simple response)
            request_headers = request.headers.get("access-control-request-headers")
            cors_headers = get_cors_headers(origin, request_headers)
            for key, value in cors_headers.items():
                response.headers[key] = value
            logger.debug(f"[CORS] Added headers | origin={origin} | status={response.status_code}")
        elif origin:
            logger.warning(f"[CORS] Origin not allowed | origin={origin} | status={response.status_code}")
        
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Add request ID to each request"""
    
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        debug_logger.log_step("REQUEST_ID", f"Generated request ID: {request_id}", {
            "request_id": request_id,
            "endpoint": request.url.path,
            "method": request.method,
        })
        
        # Add request ID to response headers
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests with enhanced context"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        request_id = getattr(request.state, "request_id", None)
        
        # Extract client_id and user_id from JWT if available (set by auth middleware)
        client_id = getattr(request.state, "client_id", None)
        user_id = getattr(request.state, "user_id", None)
        
        # Capture request body for POST/PUT/PATCH requests
        request_body = None
        if request.method in ["POST", "PUT", "PATCH"]:
            try:
                body = await request.body()
                if body:
                    try:
                        request_body = json.loads(body.decode())
                    except:
                        request_body = body.decode()[:1000]  # Truncate if not JSON
                # Recreate request body for downstream handlers
                async def receive():
                    return {"type": "http.request", "body": body}
                request._receive = receive
            except Exception:
                pass  # Ignore errors reading body
        
        # Log request with debug logger
        debug_logger.log_request(
            request.method,
            request.url.path,
            {
                "request_id": request_id,
                "client_id": client_id,
                "user_id": user_id,
                "client_ip": request.client.host if request.client else None,
                "query_params": str(request.query_params) if request.query_params else None,
            }
        )
        
        # Also log with standard logger
        logger.info(
            f"Request: {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "client_id": client_id,
                "user_id": user_id,
                "method": request.method,
                "endpoint": request.url.path,
                "client_ip": request.client.host if request.client else None,
            },
        )
        
        # Skip logging for high-frequency endpoints that don't need tracking
        skip_logging_endpoints = [
            "/api/v1/auth/me",  # Clerk auth check - too frequent
            "/health",  # Health checks
        ]
        
        should_log = not any(request.url.path == endpoint for endpoint in skip_logging_endpoints)
        
        # Log to database (using background tasks if available)
        if should_log:
            background_tasks = getattr(request.state, "background_tasks", None)
            if background_tasks:
                log_request(request, background_tasks, request_body)
            else:
                log_request(request, None, request_body)
        
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Capture response body for errors
        response_body = None
        if response.status_code >= 400:
            try:
                # Read response body if it's an error
                body_bytes = b""
                async for chunk in response.body_iterator:
                    body_bytes += chunk
                if body_bytes:
                    try:
                        response_body = json.loads(body_bytes.decode())
                    except:
                        response_body = body_bytes.decode()[:5000]  # Truncate
                # Recreate response with body
                from starlette.responses import Response as StarletteResponse
                response = StarletteResponse(
                    content=body_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            except Exception:
                pass  # Ignore errors reading response body
        
        # Log response with debug logger
        debug_logger.log_response(
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            {
                "request_id": request_id,
                "client_id": client_id,
                "user_id": user_id,
            }
        )
        
        # Also log with standard logger
        logger.info(
            f"Response: {request.method} {request.url.path} - {response.status_code}",
            extra={
                "request_id": request_id,
                "client_id": client_id,
                "user_id": user_id,
                "method": request.method,
                "endpoint": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        
        # Skip logging for high-frequency endpoints that don't need tracking
        skip_logging_endpoints = [
            "/api/v1/auth/me",  # Clerk auth check - too frequent
            "/health",  # Health checks
        ]
        
        should_log = not any(request.url.path == endpoint for endpoint in skip_logging_endpoints)
        
        # Log to database
        if should_log:
            if background_tasks:
                log_response(request, response.status_code, duration_ms, background_tasks, response_body)
            else:
                log_response(request, response.status_code, duration_ms, None, response_body)
        
        return response

