"""
Debug Logging Utility
Centralized logging system that can be easily toggled or removed
"""
import logging
import os
from typing import Any, Dict, Optional
from datetime import datetime

# Check if debug logging is enabled (default: true for dev, false for prod)
ENABLE_DEBUG_LOGGING = os.getenv("ENABLE_DEBUG_LOGGING", "true").lower() == "true"

logger = logging.getLogger(__name__)


class DebugLogger:
    """Centralized debug logger that can be easily disabled or removed"""
    
    def __init__(self, enabled: bool = ENABLE_DEBUG_LOGGING):
        self.enabled = enabled
    
    def _format_message(self, category: str, step: str, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Format debug log message"""
        parts = [f"[DEBUG]", f"[{category}]", f"[{step}]", message]
        if context:
            context_str = " | ".join([f"{k}={v}" for k, v in context.items()])
            parts.append(f"| {context_str}")
        return " ".join(parts)
    
    def _log(self, level: int, category: str, step: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Internal logging method"""
        if not self.enabled:
            return
        
        formatted = self._format_message(category, step, message, context)
        logger.log(level, formatted)
    
    def log_step(self, step: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log a general step"""
        self._log(logging.INFO, "STEP", step, message, context)
    
    def log_request(self, method: str, endpoint: str, context: Optional[Dict[str, Any]] = None):
        """Log an incoming request"""
        ctx = {"method": method, "endpoint": endpoint}
        if context:
            ctx.update(context)
        self._log(logging.INFO, "REQUEST", "INCOMING", f"{method} {endpoint}", ctx)
    
    def log_response(self, method: str, endpoint: str, status_code: int, duration_ms: Optional[int] = None, context: Optional[Dict[str, Any]] = None):
        """Log a response"""
        ctx = {"method": method, "endpoint": endpoint, "status": status_code}
        if duration_ms is not None:
            ctx["duration_ms"] = duration_ms
        if context:
            ctx.update(context)
        self._log(logging.INFO, "RESPONSE", "SENT", f"{method} {endpoint} - {status_code}", ctx)
    
    def log_error(self, step: str, error: Exception, context: Optional[Dict[str, Any]] = None):
        """Log an error"""
        ctx = {"error": str(error), "error_type": type(error).__name__}
        if context:
            ctx.update(context)
        self._log(logging.ERROR, "ERROR", step, f"Error in {step}: {error}", ctx)
    
    def log_cors(self, origin: str, allowed: bool, match_type: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        """Log CORS decision"""
        ctx = {"origin": origin, "allowed": allowed}
        if match_type:
            ctx["match_type"] = match_type
        if context:
            ctx.update(context)
        status = "ALLOWED" if allowed else "DENIED"
        self._log(logging.INFO, "CORS", "CHECK", f"CORS {status} for origin: {origin}", ctx)
    
    def log_auth(self, step: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log authentication step"""
        self._log(logging.INFO, "AUTH", step, message, context)
    
    def log_db(self, operation: str, table: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        """Log database operation"""
        ctx = {"operation": operation}
        if table:
            ctx["table"] = table
        if context:
            ctx.update(context)
        self._log(logging.INFO, "DB", operation, f"Database {operation}" + (f" on {table}" if table else ""), ctx)
    
    def log_api_call(self, service: str, endpoint: str, method: str = "GET", context: Optional[Dict[str, Any]] = None):
        """Log external API call"""
        ctx = {"service": service, "endpoint": endpoint, "method": method}
        if context:
            ctx.update(context)
        self._log(logging.INFO, "API", "EXTERNAL", f"{service} {method} {endpoint}", ctx)


# Global instance
debug_logger = DebugLogger()

