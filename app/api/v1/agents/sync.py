"""
Sync Agent Endpoint
GET /agents/{agent_id}/sync - Sync agent with Ultravox (create or update)
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, ForbiddenError, ProviderError
from app.models.schemas import ResponseMeta
from app.services.agent import sync_agent_to_ultravox

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{agent_id}/sync")
async def sync_agent(
    agent_id: str,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Sync agent with Ultravox (create or update)"""
    # Permission check handled by require_admin_role dependency
    
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        client_id = current_user.get("client_id")  # Legacy field
        
        # Sync agent
        ultravox_response = await sync_agent_to_ultravox(agent_id, client_id)
        
        # Update database with Ultravox agent ID if it was created
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        # Filter by org_id instead of client_id
        agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})
        if agent and not agent.get("ultravox_agent_id"):
            ultravox_agent_id = ultravox_response.get("agentId")
            if ultravox_agent_id:
                # Filter by org_id instead of client_id
                db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
                    "ultravox_agent_id": ultravox_agent_id,
                    "status": "active",
                })
        
        return {
            "data": {
                "agent_id": agent_id,
                "ultravox_agent_id": ultravox_response.get("agentId"),
                "synced": True,
            },
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
        logger.error(f"[AGENTS] [SYNC] Failed to sync agent (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ForbiddenError, ProviderError)):
            raise
        raise ValidationError(f"Failed to sync agent: {str(e)}")
