"""
Delete Agent Endpoint
DELETE /agents/{agent_id} - Delete agent (deletes from both Supabase + Ultravox)
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError
from app.models.schemas import ResponseMeta
from app.services.agent import delete_agent_from_ultravox

logger = logging.getLogger(__name__)

router = APIRouter()


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete agent (deletes from both Supabase + Ultravox)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get existing agent
        existing_agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        if not existing_agent:
            raise NotFoundError("agent", agent_id)
        
        # Delete from Ultravox if we have ultravox_agent_id
        ultravox_agent_id = existing_agent.get("ultravox_agent_id")
        if ultravox_agent_id:
            try:
                await delete_agent_from_ultravox(ultravox_agent_id)
                logger.info(f"[AGENTS] [DELETE] Agent deleted from Ultravox: {ultravox_agent_id}")
            except Exception as uv_error:
                logger.warning(f"[AGENTS] [DELETE] Failed to delete agent from Ultravox (non-critical): {uv_error}", exc_info=True)
                # Continue to delete from database even if Ultravox delete fails
        
        # Delete from database
        db.delete("agents", {"id": agent_id, "client_id": client_id})
        logger.info(f"[AGENTS] [DELETE] Agent deleted from database: {agent_id}")
        
        return {
            "data": {"id": agent_id, "deleted": True},
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
    except Exception as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
            "agent_id": agent_id,
        }
        logger.error(f"[AGENTS] [DELETE] Failed to delete agent (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to delete agent: {str(e)}")
