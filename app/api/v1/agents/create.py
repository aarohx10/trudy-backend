"""
Create Agent Endpoint
POST /agents - Create new agent (creates in Supabase + Ultravox)
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, ForbiddenError, ProviderError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.models.schemas import (
    ResponseMeta,
    AgentCreate,
)
from app.services.agent import create_agent_ultravox_first, validate_agent_for_ultravox_sync
from starlette.requests import Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/")
async def create_agent(
    agent_data: AgentCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create new agent (creates in Supabase + Ultravox)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Check idempotency key
    if idempotency_key:
        cached = await check_idempotency_key(
            current_user["client_id"],
            idempotency_key,
            request,
            agent_data.dict(),
        )
        if cached:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                content=cached["response_body"],
                status_code=cached["status_code"],
            )
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        now = datetime.utcnow()
        
        # Convert Pydantic model to dict
        agent_dict = agent_data.dict(exclude_none=True)
        
        # Build database record
        agent_id = str(uuid.uuid4())
        agent_record = {
            "id": agent_id,
            "client_id": client_id,
            "name": agent_dict["name"],
            "description": agent_dict.get("description"),
            "voice_id": agent_dict["voice_id"],
            "system_prompt": agent_dict["system_prompt"],
            "model": agent_dict.get("model", "ultravox-v0.6"),
            "tools": agent_dict.get("tools", []),
            "knowledge_bases": agent_dict.get("knowledge_bases", []),
            "status": "creating",
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
        
        # Validate agent can be created in Ultravox
        validation_result = await validate_agent_for_ultravox_sync(agent_record, client_id)
        
        if not validation_result["can_sync"]:
            # Validation failed - return error immediately
            error_msg = "; ".join(validation_result["errors"])
            raise ValidationError(f"Agent validation failed: {error_msg}")
        
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
            logger.info(f"[AGENTS] [CREATE] Agent created in Ultravox FIRST, then saved to DB: {agent_id}")
            
        except Exception as uv_error:
            # Ultravox creation failed - DO NOT create in DB
            import traceback
            error_details = {
                "error_type": type(uv_error).__name__,
                "error_message": str(uv_error),
                "full_traceback": traceback.format_exc(),
                "agent_id": agent_id,
                "validation_result": validation_result,
            }
            logger.error(f"[AGENTS] [CREATE] Failed to create in Ultravox FIRST (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
            # Re-raise error to return to user
            if isinstance(uv_error, ProviderError):
                raise
            raise ProviderError(
                provider="ultravox",
                message=f"Failed to create agent in Ultravox: {str(uv_error)}",
                http_status=500,
            )
        
        # Fetch the created agent
        created_agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        
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
                current_user["client_id"],
                idempotency_key,
                request,
                agent_data.dict(),
                response_data,
                201,
            )
        
        return response_data
        
    except Exception as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[AGENTS] [CREATE] Failed to create agent (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError, ProviderError)):
            raise
        raise ValidationError(f"Failed to create agent: {str(e)}")
