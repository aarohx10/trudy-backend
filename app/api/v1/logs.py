"""
Logs API Endpoints
"""
from fastapi import APIRouter, Request, BackgroundTasks, Depends
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
import logging

from app.core.db_logging import log_to_database
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class LogEntry(BaseModel):
    """Log entry from frontend"""
    source: str = Field(..., description="'frontend' or 'backend'")
    level: str = Field(..., description="'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'")
    category: str = Field(..., description="Log category")
    message: str = Field(..., description="Log message")
    request_id: Optional[str] = None
    client_id: Optional[str] = None
    user_id: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    duration_ms: Optional[int] = None
    context: Optional[Dict[str, Any]] = None
    error_details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class LogBatch(BaseModel):
    """Batch of log entries from frontend"""
    logs: List[LogEntry] = Field(..., description="List of log entries")


@router.post("/logs")
async def ingest_logs(
    log_batch: LogBatch,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: Optional[dict] = Depends(get_current_user),
):
    """
    Ingest logs from frontend
    
    This endpoint accepts log entries from the frontend and stores them in the database.
    Supports batching for efficiency.
    """
    try:
        # Get IP address
        ip_address = None
        if request.client:
            ip_address = request.client.host
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ip_address = forwarded_for.split(",")[0].strip()
        
        # Get user agent
        user_agent = request.headers.get("User-Agent")
        
        # Get client_id and user_id from auth if available
        client_id = None
        user_id = None
        if current_user:
            client_id = current_user.get("client_id")
            user_id = current_user.get("user_id")
        
        # Process each log entry
        for log_entry in log_batch.logs:
            # Override with authenticated values if available
            final_client_id = log_entry.client_id or client_id
            final_user_id = log_entry.user_id or user_id
            final_ip_address = log_entry.ip_address or ip_address
            final_user_agent = log_entry.user_agent or user_agent
            
            # Log to database asynchronously
            background_tasks.add_task(
                log_to_database,
                source=log_entry.source,
                level=log_entry.level,
                category=log_entry.category,
                message=log_entry.message,
                request_id=log_entry.request_id,
                client_id=final_client_id,
                user_id=final_user_id,
                endpoint=log_entry.endpoint,
                method=log_entry.method,
                status_code=log_entry.status_code,
                duration_ms=log_entry.duration_ms,
                context=log_entry.context,
                error_details=log_entry.error_details,
                ip_address=final_ip_address,
                user_agent=final_user_agent,
            )
        
        return {
            "success": True,
            "logged": len(log_batch.logs),
        }
    except Exception as e:
        logger.error(f"Failed to ingest logs: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }
