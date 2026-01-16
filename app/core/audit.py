"""
Audit Logging Service
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.database import DatabaseAdminService

logger = logging.getLogger(__name__)


async def log_audit_event(
    action: str,
    table_name: str,
    record_id: str,
    user_id: str,
    client_id: str,
    diff: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log audit event to database
    
    Args:
        action: Action type (INSERT, UPDATE, DELETE)
        table_name: Name of the table
        record_id: ID of the record
        user_id: User ID (Auth0 sub)
        client_id: Client ID
        diff: Before/after diff for updates
        metadata: Additional metadata
    """
    try:
        admin_db = DatabaseAdminService()
        
        audit_data = {
            "action": action,
            "table_name": table_name,
            "record_id": record_id,
            "user_id": user_id,
            "client_id": client_id,
            "diff": diff or {},
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        
        admin_db.insert("audit_logs", audit_data)
        
        logger.info(
            f"Audit log: {action} on {table_name}.{record_id}",
            extra={
                "action": action,
                "table_name": table_name,
                "record_id": record_id,
                "user_id": user_id,
                "client_id": client_id,
            },
        )
        
    except Exception as e:
        # Log error but don't fail the request
        logger.error(f"Failed to log audit event: {e}")


def audit_log_middleware(action: str, table_name: str):
    """Decorator to automatically log audit events"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Extract user_id and client_id from current_user if available
            current_user = kwargs.get("current_user")
            user_id = current_user.get("user_id") if current_user else None
            client_id = current_user.get("client_id") if current_user else None
            
            # Get record_id from function arguments or return value
            record_id = kwargs.get("id") or kwargs.get(f"{table_name}_id")
            
            # Call original function
            result = await func(*args, **kwargs)
            
            # Extract record_id from result if not found in args
            if not record_id and result and isinstance(result, dict):
                data = result.get("data", {})
                if isinstance(data, dict):
                    record_id = data.get("id")
            
            # Log audit event
            if record_id and user_id and client_id:
                await log_audit_event(
                    action=action,
                    table_name=table_name,
                    record_id=record_id,
                    user_id=user_id,
                    client_id=client_id,
                )
            
            return result
        
        return wrapper
    return decorator

