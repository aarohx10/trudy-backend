"""
Custom Middleware
"""
import uuid
import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import logging
from app.core.debug_logging import debug_logger

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
        
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
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
        
        return response

