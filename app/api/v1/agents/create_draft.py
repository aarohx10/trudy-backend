"""
Create Draft Agent Endpoint
POST /agents/draft - Create a draft agent with default settings, optionally from a template
"""
from fastapi import APIRouter, Depends, Body
from typing import Dict, Any
from datetime import datetime
import uuid
import logging

from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError
from app.models.schemas import ResponseMeta
from app.services.agent import create_agent_ultravox_first

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/draft")
async def create_draft_agent(
    payload: Dict[str, Any] = Body(default={}),
    current_user: dict = Depends(require_admin_role),
):
    """Create a draft agent with default settings, optionally from a template"""
    try:
        # TEMPORARY: Allow NULL clerk_org_id for testing (remove restrictions in DB first)
        clerk_org_id = payload.get("clerk_org_id")
        if clerk_org_id:
            clerk_org_id = str(clerk_org_id).strip()
        
        db = DatabaseService(org_id=clerk_org_id)
        now = datetime.utcnow()
        template_id = payload.get("template_id")
        
        # Get default voice if available (skip if no clerk_org_id)
        default_voice_id = None
        if clerk_org_id:
            voices = db.select("voices", {"clerk_org_id": clerk_org_id}, order_by="created_at DESC")
            default_voice_id = voices[0]["id"] if voices else None
        
        # Get template if provided
        template = None
        if template_id:
            template = db.select_one("agent_templates", {"id": template_id})

        agent_id = str(uuid.uuid4())
        
        # Determine initial values
        name = template.get("name", "Untitled Agent") if template else "Untitled Agent"
        system_prompt = template.get("system_prompt", "You are a helpful assistant.") if template else "You are a helpful assistant."
        
        # Create agent record
        agent_record = {
            "id": agent_id,
            "name": name,
            "description": template.get("description") if template else "Draft agent",
            "voice_id": default_voice_id,
            "system_prompt": system_prompt,
            "model": "ultravox-v0.6",
            "tools": [],
            "knowledge_bases": [],
            "status": "draft",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "temperature": 0.3,
            "language_hint": "en-US",
            "initial_output_medium": "MESSAGE_MEDIUM_VOICE",
            "recording_enabled": False,
            "join_timeout": "30s",
            "max_duration": "3600s",
        }
        
        if template_id:
            agent_record["template_id"] = template_id
        
        # Only include clerk_org_id if it exists (can be NULL for testing)
        if clerk_org_id:
            agent_record["clerk_org_id"] = clerk_org_id
        
        db.insert("agents", agent_record)
        
        # Try Ultravox sync in background (non-blocking)
        try:
            ultravox_response = await create_agent_ultravox_first(agent_record, clerk_org_id)
            ultravox_agent_id = ultravox_response.get("agentId")
            if ultravox_agent_id:
                db.update("agents", {"id": agent_id}, {
                    "ultravox_agent_id": ultravox_agent_id,
                    "status": "active"
                })
        except Exception as e:
            logger.warning(f"[AGENTS] [DRAFT] Ultravox creation failed (non-critical): {e}")
        
        # Re-fetch the created agent
        created_agent = db.select_one("agents", {"id": agent_id})
        
        if not created_agent:
            raise ValidationError(f"Failed to retrieve agent after creation: {agent_id}")
        
        return {
            "data": created_agent,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"[AGENTS] [DRAFT] Failed to create draft agent: {e}", exc_info=True)
        raise ValidationError(f"Failed to create draft agent: {str(e)}")
