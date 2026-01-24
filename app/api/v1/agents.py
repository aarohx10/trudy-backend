"""
Agent Endpoints
Handles agent CRUD operations, Ultravox synchronization, test calls, and AI assistance.
"""
from fastapi import APIRouter, Depends, Header, Body, HTTPException
from typing import Optional, Dict, Any, List
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError, ProviderError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.models.schemas import (
    ResponseMeta,
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    AgentTestCallRequest,
    AgentTestCallResponse,
    AgentAIAssistRequest,
    AgentAIAssistResponse,
)
from app.services.agent import (
    create_agent_in_ultravox,
    update_agent_in_ultravox,
    delete_agent_from_ultravox,
    sync_agent_to_ultravox,
    build_ultravox_call_template,
    get_voice_ultravox_id,
)
from app.services.ultravox import ultravox_client
from app.core.config import settings
from starlette.requests import Request

logger = logging.getLogger(__name__)

router = APIRouter()

# Try to import OpenAI for AI assistance
try:
    import openai
    openai_available = True
except ImportError:
    openai_available = False
    logger.warning("OpenAI library not available. AI assistance features will be disabled.")


@router.get("")
async def list_agents(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List all agents for current client"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        agents = db.select("agents", {"client_id": client_id}, order_by="created_at DESC")
        
        return {
            "data": list(agents),
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
        }
        logger.error(f"[AGENTS] [LIST] Failed to list agents (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list agents: {str(e)}")


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single agent"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        
        if not agent:
            raise NotFoundError("agent", agent_id)
        
        return {
            "data": agent,
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
        logger.error(f"[AGENTS] [GET] Failed to get agent (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to get agent: {str(e)}")


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


@router.post("")
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
            "model": agent_dict.get("model", "fixie-ai/ultravox-v0_4-8k"),
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
        
        # Insert into database first
        db.insert("agents", agent_record)
        logger.info(f"[AGENTS] [CREATE] Agent saved to database: {agent_id}")
        
        # Try to sync to Ultravox (non-blocking - can fail and retry later)
        try:
            ultravox_response = await sync_agent_to_ultravox(agent_id, client_id)
            logger.info(f"[AGENTS] [CREATE] Agent synced to Ultravox: {ultravox_response.get('agentId')}")
        except Exception as uv_error:
            logger.warning(f"[AGENTS] [CREATE] Failed to sync agent to Ultravox (non-critical, can retry): {uv_error}", exc_info=True)
            # Update status to failed but keep the record
            db.update("agents", {"id": agent_id, "client_id": client_id}, {
                "status": "failed",
            })
        
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
        
        # Try to sync to Ultravox (non-blocking)
        ultravox_agent_id = existing_agent.get("ultravox_agent_id")
        if ultravox_agent_id:
            try:
                await update_agent_in_ultravox(ultravox_agent_id, merged_agent)
                # Update status to active on success
                db.update("agents", {"id": agent_id, "client_id": client_id}, {"status": "active"})
                logger.info(f"[AGENTS] [UPDATE] Agent synced to Ultravox: {ultravox_agent_id}")
            except Exception as uv_error:
                logger.warning(f"[AGENTS] [UPDATE] Failed to sync agent to Ultravox (non-critical): {uv_error}", exc_info=True)
                # Don't fail the request, just log the error
        
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


@router.patch("/{agent_id}")
async def partial_update_agent(
    agent_id: str,
    agent_data: AgentUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Partial update agent (for auto-save) - same as PUT but more lenient"""
    # Reuse PUT logic
    return await update_agent(agent_id, agent_data, current_user, x_client_id)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete agent (deletes from both Supabase + Ultravox)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get existing agent
        existing_agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        if not existing_agent:
            raise NotFoundError("agent", agent_id)
        
        # Delete from Ultravox if we have ultravox_agent_id
        ultravox_agent_id = existing_agent.get("ultravox_agent_id")
        if ultravox_agent_id:
            try:
                await delete_agent_from_ultravox(ultravox_agent_id)
                logger.info(f"[AGENTS] [DELETE] Agent deleted from Ultravox: {ultravox_agent_id}")
            except Exception as uv_error:
                logger.warning(f"[AGENTS] [DELETE] Failed to delete agent from Ultravox (non-critical): {uv_error}", exc_info=True)
                # Continue to delete from database even if Ultravox delete fails
        
        # Delete from database
        db.delete("agents", {"id": agent_id, "client_id": client_id})
        logger.info(f"[AGENTS] [DELETE] Agent deleted from database: {agent_id}")
        
        return {
            "data": {"id": agent_id, "deleted": True},
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
        logger.error(f"[AGENTS] [DELETE] Failed to delete agent (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to delete agent: {str(e)}")


@router.post("/{agent_id}/test-call")
async def create_test_call(
    agent_id: str,
    test_call_data: AgentTestCallRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create WebRTC test call for agent"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get agent
        agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        if not agent:
            raise NotFoundError("agent", agent_id)
        
        ultravox_agent_id = agent.get("ultravox_agent_id")
        if not ultravox_agent_id:
            raise ValidationError("Agent not synced to Ultravox. Please sync the agent first.")
        
        # Build call data with WebRTC medium
        call_data = {
            "medium": {
                "webRtc": {
                    "dataMessages": {
                        "transcript": True,
                        "state": True,
                    }
                }
            }
        }
        
        # Create call in Ultravox
        ultravox_response = await ultravox_client.create_agent_call(ultravox_agent_id, call_data)
        
        call_id = ultravox_response.get("callId")
        join_url = ultravox_response.get("joinUrl")
        
        if not join_url:
            raise ValidationError("Ultravox did not return joinUrl for test call")
        
        response_data = {
            "data": {
                "call_id": call_id,
                "join_url": join_url,
                "agent_id": agent_id,
                "created_at": datetime.utcnow().isoformat(),
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
        return response_data
        
    except Exception as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
            "agent_id": agent_id,
        }
        logger.error(f"[AGENTS] [TEST_CALL] Failed to create test call (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ValidationError, ProviderError)):
            raise
        raise ValidationError(f"Failed to create test call: {str(e)}")


@router.get("/{agent_id}/sync")
async def sync_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Sync agent with Ultravox (create or update)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        
        # Sync agent
        ultravox_response = await sync_agent_to_ultravox(agent_id, client_id)
        
        # Update database with Ultravox agent ID if it was created
        db = DatabaseService()
        agent = db.select_one("agents", {"id": agent_id, "client_id": client_id})
        if agent and not agent.get("ultravox_agent_id"):
            ultravox_agent_id = ultravox_response.get("agentId")
            if ultravox_agent_id:
                db.update("agents", {"id": agent_id, "client_id": client_id}, {
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


@router.post("/ai-assist")
async def ai_assist(
    assist_request: AgentAIAssistRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """AI assistance for agent creation/editing (uses OpenAI)"""
    if not openai_available or not settings.OPENAI_API_KEY:
        raise ValidationError("OpenAI is not configured. AI assistance is unavailable.")
    
    try:
        # Build prompt with context
        context_text = ""
        if assist_request.context:
            context_text = f"\n\nCurrent Agent Context:\n{json.dumps(assist_request.context, indent=2)}"
        
        action_instructions = ""
        if assist_request.action == "improve_prompt":
            action_instructions = "\n\nFocus on improving the system prompt. Make it more effective, clear, and actionable."
        elif assist_request.action == "suggest_greeting":
            action_instructions = "\n\nSuggest an appropriate greeting message for the agent based on the context."
        elif assist_request.action:
            action_instructions = f"\n\nAction: {assist_request.action}"
        
        system_prompt = """You are an AI assistant helping users create and improve AI agents. 
Provide helpful, actionable suggestions based on the user's request and the agent context provided.
Be concise but thorough. Focus on practical improvements."""
        
        user_prompt = f"{assist_request.prompt}{context_text}{action_instructions}"
        
        # Call OpenAI
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        response = await client.chat.completions.create(
            model="gpt-4o-mini",  # Fast and cost-effective
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
        )
        
        suggestion = response.choices[0].message.content
        
        # Try to extract improved content if it's structured
        improved_content = None
        if assist_request.action == "improve_prompt" and "```" in suggestion:
            # Try to extract code block content
            import re
            code_blocks = re.findall(r'```(?:\w+)?\n(.*?)\n```', suggestion, re.DOTALL)
            if code_blocks:
                improved_content = code_blocks[0].strip()
        
        return {
            "data": {
                "suggestion": suggestion,
                "improved_content": improved_content,
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
        }
        logger.error(f"[AGENTS] [AI_ASSIST] Failed to get AI assistance (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to get AI assistance: {str(e)}")
