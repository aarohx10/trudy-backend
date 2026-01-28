"""
Get Agent Endpoint
GET /agents/{agent_id} - Get single agent
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Get single agent.
    
    CRITICAL: Filters by clerk_org_id to ensure organization-scoped access.
    """
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        
        # Filter by org_id instead of client_id
        agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})
        
        if not agent:
            raise NotFoundError("agent", agent_id)
        
        return {
            "data": agent,
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
        logger.error(f"[AGENTS] [GET] Failed to get agent (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to get agent: {str(e)}")
