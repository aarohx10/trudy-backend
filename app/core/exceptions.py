"""
Custom Exceptions
"""
from datetime import datetime
from typing import Optional, Dict, Any


class TrudyException(Exception):
    """Base exception for Trudy API"""
    
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.timestamp = datetime.utcnow()
        super().__init__(self.message)


class ValidationError(TrudyException):
    """Validation error (422)"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__("validation_error", message, 422, details)


class UnauthorizedError(TrudyException):
    """Unauthorized error (401)"""
    
    def __init__(self, message: str = "Unauthorized"):
        super().__init__("unauthorized", message, 401)


class ForbiddenError(TrudyException):
    """Forbidden error (403)"""
    
    def __init__(self, message: str = "Forbidden"):
        super().__init__("forbidden", message, 403)


class NotFoundError(TrudyException):
    """Not found error (404)"""
    
    def __init__(self, resource: str, resource_id: Optional[str] = None):
        message = f"{resource} not found"
        if resource_id:
            message += f": {resource_id}"
        super().__init__("not_found", message, 404)


class ConflictError(TrudyException):
    """Conflict error (409)"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__("conflict", message, 409, details)


class PaymentRequiredError(TrudyException):
    """Payment required error (402)"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__("payment_required", message, 402, details)


class RateLimitError(TrudyException):
    """Rate limit error (429)"""
    
    def __init__(self, message: str = "Rate limit exceeded", reset_at: Optional[datetime] = None):
        details = {}
        if reset_at:
            details["reset_at"] = reset_at.isoformat()
        super().__init__("rate_limit_exceeded", message, 429, details)


class ProviderError(TrudyException):
    """Provider API error (502)"""
    
    def __init__(
        self,
        provider: str,
        message: str,
        http_status: Optional[int] = None,
        retry_after: Optional[int] = None,
    ):
        details = {"provider": provider}
        if http_status:
            details["httpStatus"] = http_status
        if retry_after:
            details["retry_after"] = retry_after
        super().__init__("provider_error", message, 502, details)

