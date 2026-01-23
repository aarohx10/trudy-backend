"""
Database Logging Service
Logs application events to the database for comprehensive tracking and debugging
"""
import logging
import traceback
import json
from typing import Optional, Dict, Any, List
from fastapi import Request
from fastapi.background import BackgroundTasks
from app.core.config import settings
from app.core.database import DatabaseAdminService

logger = logging.getLogger(__name__)

# Global admin database service for logging
_admin_db = None


def get_admin_db() -> DatabaseAdminService:
    """Get or create admin database service"""
    global _admin_db
    if _admin_db is None:
        _admin_db = DatabaseAdminService()
    return _admin_db


def sanitize_data(data: Any, sensitive_keys: Optional[List[str]] = None) -> Any:
    """
    Sanitize sensitive data from logs
    
    Args:
        data: Data to sanitize (dict, list, or primitive)
        sensitive_keys: List of keys to redact (default: common sensitive keys)
    
    Returns:
        Sanitized data
    """
    if sensitive_keys is None:
        sensitive_keys = [
            "password", "token", "secret", "api_key", "authorization",
            "access_token", "refresh_token", "jwt", "bearer",
            "credit_card", "cvv", "ssn", "social_security",
            "private_key", "secret_key", "encryption_key"
        ]
    
    if isinstance(data, dict):
        sanitized = {}
        for key, value in data.items():
            key_lower = key.lower()
            # Check if key contains any sensitive keyword
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_data(value, sensitive_keys)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_data(item, sensitive_keys) for item in data]
    else:
        return data


def truncate_string(value: str, max_length: int = 10000) -> str:
    """Truncate string if too long"""
    if not isinstance(value, str):
        return value
    if len(value) > max_length:
        return value[:max_length] + f"... [truncated {len(value) - max_length} chars]"
    return value


