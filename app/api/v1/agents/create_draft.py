"""
Create Draft Agent Endpoint
POST /agents/draft - Create a draft agent with default settings, optionally from a template
"""
from fastapi import APIRouter, Depends, Header, Body
from typing import Optional, Dict, Any
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ForbiddenError, ValidationError
from app.models.schemas import ResponseMeta
from app.services.agent import create_agent_ultravox_first, validate_agent_for_ultravox_sync

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/draft")
async def create_draft_agent(
    payload: Dict[str, Any] = Body(default={}),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create a draft agent with default settings, optionally from a template"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        client_id = current_user.get("client_id")  # Legacy field
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        now = datetime.utcnow()
        template_id = payload.get("template_id")
        
        # 1. Get a default voice (try to find one, otherwise use a placeholder or handle later)
        # We try to find ANY voice for this organization to set as default
        voices = db.select("voices", {"clerk_org_id": clerk_org_id}, order_by="created_at DESC")
        default_voice_id = None
        if voices:
            default_voice_id = voices[0]["id"]
        
        # 2. Get template if provided
        template = None
        if template_id:
            template = db.select_one("agent_templates", {"id": template_id})

        agent_id = str(uuid.uuid4())
        
        # Determine initial values
        name = "Untitled Agent"
        system_prompt = "You are a helpful assistant."
        
        if template:
            name = template.get("name", "Untitled Agent")
            system_prompt = template.get("system_prompt", system_prompt)
        
        # Build agent record
        # Note: Some fields require migration 015_expand_agents_table.sql to be run
        agent_record = {
            "id": agent_id,
            "client_id": client_id,
            "name": name,
            "description": template.get("description") if template else "Draft agent",
            "voice_id": default_voice_id,  # None if no voice available - user must select voice
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
        
        # Add template_id if provided (requires migration 015)
        if template_id:
            agent_record["template_id"] = template_id
        
        if template and template.get("category"):
             # Maybe append category to description or store it? 
             # Agent table doesn't have category.
             pass
        
        # Validate agent can be created in Ultravox
        validation_result = await validate_agent_for_ultravox_sync(agent_record, client_id)
        
        if validation_result["can_sync"]:
            # Create in Ultravox FIRST
            try:
                ultravox_response = await create_agent_ultravox_first(agent_record, client_id)
                ultravox_agent_id = ultravox_response.get("agentId")
                
                if not ultravox_agent_id:
                    raise ValueError("Ultravox did not return agentId")
                
                # Add ultravox_agent_id to agent_record
                agent_record["ultravox_agent_id"] = ultravox_agent_id
                agent_record["status"] = "active"
                
                # Now save to Supabase
                db.insert("agents", agent_record)
                logger.info(f"[AGENTS] [DRAFT] Agent created in Ultravox FIRST, then saved to DB: {agent_id}")
                
            except Exception as uv_error:
                # Ultravox creation failed - DO NOT create in DB
                import traceback
                import json
                error_details = {
                    "error_type": type(uv_error).__name__,
                    "error_message": str(uv_error),
                    "full_traceback": traceback.format_exc(),
                    "agent_id": agent_id,
                }
                logger.error(f"[AGENTS] [DRAFT] Failed to create in Ultravox FIRST (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
                # Re-raise error to return to user
                raise ValidationError(f"Failed to create agent in Ultravox: {str(uv_error)}")
        else:
            # Validation failed - voice not selected
            reason = validation_result.get("reason", "unknown")
            if reason == "voice_required":
                # No voice selected - create as draft in DB only
                agent_record["status"] = "draft"
                db.insert("agents", agent_record)
                logger.info(f"[AGENTS] [DRAFT] Agent created as draft (no voice selected): {agent_id}")
            else:
                # Other validation failure - return error
                error_msg = "; ".join(validation_result["errors"])
                raise ValidationError(f"Agent validation failed: {error_msg}")
        
        # Fetch the created agent
        created_agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        
        if not created_agent:
            raise ValidationError(f"Failed to retrieve created agent: {agent_id}")
        
        return {
            "data": created_agent,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
    except Exception as e:
        logger.error(f"[AGENTS] [DRAFT] Failed to create draft agent: {str(e)}", exc_info=True)
        raise ValidationError(f"Failed to create draft agent: {str(e)}")
