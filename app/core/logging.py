"""
Logging Configuration
"""
import logging
import sys
import json
from typing import Any, Dict
from app.core.config import settings


def setup_logging():
    """Setup structured logging with enhanced formatting"""
    class StructuredFormatter(logging.Formatter):
        """Custom formatter that includes request_id and client_id in logs"""
        
        def format(self, record: logging.LogRecord) -> str:
            # Get default format
            log_data: Dict[str, Any] = {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            
            # Add extra context if available
            if hasattr(record, "request_id"):
                log_data["request_id"] = record.request_id
            if hasattr(record, "client_id"):
                log_data["client_id"] = record.client_id
            if hasattr(record, "user_id"):
                log_data["user_id"] = record.user_id
            if hasattr(record, "endpoint"):
                log_data["endpoint"] = record.endpoint
            if hasattr(record, "method"):
                log_data["method"] = record.method
            if hasattr(record, "status_code"):
                log_data["status_code"] = record.status_code
            if hasattr(record, "duration_ms"):
                log_data["duration_ms"] = record.duration_ms
            
            # Add exception info if present
            if record.exc_info:
                log_data["exception"] = self.formatException(record.exc_info)
            
            # Format as JSON for structured logging
            if settings.ENVIRONMENT == "prod":
                return json.dumps(log_data)
            else:
                # Human-readable format for dev
                parts = [f"[{log_data['timestamp']}]", f"{log_data['level']}", f"{log_data['logger']}"]
                if "request_id" in log_data:
                    parts.append(f"req_id={log_data['request_id']}")
                if "client_id" in log_data:
                    parts.append(f"client={log_data['client_id']}")
                parts.append(log_data["message"])
                return " - ".join(parts)
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper()),
        handlers=[handler],
        force=True,
    )
    
    # Set third-party loggers to WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

