"""
Create Draft Agent Endpoint
POST /agents/draft - Create a draft agent with default settings, optionally from a template
"""
from fastapi import APIRouter, Depends, Body, Request
from typing import Dict, Any
from datetime import datetime
import uuid
import logging
import json

from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError
from app.models.schemas import ResponseMeta
from app.services.agent import create_agent_ultravox_first

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/draft")
async def create_draft_agent(
    request: Request,
    payload: Dict[str, Any] = Body(default={}),
    current_user: dict = Depends(require_admin_role),
):
    """Create a draft agent with default settings, optionally from a template"""
    try:
        # ============================================================
        # SIMPLIFIED LOGIC: Extract payload, get org_id, insert
        # ============================================================
        
        # 1. Get Payload (Robustly)
        # Try manual parsing first to avoid FastAPI Body() issues
        data = {}
        try:
            raw_body = await request.body()
            if raw_body:
                data = json.loads(raw_body.decode('utf-8'))
        except Exception:
            pass
            
        # Fallback to FastAPI parsed payload if manual failed
        if not data and payload:
            data = payload
            
        # 2. Get Org ID
        # Priority: Payload -> Current User -> Error
        clerk_org_id = data.get("clerk_org_id") or current_user.get("clerk_org_id")
        
        if not clerk_org_id:
            logger.error(f"[AGENTS] [DRAFT] Missing clerk_org_id. Payload keys: {list(data.keys())}")
            raise ValidationError("Missing organization ID")
            
        clerk_org_id = str(clerk_org_id).strip()
        if not clerk_org_id:
            raise ValidationError("Organization ID cannot be empty")
            
        logger.info(f"[AGENTS] [DRAFT] Creating agent for org: {clerk_org_id}")

        # 3. Prepare Data
        db = DatabaseService(org_id=clerk_org_id)
        now = datetime.utcnow()
        template_id = data.get("template_id")
        
        # Get default voice
        default_voice_id = None
        voices = db.select("voices", {"clerk_org_id": clerk_org_id}, order_by="created_at DESC")
        default_voice_id = voices[0]["id"] if voices else None
        
        # Get template
        template = None
        if template_id:
            template = db.select_one("agent_templates", {"id": template_id})

        agent_id = str(uuid.uuid4())
        
        # Determine initial values
        name = template.get("name", "Untitled Agent") if template else "Untitled Agent"
        system_prompt = template.get("system_prompt", "You are a helpful assistant.") if template else "You are a helpful assistant."
        
        # 4. Create Record
        agent_record = {
            "id": agent_id,
            "clerk_org_id": clerk_org_id,  # ALWAYS INCLUDED
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
        
        # 5. Insert
        logger.info(f"[AGENTS] [DRAFT] Inserting agent: {agent_id}")
        db.insert("agents", agent_record)
        
        # 6. Sync (Background)
        try:
            ultravox_response = await create_agent_ultravox_first(agent_record, clerk_org_id)
            ultravox_agent_id = ultravox_response.get("agentId")
            if ultravox_agent_id:
                db.update("agents", {"id": agent_id}, {
                    "ultravox_agent_id": ultravox_agent_id,
                    "status": "active"
                })
        except Exception as e:
            logger.warning(f"[AGENTS] [DRAFT] Ultravox sync failed (non-critical): {e}")
        
        # 7. Return
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
