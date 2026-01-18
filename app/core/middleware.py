"""
Custom Middleware
"""
import uuid
import time
import json
from fastapi import Request, Response, BackgroundTasks
from starlette.middleware.base import BaseHTTPMiddleware
import logging
from app.core.debug_logging import debug_logger
from app.core.db_logging import log_request, log_response

logger = logging.getLogger(__name__)


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
        
        # Log to database (using background tasks if available)
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
        
        # Log to database
        if background_tasks:
            log_response(request, response.status_code, duration_ms, background_tasks, response_body)
        else:
            log_response(request, response.status_code, duration_ms, None, response_body)
        
        return response

