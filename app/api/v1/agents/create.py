"""
Create Agent Endpoint
POST /agents - Create new agent (creates in Supabase + Ultravox)
"""
from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from typing import Optional
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, ProviderError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.models.schemas import ResponseMeta, AgentCreate
from app.services.agent import create_agent_ultravox_first, validate_agent_for_ultravox_sync
from starlette.requests import Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/")
async def create_agent(
    agent_data: AgentCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create new agent (creates in Supabase + Ultravox)"""
    try:
        # Get org_id from multiple sources (request body takes priority, then JWT token)
        # Convert to dict first to check for clerk_org_id
        agent_dict = agent_data.dict(exclude_none=True)
        clerk_org_id = agent_dict.get("clerk_org_id") or current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token or request body")
        
        # Check idempotency key
        if idempotency_key:
            cached = await check_idempotency_key(
                clerk_org_id,
                idempotency_key,
                request,
                agent_dict,
            )
            if cached:
                return JSONResponse(
                    content=cached["response_body"],
                    status_code=cached["status_code"],
                )
        
        # Initialize database service
        db = DatabaseService(org_id=clerk_org_id)
        now = datetime.utcnow()
        agent_id = str(uuid.uuid4())
        
        # Create agent record - always start as "draft"
        agent_record = {
            "id": agent_id,
            "clerk_org_id": clerk_org_id,
            "name": agent_dict["name"],
            "description": agent_dict.get("description"),
            "voice_id": agent_dict["voice_id"],
            "system_prompt": agent_dict["system_prompt"],
            "model": agent_dict.get("model", "ultravox-v0.6"),
            "tools": agent_dict.get("tools", []),
            "knowledge_bases": agent_dict.get("knowledge_bases", []),
            "status": "draft",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # Add optional call template fields
        if agent_dict.get("call_template_name"):
            agent_record["call_template_name"] = agent_dict["call_template_name"]
        if agent_dict.get("greeting_settings"):
            agent_record["greeting_settings"] = agent_dict["greeting_settings"].dict() if hasattr(agent_dict["greeting_settings"], "dict") else agent_dict["greeting_settings"]
        if agent_dict.get("inactivity_messages"):
            agent_record["inactivity_messages"] = [msg.dict() if hasattr(msg, "dict") else msg for msg in agent_dict["inactivity_messages"]]
        if agent_dict.get("temperature") is not None:
            agent_record["temperature"] = float(agent_dict["temperature"])
        if agent_dict.get("language_hint"):
            agent_record["language_hint"] = agent_dict["language_hint"]
        if agent_dict.get("time_exceeded_message"):
            agent_record["time_exceeded_message"] = agent_dict["time_exceeded_message"]
        if agent_dict.get("recording_enabled") is not None:
            agent_record["recording_enabled"] = agent_dict["recording_enabled"]
        if agent_dict.get("join_timeout"):
            agent_record["join_timeout"] = agent_dict["join_timeout"]
        if agent_dict.get("max_duration"):
            agent_record["max_duration"] = agent_dict["max_duration"]
        if agent_dict.get("initial_output_medium"):
            agent_record["initial_output_medium"] = agent_dict["initial_output_medium"].value if hasattr(agent_dict["initial_output_medium"], "value") else str(agent_dict["initial_output_medium"])
        if agent_dict.get("vad_settings"):
            agent_record["vad_settings"] = agent_dict["vad_settings"].dict() if hasattr(agent_dict["vad_settings"], "dict") else agent_dict["vad_settings"]
        if agent_dict.get("template_id"):
            agent_record["template_id"] = agent_dict["template_id"]
        
        # Add legacy fields
        if agent_dict.get("success_criteria"):
            agent_record["success_criteria"] = agent_dict["success_criteria"]
        if agent_dict.get("extraction_schema"):
            agent_record["extraction_schema"] = agent_dict["extraction_schema"]
        if agent_dict.get("crm_webhook_url"):
            agent_record["crm_webhook_url"] = agent_dict["crm_webhook_url"]
        if agent_dict.get("crm_webhook_secret"):
            agent_record["crm_webhook_secret"] = agent_dict["crm_webhook_secret"]
        
        # Insert into database (trust it works)
        db.insert("agents", agent_record)
        
        # Try Ultravox sync (non-blocking)
        try:
            validation_result = await validate_agent_for_ultravox_sync(agent_record, clerk_org_id)
            if validation_result["can_sync"]:
                ultravox_response = await create_agent_ultravox_first(agent_record, clerk_org_id)
                ultravox_agent_id = ultravox_response.get("agentId")
                if ultravox_agent_id:
                    db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
                        "ultravox_agent_id": ultravox_agent_id,
                        "status": "active"
                    })
        except Exception as e:
            logger.warning(f"[AGENTS] [CREATE] Ultravox sync failed: {e}")
            # Agent stays as "draft"
        
        # Simple re-fetch - let select_one auto-append clerk_org_id using db.org_id (matches what was used during insert)
        logger.info(
            f"[AGENTS] [CREATE] [FETCH] Attempting to re-fetch agent | "
            f"agent_id={agent_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"db.org_id={db.org_id}"
        )
        created_agent = db.select_one("agents", {"id": agent_id})
        
        if not created_agent:
            logger.error(
                f"[AGENTS] [CREATE] [FETCH] [ERROR] Agent not found after insert! | "
                f"agent_id={agent_id} | "
                f"clerk_org_id={clerk_org_id} | "
                f"db.org_id={db.org_id} | "
                f"filter_used=id only (auto-append expected)"
            )
            raise ValidationError(f"Failed to retrieve agent after creation: {agent_id}")
        
        logger.info(
            f"[AGENTS] [CREATE] [FETCH] ✅ Agent fetched successfully | "
            f"agent_id={agent_id} | "
            f"fetched_clerk_org_id={created_agent.get('clerk_org_id')} | "
            f"expected_clerk_org_id={clerk_org_id} | "
            f"db.org_id={db.org_id}"
        )
        
        response_data = {
            "data": created_agent,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
        # Store idempotency response
        if idempotency_key:
            await store_idempotency_response(
                clerk_org_id,
                idempotency_key,
                request,
                agent_data.dict(),
                response_data,
                201,
            )
        
        return response_data
        
    except (ValidationError, ProviderError):
        raise
    except Exception as e:
        logger.error(f"[AGENTS] [CREATE] Failed to create agent: {e}", exc_info=True)
        raise ValidationError(f"Failed to create agent: {str(e)}")
