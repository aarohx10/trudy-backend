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
        client_id = current_user.get("client_id")
        db = DatabaseService()
        now = datetime.utcnow()
        template_id = payload.get("template_id")
        
        # 1. Get a default voice (try to find one, otherwise use a placeholder or handle later)
        # We try to find ANY voice for this client to set as default
        voices = db.select("voices", {"client_id": client_id}, limit=1)
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
        
        agent_record = {
            "id": agent_id,
            "client_id": client_id,
            "name": name,
            "description": template.get("description") if template else "Draft agent",
            "voice_id": default_voice_id if default_voice_id else str(uuid.uuid4()), # Placeholder if missing
            "system_prompt": system_prompt,
            "model": "fixie-ai/ultravox-v0_4-8k",
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
            "template_id": template_id
        }
        
        if template and template.get("category"):
             # Maybe append category to description or store it? 
             # Agent table doesn't have category.
             pass
        
        db.insert("agents", agent_record)
        logger.info(f"[AGENTS] [DRAFT] Created draft agent: {agent_id} (Template: {template_id})")
        
        return {
            "data": agent_record,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
    except Exception as e:
        logger.error(f"[AGENTS] [DRAFT] Failed to create draft agent: {str(e)}", exc_info=True)
        raise ValidationError(f"Failed to create draft agent: {str(e)}")
