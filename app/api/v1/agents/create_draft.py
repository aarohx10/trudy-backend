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
        # Get org_id from multiple sources (frontend request body takes priority, then JWT token)
        payload_org_id = payload.get("clerk_org_id")
        token_org_id = current_user.get("clerk_org_id")
        clerk_org_id = payload_org_id or token_org_id
        
        logger.info(
            f"[AGENTS] [DRAFT] [INIT] Extracting org_id | "
            f"payload_org_id={payload_org_id} | "
            f"token_org_id={token_org_id} | "
            f"final_clerk_org_id={clerk_org_id}"
        )
        
        if not clerk_org_id:
            logger.error(
                f"[AGENTS] [DRAFT] [ERROR] Missing organization ID | "
                f"payload_keys={list(payload.keys())} | "
                f"current_user_keys={list(current_user.keys())}"
            )
            raise ValidationError("Missing organization ID in token or request body")
        
        # Strip and normalize
        clerk_org_id = str(clerk_org_id).strip()
        logger.info(f"[AGENTS] [DRAFT] [INIT] ✅ Using clerk_org_id={clerk_org_id}")
        
        # Initialize database service
        db = DatabaseService(org_id=clerk_org_id)
        logger.info(f"[AGENTS] [DRAFT] [INIT] DatabaseService initialized | db.org_id={db.org_id}")
        now = datetime.utcnow()
        template_id = payload.get("template_id")
        
        # Get default voice if available
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
        
        # Create agent record - always start as "draft"
        agent_record = {
            "id": agent_id,
            "clerk_org_id": clerk_org_id,
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
        
        # Insert into database (trust it works)
        logger.info(
            f"[AGENTS] [DRAFT] [INSERT] About to insert agent | "
            f"agent_id={agent_id} | "
            f"agent_record_clerk_org_id={agent_record.get('clerk_org_id')} | "
            f"db.org_id={db.org_id}"
        )
        db.insert("agents", agent_record)
        logger.info(f"[AGENTS] [DRAFT] [INSERT] ✅ Agent inserted successfully | agent_id={agent_id}")
        
        # Try Ultravox sync in background (non-blocking)
        try:
            ultravox_response = await create_agent_ultravox_first(agent_record, clerk_org_id)
            ultravox_agent_id = ultravox_response.get("agentId")
            if ultravox_agent_id:
                db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
                    "ultravox_agent_id": ultravox_agent_id,
                    "status": "active"
                })
        except Exception as e:
            logger.warning(f"[AGENTS] [DRAFT] Ultravox creation failed (non-critical): {e}")
            # Agent stays as "draft"
        
        # Simple re-fetch - let select_one auto-append clerk_org_id using db.org_id (matches what was used during insert)
        logger.info(
            f"[AGENTS] [DRAFT] [FETCH] Attempting to re-fetch agent | "
            f"agent_id={agent_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"db.org_id={db.org_id}"
        )
        
        # Try with explicit clerk_org_id first
        created_agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})
        
        if not created_agent:
            logger.warning(
                f"[AGENTS] [DRAFT] [FETCH] [RETRY] Agent not found with explicit clerk_org_id, trying with db.org_id | "
                f"agent_id={agent_id} | "
                f"clerk_org_id={clerk_org_id} | "
                f"db.org_id={db.org_id}"
            )
            # Retry with just id (let auto-append work)
            created_agent = db.select_one("agents", {"id": agent_id})
        
        if not created_agent:
            logger.error(
                f"[AGENTS] [DRAFT] [FETCH] [ERROR] Agent not found after insert! | "
                f"agent_id={agent_id} | "
                f"clerk_org_id={clerk_org_id} | "
                f"db.org_id={db.org_id} | "
                f"Tried both explicit and auto-append filters"
            )
            
            # CRITICAL DEBUG: Try to fetch without RLS to see if agent exists
            try:
                from app.core.database import DatabaseAdminService
                admin_db = DatabaseAdminService()
                debug_agent = admin_db.select_one("agents", {"id": agent_id})
                if debug_agent:
                    logger.error(
                        f"[AGENTS] [DRAFT] [FETCH] [DEBUG] Agent EXISTS in database but RLS is blocking! | "
                        f"agent_id={agent_id} | "
                        f"stored_clerk_org_id={debug_agent.get('clerk_org_id')} | "
                        f"expected_clerk_org_id={clerk_org_id} | "
                        f"db.org_id={db.org_id} | "
                        f"MATCH={debug_agent.get('clerk_org_id') == clerk_org_id}"
                    )
                else:
                    logger.error(
                        f"[AGENTS] [DRAFT] [FETCH] [DEBUG] Agent does NOT exist in database at all! | "
                        f"agent_id={agent_id}"
                    )
            except Exception as debug_error:
                logger.error(f"[AGENTS] [DRAFT] [FETCH] [DEBUG] Failed to check with admin DB: {debug_error}")
            
            raise ValidationError(f"Failed to retrieve agent after creation: {agent_id}")
        
        logger.info(
            f"[AGENTS] [DRAFT] [FETCH] ✅ Agent fetched successfully | "
            f"agent_id={agent_id} | "
            f"fetched_clerk_org_id={created_agent.get('clerk_org_id')} | "
            f"expected_clerk_org_id={clerk_org_id} | "
            f"db.org_id={db.org_id}"
        )
        
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