async def log_to_database(
    source: str,
    level: str,
    category: str,
    message: str,
    request_id: Optional[str] = None,
    client_id: Optional[str] = None,
    user_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None,
    error_details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> bool:
    """
    Log an event to the database
    
    Args:
        source: 'frontend' or 'backend'
        level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
        category: Log category (e.g., 'api_request', 'api_response', 'error', 'auth')
        message: Log message
        request_id: Request correlation ID
        client_id: Client/tenant ID
        user_id: User ID
        endpoint: API endpoint or frontend route
        method: HTTP method
        status_code: HTTP status code
        duration_ms: Request duration in milliseconds
        context: Additional context data (will be sanitized)
        error_details: Error details (stack trace, error type, etc.)
        ip_address: Client IP address
        user_agent: User agent string
    
    Returns:
        True if logged successfully, False otherwise
    """
    if not settings.ENABLE_DB_LOGGING:
        return False
    
    try:
        # Sanitize context data
        sanitized_context = sanitize_data(context) if context else {}
        
        # Truncate long strings
        message = truncate_string(message, max_length=5000)
        if error_details:
            error_details = {k: truncate_string(str(v), max_length=10000) if isinstance(v, str) else v 
                           for k, v in error_details.items()}
        
        # Prepare log entry
        log_entry = {
            "source": source,
            "level": level,
            "category": category,
            "message": message,
            "request_id": request_id,
            "client_id": client_id,
            "user_id": user_id,
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "context": sanitized_context,
            "error_details": error_details,
            "ip_address": ip_address,
            "user_agent": truncate_string(user_agent, max_length=500) if user_agent else None,
        }
        
        # Remove None values
        log_entry = {k: v for k, v in log_entry.items() if v is not None}
        
        # Insert into database (using admin service to bypass RLS)
        admin_db = get_admin_db()
        admin_db.insert("application_logs", log_entry)
        
        return True
    except Exception as e:
        # Fallback to console logging if database write fails
        logger.error(f"Failed to write log to database: {e}", exc_info=True)
        return False


def log_request(
    request: Request,
    background_tasks: Optional[BackgroundTasks] = None,
    request_body: Optional[Any] = None,
) -> None:
    """
    Log an incoming API request
    
    Args:
        request: FastAPI Request object
        background_tasks: Optional background tasks for async logging
        request_body: Optional request body (will be sanitized)
    """
    if not settings.ENABLE_DB_LOGGING:
        return
    
    request_id = getattr(request.state, "request_id", None)
    client_id = getattr(request.state, "client_id", None)
    user_id = getattr(request.state, "user_id", None)
    
    # Get IP address
    ip_address = None
    if request.client:
        ip_address = request.client.host
    # Check for forwarded IP
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    
    # Get user agent
    user_agent = request.headers.get("User-Agent")
    
    # Sanitize request body
    sanitized_body = None
    if request_body:
        try:
            if isinstance(request_body, (dict, list)):
                sanitized_body = sanitize_data(request_body)
            elif isinstance(request_body, str):
                # Try to parse as JSON
                try:
                    parsed = json.loads(request_body)
                    sanitized_body = sanitize_data(parsed)
                except:
                    sanitized_body = truncate_string(request_body, max_length=5000)
            else:
                sanitized_body = str(request_body)
        except Exception:
            sanitized_body = "[Unable to parse request body]"
    
    context = {
        "query_params": dict(request.query_params) if request.query_params else None,
        "request_body": sanitized_body,
        "headers": {
            k: v for k, v in request.headers.items() 
            if k.lower() not in ["authorization", "cookie", "x-api-key"]
        },
    }
    
    if background_tasks:
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="INFO",
            category="api_request",
            message=f"{request.method} {request.url.path}",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=request.url.path,
            method=request.method,
            context=context,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    else:
        # Synchronous fallback (not recommended for production)
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(
                log_to_database(
                    source="backend",
                    level="INFO",
                    category="api_request",
                    message=f"{request.method} {request.url.path}",
                    request_id=request_id,
                    client_id=client_id,
                    user_id=user_id,
                    endpoint=request.url.path,
                    method=request.method,
                    context=context,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )
        except Exception:
            pass


def log_response(
    request: Request,
    status_code: int,
    duration_ms: int,
    background_tasks: Optional[BackgroundTasks] = None,
    response_body: Optional[Any] = None,
) -> None:
    """
    Log an API response
    
    Args:
        request: FastAPI Request object
        status_code: HTTP status code
        duration_ms: Request duration in milliseconds
        background_tasks: Optional background tasks for async logging
        response_body: Optional response body (will be sanitized and truncated)
    """
    if not settings.ENABLE_DB_LOGGING:
        return
    
    request_id = getattr(request.state, "request_id", None)
    client_id = getattr(request.state, "client_id", None)
    user_id = getattr(request.state, "user_id", None)
    
    # Determine log level based on status code
    if status_code >= 500:
        level = "ERROR"
    elif status_code >= 400:
        level = "WARNING"
    else:
        level = "INFO"
    
    # Sanitize and truncate response body
    sanitized_body = None
    if response_body:
        try:
            if isinstance(response_body, (dict, list)):
                sanitized_body = sanitize_data(response_body)
            elif isinstance(response_body, str):
                try:
                    parsed = json.loads(response_body)
                    sanitized_body = sanitize_data(parsed)
                except:
                    sanitized_body = truncate_string(response_body, max_length=5000)
            else:
                sanitized_body = str(response_body)
        except Exception:
            sanitized_body = "[Unable to parse response body]"
    
    context = {
        "response_body": sanitized_body,
    }
    
    if background_tasks:
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level=level,
            category="api_response",
            message=f"{request.method} {request.url.path} - {status_code}",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=request.url.path,
            method=request.method,
            status_code=status_code,
            duration_ms=duration_ms,
            context=context,
        )
    else:
        # Synchronous fallback
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(
                log_to_database(
                    source="backend",
                    level=level,
                    category="api_response",
                    message=f"{request.method} {request.url.path} - {status_code}",
                    request_id=request_id,
                    client_id=client_id,
                    user_id=user_id,
                    endpoint=request.url.path,
                    method=request.method,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    context=context,
                )
            )
        except Exception:
            pass


def log_error(
    request: Optional[Request],
    error: Exception,
    background_tasks: Optional[BackgroundTasks] = None,
    additional_context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log an error with full stack trace
    
    Args:
        request: Optional FastAPI Request object
        error: Exception object
        background_tasks: Optional background tasks for async logging
        additional_context: Additional context to include
    """
    if not settings.ENABLE_DB_LOGGING:
        return
    
    request_id = None
    client_id = None
    user_id = None
    endpoint = None
    method = None
    ip_address = None
    user_agent = None
    
    if request:
        request_id = getattr(request.state, "request_id", None)
        client_id = getattr(request.state, "client_id", None)
        user_id = getattr(request.state, "user_id", None)
        endpoint = request.url.path
        method = request.method
        if request.client:
            ip_address = request.client.host
        user_agent = request.headers.get("User-Agent")
    
    # Get stack trace and RAW error details
    error_type = type(error).__name__
    error_message = str(error)
    stack_trace = traceback.format_exc()
    
    # Log RAW error to console with full details
    import json
    error_details_raw = {
        "error_type": error_type,
        "error_message": error_message,
        "error_args": error.args if hasattr(error, 'args') else None,
        "error_dict": error.__dict__ if hasattr(error, '__dict__') else None,
        "full_error_object": json.dumps(error.__dict__, default=str) if hasattr(error, '__dict__') else str(error),
        "error_module": getattr(error, '__module__', None),
        "error_class": error_type,
        "error_mro": [cls.__name__ for cls in type(error).__mro__] if hasattr(type(error), '__mro__') else None,
        "stack_trace": stack_trace,
        "request_id": request_id,
        "client_id": client_id,
        "user_id": user_id,
        "endpoint": endpoint,
        "method": method,
        "additional_context": additional_context,
    }
    
    # Log to console with RAW error
    logger.error(f"[DB_LOGGING] Error logged (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
    
    error_details = {
        "error_type": error_type,
        "error_message": error_message,
        "stack_trace": stack_trace,
        "raw_error": error_details_raw,  # Include raw error in database log
    }
    
    context = additional_context or {}
    
    if background_tasks:
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="ERROR",
            category="error",
            message=f"Error: {error_type} - {error_message}",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=endpoint,
            method=method,
            context=context,
            error_details=error_details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    else:
        # Synchronous fallback
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(
                log_to_database(
                    source="backend",
                    level="ERROR",
                    category="error",
                    message=f"Error: {error_type} - {error_message}",
                    request_id=request_id,
                    client_id=client_id,
                    user_id=user_id,
                    endpoint=endpoint,
                    method=method,
                    context=context,
                    error_details=error_details,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )
        except Exception:
            pass


def log_user_action(
    request: Optional[Request],
    action: str,
    background_tasks: Optional[BackgroundTasks] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log a user action
    
    Args:
        request: Optional FastAPI Request object
        action: Action description
        background_tasks: Optional background tasks for async logging
        context: Additional context
    """
    if not settings.ENABLE_DB_LOGGING:
        return
    
    request_id = None
    client_id = None
    user_id = None
    endpoint = None
    method = None
    
    if request:
        request_id = getattr(request.state, "request_id", None)
        client_id = getattr(request.state, "client_id", None)
        user_id = getattr(request.state, "user_id", None)
        endpoint = request.url.path
        method = request.method
    
    if background_tasks:
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="INFO",
            category="user_action",
            message=action,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=endpoint,
            method=method,
            context=context or {},
        )
    else:
        # Synchronous fallback
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(
                log_to_database(
                    source="backend",
                    level="INFO",
                    category="user_action",
                    message=action,
                    request_id=request_id,
                    client_id=client_id,
                    user_id=user_id,
                    endpoint=endpoint,
                    method=method,
                    context=context or {},
                )
            )
        except Exception:
            pass


def log_database_operation(
    operation: str,
    table: str,
    background_tasks: Optional[BackgroundTasks] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log a database operation
    
    Args:
        operation: Operation type (SELECT, INSERT, UPDATE, DELETE)
        table: Table name
        background_tasks: Optional background tasks for async logging
        context: Additional context
    """
    if not settings.ENABLE_DB_LOGGING:
        return
    
    message = f"Database {operation} on {table}"
    
    if background_tasks:
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="DEBUG",
            category="database",
            message=message,
            context=context or {},
        )
    else:
        # Synchronous fallback
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(
                log_to_database(
                    source="backend",
                    level="DEBUG",
                    category="database",
                    message=message,
                    context=context or {},
                )
            )
        except Exception:
            pass
