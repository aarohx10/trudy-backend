"""
Update Agent Endpoint
PUT /agents/{agent_id} - Update agent (updates both Supabase + Ultravox)
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError, ProviderError
from app.models.schemas import (
    ResponseMeta,
    AgentUpdate,
)
from app.services.agent import sync_agent_to_ultravox, validate_agent_for_ultravox_sync

logger = logging.getLogger(__name__)

router = APIRouter()


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    agent_data: AgentUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update agent (updates both Supabase + Ultravox)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get existing agent
        existing_agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        if not existing_agent:
            raise NotFoundError("agent", agent_id)
        
        # Convert Pydantic model to dict (only include provided fields)
        update_dict = agent_data.dict(exclude_none=True)
        
        # Build update data
        update_data = {
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        # Update basic fields
        if "name" in update_dict:
            update_data["name"] = update_dict["name"]
        if "description" in update_dict:
            update_data["description"] = update_dict["description"]
        if "voice_id" in update_dict:
            update_data["voice_id"] = update_dict["voice_id"]
        if "system_prompt" in update_dict:
            update_data["system_prompt"] = update_dict["system_prompt"]
        if "model" in update_dict:
            update_data["model"] = update_dict["model"]
        if "tools" in update_dict:
            update_data["tools"] = update_dict["tools"]
        if "knowledge_bases" in update_dict:
            update_data["knowledge_bases"] = update_dict["knowledge_bases"]
        
        # Update call template fields
        if "call_template_name" in update_dict:
            update_data["call_template_name"] = update_dict["call_template_name"]
        if "greeting_settings" in update_dict:
            update_data["greeting_settings"] = update_dict["greeting_settings"].dict() if hasattr(update_dict["greeting_settings"], "dict") else update_dict["greeting_settings"]
        if "inactivity_messages" in update_dict:
            update_data["inactivity_messages"] = [msg.dict() if hasattr(msg, "dict") else msg for msg in update_dict["inactivity_messages"]]
        if "temperature" in update_dict:
            update_data["temperature"] = float(update_dict["temperature"])
        if "language_hint" in update_dict:
            update_data["language_hint"] = update_dict["language_hint"]
        if "time_exceeded_message" in update_dict:
            update_data["time_exceeded_message"] = update_dict["time_exceeded_message"]
        if "recording_enabled" in update_dict:
            update_data["recording_enabled"] = update_dict["recording_enabled"]
        if "join_timeout" in update_dict:
            update_data["join_timeout"] = update_dict["join_timeout"]
        if "max_duration" in update_dict:
            update_data["max_duration"] = update_dict["max_duration"]
        if "initial_output_medium" in update_dict:
            update_data["initial_output_medium"] = update_dict["initial_output_medium"].value if hasattr(update_dict["initial_output_medium"], "value") else str(update_dict["initial_output_medium"])
        if "vad_settings" in update_dict:
            update_data["vad_settings"] = update_dict["vad_settings"].dict() if hasattr(update_dict["vad_settings"], "dict") else update_dict["vad_settings"]
        
        # Update legacy fields
        if "success_criteria" in update_dict:
            update_data["success_criteria"] = update_dict["success_criteria"]
        if "extraction_schema" in update_dict:
            update_data["extraction_schema"] = update_dict["extraction_schema"]
        if "crm_webhook_url" in update_dict:
            update_data["crm_webhook_url"] = update_dict["crm_webhook_url"]
        if "crm_webhook_secret" in update_dict:
            update_data["crm_webhook_secret"] = update_dict["crm_webhook_secret"]
        
        # Merge with existing agent data for Ultravox sync
        merged_agent = {**existing_agent, **update_data}
        
        # Update in database
        db.update("agents", {"id": agent_id, "client_id": client_id}, update_data)
        logger.info(f"[AGENTS] [UPDATE] Agent updated in database: {agent_id}")
        
        # Validate and sync to Ultravox
        validation_result = await validate_agent_for_ultravox_sync(merged_agent, client_id)
        
        if validation_result["can_sync"]:
            try:
                ultravox_response = await sync_agent_to_ultravox(agent_id, client_id)
                # Update status to active on success
                db.update("agents", {"id": agent_id, "client_id": client_id}, {"status": "active"})
                logger.info(f"[AGENTS] [UPDATE] Agent synced to Ultravox: {agent_id}")
            except Exception as uv_error:
                # Log full error details
                import traceback
                error_details = {
                    "error_type": type(uv_error).__name__,
                    "error_message": str(uv_error),
                    "full_traceback": traceback.format_exc(),
                    "agent_id": agent_id,
                    "validation_result": validation_result,
                }
                logger.error(f"[AGENTS] [UPDATE] Failed to sync agent to Ultravox (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
                # Update status to failed
                db.update("agents", {"id": agent_id, "client_id": client_id}, {"status": "failed"})
                # Don't fail the request, but status reflects the failure
        else:
            # Validation failed - agent not ready for Ultravox
            reason = validation_result.get("reason", "unknown")
            logger.info(f"[AGENTS] [UPDATE] Agent not synced (validation failed: {reason})")
            # Keep current status (draft or failed) - don't change it
        
        # Fetch updated agent
        updated_agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        
        return {
            "data": updated_agent,
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
        logger.error(f"[AGENTS] [UPDATE] Failed to update agent (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ForbiddenError, ProviderError)):
            raise
        raise ValidationError(f"Failed to update agent: {str(e)}")
