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
from app.core.permissions import require_admin_role
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
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create new agent (creates in Supabase + Ultravox)"""
    # Permission check handled by require_admin_role dependency
    
    try:
        # =================================================================
        # DEBUG LOGGING: Track organization ID and user context
        # =================================================================
        clerk_user_id = current_user.get("clerk_user_id") or current_user.get("user_id")
        clerk_org_id = current_user.get("clerk_org_id")
        user_role = current_user.get("role", "unknown")
        
        logger.info(
            f"[AGENTS] [CREATE] [DEBUG] Agent creation attempt | "
            f"clerk_user_id={clerk_user_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"role={user_role}"
        )
        
        # CRITICAL: Use clerk_org_id for organization-first approach
        # Match knowledge bases pattern: Simple, direct validation
        if not clerk_org_id:
            logger.error(f"[AGENTS] [CREATE] [ERROR] Missing organization ID in token | clerk_user_id={clerk_user_id}")
            raise ValidationError("Missing organization ID in token")
        
        # Permission check is handled by require_admin_role dependency
        # Role assignment is handled in get_current_user() via ensure_admin_role_for_creator()
        
        # Check idempotency key
        if idempotency_key:
            cached = await check_idempotency_key(
                clerk_org_id,
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
        
        # Initialize database service with org_id context (match knowledge bases pattern exactly)
        db = DatabaseService(org_id=clerk_org_id)
        now = datetime.utcnow()
        
        # Convert Pydantic model to dict
        agent_dict = agent_data.dict(exclude_none=True)
        
        agent_id = str(uuid.uuid4())
        
        # Create agent record - use clerk_org_id only (organization-first approach)
        # Match knowledge bases pattern: Simple, direct assignment
        agent_record = {
            "id": agent_id,
            "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
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
        
        logger.info(
            f"[AGENTS] [CREATE] [DEBUG] Agent record prepared | "
            f"agent_id={agent_id} | "
            f"clerk_org_id={clerk_org_id}"
        )
        
        logger.info(
            f"[AGENTS] [CREATE] [DEBUG] Creating agent record | "
            f"agent_id={agent_id} | "
            f"clerk_user_id={clerk_user_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"name={agent_dict['name']}"
        )
        
        # Validate agent can be created in Ultravox
        validation_result = await validate_agent_for_ultravox_sync(agent_record, clerk_org_id)
        
        if not validation_result["can_sync"]:
            # Validation failed - return error immediately
            error_msg = "; ".join(validation_result["errors"])
            raise ValidationError(f"Agent validation failed: {error_msg}")
        
        # Set initial status
        agent_record["status"] = "creating"  # Will be updated to "active" after Ultravox
        
        # MATCH KNOWLEDGE BASES PATTERN: Insert FIRST (before external operations)
        logger.info(
            f"[AGENTS] [CREATE] [INSERT] Inserting agent into database | "
            f"agent_id={agent_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"db.org_id={db.org_id} | "
            f"agent_record.clerk_org_id={agent_record.get('clerk_org_id')}"
        )
        try:
            insert_result = db.insert("agents", agent_record)  # Capture return value for verification only
            # Verify insert succeeded and returned data
            if not insert_result:
                logger.error(f"[AGENTS] [CREATE] [INSERT] [ERROR] Insert returned None/empty! | agent_id={agent_id}")
                raise ValidationError(f"Failed to insert agent: Insert returned empty result")
            
            returned_clerk_org_id = insert_result.get("clerk_org_id")
            logger.info(
                f"[AGENTS] [CREATE] [INSERT] ✅ Agent record inserted | "
                f"agent_id={agent_id} | "
                f"clerk_org_id={clerk_org_id} | "
                f"returned_clerk_org_id={returned_clerk_org_id} | "
                f"returned_id={insert_result.get('id')}"
            )
            
            # Verify returned clerk_org_id matches what we sent (after normalization)
            if returned_clerk_org_id and str(returned_clerk_org_id).strip() != str(clerk_org_id).strip():
                logger.error(
                    f"[AGENTS] [CREATE] [INSERT] [ERROR] clerk_org_id mismatch in returned record! | "
                    f"expected={clerk_org_id} | returned={returned_clerk_org_id} | agent_id={agent_id}"
                )
                # Don't fail here - database might have normalized it, but log for debugging
        except Exception as insert_error:
            import traceback
            import json
            error_details = {
                "error_type": type(insert_error).__name__,
                "error_message": str(insert_error),
                "full_traceback": traceback.format_exc(),
                "agent_id": agent_id,
                "clerk_org_id": clerk_org_id,
                "db.org_id": db.org_id,
                "agent_record_clerk_org_id": agent_record.get("clerk_org_id"),
            }
            logger.error(f"[AGENTS] [CREATE] [INSERT] [ERROR] Insert failed (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
            raise ValidationError(f"Failed to insert agent: {str(insert_error)}")
        
        # Create in Ultravox AFTER database insert (like knowledge bases pattern)
        try:
            # Pass clerk_org_id to Ultravox for metadata tagging (vital for webhook billing/logging)
            ultravox_response = await create_agent_ultravox_first(agent_record, clerk_org_id)
            ultravox_agent_id = ultravox_response.get("agentId")
            
            if not ultravox_agent_id:
                raise ValueError("Ultravox did not return agentId")
            
            # Update database with Ultravox ID (separate update call - don't modify original dictionary)
            logger.info(f"[AGENTS] [CREATE] [UPDATE] Updating agent with Ultravox ID | agent_id={agent_id} | ultravox_agent_id={ultravox_agent_id}")
            db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
                "ultravox_agent_id": ultravox_agent_id,
                "status": "active"
            })
            
            logger.info(
                f"[AGENTS] [CREATE] [UPDATE] ✅ Agent updated with Ultravox ID | "
                f"agent_id={agent_id} | "
                f"clerk_org_id={clerk_org_id}"
            )
            
        except Exception as uv_error:
            # Ultravox creation failed - agent already in DB as "creating" status
            # Update status to indicate failure
            logger.warning(f"[AGENTS] [CREATE] Ultravox creation failed, updating status | agent_id={agent_id}")
            db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
                "status": "draft"  # Fallback to draft if Ultravox fails
            })
            import traceback
            import json
            error_details = {
                "error_type": type(uv_error).__name__,
                "error_message": str(uv_error),
                "full_traceback": traceback.format_exc(),
                "agent_id": agent_id,
                "validation_result": validation_result,
            }
            logger.error(f"[AGENTS] [CREATE] Failed to create in Ultravox (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
            # Re-raise error to return to user
            if isinstance(uv_error, ProviderError):
                raise
            raise ProviderError(
                provider="ultravox",
                message=f"Failed to create agent in Ultravox: {str(uv_error)}",
                http_status=500,
            )
        
        # MATCH KNOWLEDGE BASES PATTERN: Re-fetch at the end (after all operations)
        logger.info(
            f"[AGENTS] [CREATE] [FETCH] Fetching agent from database | "
            f"agent_id={agent_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"db.org_id={db.org_id}"
        )
        try:
            created_agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})
            
            if not created_agent:
                # Try to fetch without org filter to see if record exists (for debugging)
                logger.error(
                    f"[AGENTS] [CREATE] [FETCH] [ERROR] Agent not found after insert! | "
                    f"agent_id={agent_id} | "
                    f"clerk_org_id={clerk_org_id} | "
                    f"db.org_id={db.org_id}"
                )
                # Debug: Try fetching by ID only to see if record exists
                try:
                    debug_agent = db.select_one("agents", {"id": agent_id})
                    if debug_agent:
                        logger.error(
                            f"[AGENTS] [CREATE] [FETCH] [DEBUG] Agent found without org filter! | "
                            f"agent_id={agent_id} | "
                            f"found_clerk_org_id={debug_agent.get('clerk_org_id')} | "
                            f"expected_clerk_org_id={clerk_org_id}"
                        )
                    else:
                        logger.error(f"[AGENTS] [CREATE] [FETCH] [DEBUG] Agent not found even without org filter! | agent_id={agent_id}")
                except Exception as debug_error:
                    logger.error(f"[AGENTS] [CREATE] [FETCH] [DEBUG] Failed to debug fetch: {debug_error}", exc_info=True)
                
                raise ValidationError(f"Failed to create/retrieve agent: {agent_id}")
            
            fetched_clerk_org_id = created_agent.get("clerk_org_id")
            logger.info(
                f"[AGENTS] [CREATE] [FETCH] ✅ Agent fetched successfully | "
                f"agent_id={agent_id} | "
                f"clerk_org_id={fetched_clerk_org_id} | "
                f"expected_clerk_org_id={clerk_org_id}"
            )
            
            # Verify fetched record has correct clerk_org_id (after normalization)
            if fetched_clerk_org_id and str(fetched_clerk_org_id).strip() != str(clerk_org_id).strip():
                logger.warning(
                    f"[AGENTS] [CREATE] [FETCH] [WARNING] Fetched record clerk_org_id differs (after normalization)! | "
                    f"actual={fetched_clerk_org_id} | expected={clerk_org_id} | "
                    f"Both normalized: actual={str(fetched_clerk_org_id).strip()} | expected={str(clerk_org_id).strip()}"
                )
                # Don't fail - might be normalization difference, but log for debugging
            
        except ValidationError:
            raise
        except Exception as fetch_error:
            import traceback
            import json
            error_details = {
                "error_type": type(fetch_error).__name__,
                "error_message": str(fetch_error),
                "full_traceback": traceback.format_exc(),
                "agent_id": agent_id,
                "clerk_org_id": clerk_org_id,
                "db.org_id": db.org_id,
            }
            logger.error(f"[AGENTS] [CREATE] [FETCH] [ERROR] Re-fetch failed (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
            raise ValidationError(f"Failed to retrieve agent after creation: {str(fetch_error)}")
        
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
        
    except (ValidationError, ForbiddenError, ProviderError):
        # Re-raise known error types as-is (they already have proper messages)
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "endpoint": "/agents",
            "user_id": current_user.get("clerk_user_id") if 'current_user' in locals() else None,
            "org_id": current_user.get("clerk_org_id") if 'current_user' in locals() else None,
        }
        logger.error(f"[AGENTS] [CREATE] [ERROR] Unexpected error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to create agent: {str(e)}")
