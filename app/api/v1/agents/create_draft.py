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
        # SAFEGUARD 1: Manual JSON parsing fallback if FastAPI Body() fails
        # ============================================================
        manual_payload = {}
        try:
            raw_body = await request.body()
            if raw_body:
                raw_body_str = raw_body.decode('utf-8')
                logger.info(f"[AGENTS] [DRAFT] [DEBUG] Raw request body: {raw_body_str}")
                logger.info(f"[AGENTS] [DRAFT] [DEBUG] Body length: {len(raw_body)}")
                logger.info(f"[AGENTS] [DRAFT] [DEBUG] Content-Type: {request.headers.get('content-type')}")
                
                # Try to manually parse JSON as fallback
                try:
                    manual_payload = json.loads(raw_body_str)
                    logger.info(f"[AGENTS] [DRAFT] [DEBUG] Manual JSON parse successful: {manual_payload}")
                except json.JSONDecodeError as e:
                    logger.warning(f"[AGENTS] [DRAFT] [DEBUG] Manual JSON parse failed: {e}")
        except Exception as e:
            logger.error(f"[AGENTS] [DRAFT] [DEBUG] Failed to read raw body: {e}")
        
        # ============================================================
        # SAFEGUARD 2: Use manual_payload if FastAPI payload is empty
        # ============================================================
        effective_payload = payload if payload else manual_payload
        
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] FastAPI parsed payload: {payload}")
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] Effective payload (after fallback): {effective_payload}")
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] Payload keys: {list(effective_payload.keys())}")
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] payload.get('clerk_org_id'): {effective_payload.get('clerk_org_id')}")
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] current_user.get('clerk_org_id'): {current_user.get('clerk_org_id')}")
        
        # ============================================================
        # SAFEGUARD 3: Extract clerk_org_id with MULTIPLE fallbacks
        # Priority: effective_payload → current_user → RAISE ERROR
        # ============================================================
        clerk_org_id = effective_payload.get("clerk_org_id") or current_user.get("clerk_org_id")
        
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] clerk_org_id after extraction (before validation): {clerk_org_id}")
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] clerk_org_id type: {type(clerk_org_id)}")
        
        # ============================================================
        # SAFEGUARD 4: Explicit validation (same pattern as other endpoints)
        # ============================================================
        if not clerk_org_id:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Missing clerk_org_id in both payload and current_user")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Effective payload: {effective_payload}")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Current user keys: {list(current_user.keys())}")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Current user clerk_org_id: {current_user.get('clerk_org_id')}")
            raise ValidationError("Missing organization ID. Ensure you are authenticated and part of an organization.")
        
        # Strip whitespace and validate it's not empty
        clerk_org_id = str(clerk_org_id).strip()
        if not clerk_org_id:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] clerk_org_id is empty after stripping")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Original value: {effective_payload.get('clerk_org_id') or current_user.get('clerk_org_id')}")
            raise ValidationError("Organization ID cannot be empty")
        
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] ✅ Final clerk_org_id validated: '{clerk_org_id}'")
        
        db = DatabaseService(org_id=clerk_org_id)
        now = datetime.utcnow()
        template_id = effective_payload.get("template_id")
        
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
        
        # ============================================================
        # SAFEGUARD 5: ALWAYS include clerk_org_id (no conditional)
        # This ensures it's NEVER NULL when inserting
        # ============================================================
        agent_record["clerk_org_id"] = clerk_org_id
        
        # ============================================================
        # SAFEGUARD 6: Final validation before insert
        # ============================================================
        if "clerk_org_id" not in agent_record:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] clerk_org_id key missing from agent_record")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] agent_record keys: {list(agent_record.keys())}")
            raise ValidationError("clerk_org_id is missing from agent_record - this should never happen")
        
        if not agent_record["clerk_org_id"] or not str(agent_record["clerk_org_id"]).strip():
            logger.error(f"[AGENTS] [DRAFT] [ERROR] clerk_org_id is empty in agent_record")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] agent_record['clerk_org_id']: {agent_record['clerk_org_id']}")
            raise ValidationError("clerk_org_id cannot be empty in agent_record - this should never happen")
        
        # CRITICAL DEBUG: Log agent_record before insert
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] agent_record before insert: {agent_record}")
        logger.info(f"[AGENTS] [DRAFT] [DEBUG] ✅ clerk_org_id in agent_record: {agent_record['clerk_org_id']}")
        
        # ============================================================
        # SAFEGUARD 7: Insert with explicit error handling
        # ============================================================
        try:
            db.insert("agents", agent_record)
            logger.info(f"[AGENTS] [DRAFT] [DEBUG] ✅ Agent inserted successfully with clerk_org_id: {agent_record['clerk_org_id']}")
        except Exception as insert_error:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Database insert failed: {insert_error}")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] agent_record that failed: {agent_record}")
            logger.error(f"[AGENTS] [DRAFT] [ERROR] clerk_org_id value: {agent_record.get('clerk_org_id')}")
            raise ValidationError(f"Failed to insert agent: {str(insert_error)}")
        
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
