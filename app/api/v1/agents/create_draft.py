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
from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import ForbiddenError, ValidationError
from app.models.schemas import ResponseMeta
from app.services.agent import create_agent_ultravox_first, validate_agent_for_ultravox_sync

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/draft")
async def create_draft_agent(
    payload: Dict[str, Any] = Body(default={}),
    current_user: dict = Depends(require_admin_role),
):
    """Create a draft agent with default settings, optionally from a template"""
    # Permission check handled by require_admin_role dependency
    
    try:
        # =================================================================
        # DEBUG LOGGING: Track organization ID and user context
        # =================================================================
        clerk_user_id = current_user.get("clerk_user_id") or current_user.get("user_id")
        clerk_org_id = current_user.get("clerk_org_id")
        user_role = current_user.get("role", "unknown")
        
        logger.info(
            f"[AGENTS] [DRAFT] [DEBUG] Draft agent creation attempt | "
            f"clerk_user_id={clerk_user_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"role={user_role}"
        )
        
        # CRITICAL: Use clerk_org_id for organization-first approach
        # Match knowledge bases pattern: Simple, direct validation
        if not clerk_org_id:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Missing organization ID in token | clerk_user_id={clerk_user_id}")
            raise ValidationError("Missing organization ID in token")
        
        # Permission check is handled by require_admin_role dependency
        # Role assignment is handled in get_current_user() via ensure_admin_role_for_creator()
        
        # Initialize database service with org_id context (match knowledge bases pattern)
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
        
        # Create agent record - use clerk_org_id only (organization-first approach)
        # Match knowledge bases pattern: Simple, direct assignment
        agent_record = {
            "id": agent_id,
            "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
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
        
        logger.info(
            f"[AGENTS] [DRAFT] [DEBUG] Agent record prepared | "
            f"agent_id={agent_id} | "
            f"clerk_org_id={clerk_org_id}"
        )
        
        logger.info(
            f"[AGENTS] [DRAFT] [DEBUG] Creating draft agent record | "
            f"agent_id={agent_id} | "
            f"clerk_user_id={clerk_user_id} | "
            f"clerk_org_id={clerk_org_id} | "
            f"name={name}"
        )
        
        # CRITICAL: Ensure clerk_org_id is NEVER modified after initial assignment
        # Store in variable as source of truth
        expected_clerk_org_id = clerk_org_id
        
        # Pre-insert validation: verify clerk_org_id is set correctly
        if agent_record.get("clerk_org_id") != expected_clerk_org_id:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] clerk_org_id mismatch before insert! | actual={agent_record.get('clerk_org_id')} | expected={expected_clerk_org_id}")
            agent_record["clerk_org_id"] = expected_clerk_org_id  # Force correct value
        logger.info(f"[AGENTS] [DRAFT] [PRE-INSERT] ✅ clerk_org_id verified | value={agent_record.get('clerk_org_id')}")
        
        # Validate agent can be created in Ultravox (for status determination)
        validation_result = await validate_agent_for_ultravox_sync(agent_record, clerk_org_id)
        
        # Determine initial status based on validation
        if validation_result["can_sync"]:
            agent_record["status"] = "creating"  # Will be updated to "active" after Ultravox
        else:
            reason = validation_result.get("reason", "unknown")
            if reason == "voice_required":
                agent_record["status"] = "draft"
            else:
                # Other validation failure - return error
                error_msg = "; ".join(validation_result["errors"])
                raise ValidationError(f"Agent validation failed: {error_msg}")
        
        # CRITICAL: Verify clerk_org_id is STILL correct after status modification
        if agent_record.get("clerk_org_id") != expected_clerk_org_id:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] clerk_org_id corrupted after status modification! | actual={agent_record.get('clerk_org_id')} | expected={expected_clerk_org_id}")
            agent_record["clerk_org_id"] = expected_clerk_org_id  # Force correct value
        
        # MATCH KNOWLEDGE BASES PATTERN: Insert FIRST (before external operations)
        logger.info(f"[AGENTS] [DRAFT] [INSERT] Inserting agent into database | agent_id={agent_id} | clerk_org_id={expected_clerk_org_id}")
        db.insert("agents", agent_record)  # Don't capture return value (like knowledge bases)
        
        logger.info(
            f"[AGENTS] [DRAFT] [INSERT] ✅ Agent record inserted | "
            f"agent_id={agent_id} | "
            f"clerk_org_id={expected_clerk_org_id}"
        )
        
        # Variable to store the created agent record
        created_agent = None
        
        # Handle external operations AFTER insert (like knowledge bases)
        if validation_result["can_sync"]:
            # Create in Ultravox AFTER database insert
            try:
                ultravox_response = await create_agent_ultravox_first(agent_record, clerk_org_id)
                ultravox_agent_id = ultravox_response.get("agentId")
                
                if not ultravox_agent_id:
                    raise ValueError("Ultravox did not return agentId")
                
                # Update database with Ultravox ID (separate update call - don't modify original dictionary)
                logger.info(f"[AGENTS] [DRAFT] [UPDATE] Updating agent with Ultravox ID | agent_id={agent_id} | ultravox_agent_id={ultravox_agent_id}")
                db.update("agents", {"id": agent_id, "clerk_org_id": expected_clerk_org_id}, {
                    "ultravox_agent_id": ultravox_agent_id,
                    "status": "active"
                })
                
                logger.info(
                    f"[AGENTS] [DRAFT] [UPDATE] ✅ Agent updated with Ultravox ID | "
                    f"agent_id={agent_id} | "
                    f"clerk_org_id={expected_clerk_org_id}"
                )
                
            except Exception as uv_error:
                # Ultravox creation failed - agent already in DB as "creating" status
                # Update status to indicate failure
                logger.warning(f"[AGENTS] [DRAFT] Ultravox creation failed, updating status | agent_id={agent_id}")
                db.update("agents", {"id": agent_id, "clerk_org_id": expected_clerk_org_id}, {
                    "status": "draft"  # Fallback to draft if Ultravox fails
                })
                import traceback
                import json
                error_details = {
                    "error_type": type(uv_error).__name__,
                    "error_message": str(uv_error),
                    "full_traceback": traceback.format_exc(),
                    "agent_id": agent_id,
                }
                logger.error(f"[AGENTS] [DRAFT] Failed to create in Ultravox (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
                # Re-raise error to return to user
                raise ValidationError(f"Failed to create agent in Ultravox: {str(uv_error)}")
        
        # MATCH KNOWLEDGE BASES PATTERN: Re-fetch at the end (after all operations)
        logger.info(f"[AGENTS] [DRAFT] [FETCH] Fetching agent from database | agent_id={agent_id} | clerk_org_id={expected_clerk_org_id}")
        created_agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": expected_clerk_org_id})
        
        if not created_agent:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Agent not found after insert! | agent_id={agent_id} | clerk_org_id={expected_clerk_org_id}")
            raise ValidationError(f"Failed to create/retrieve agent: {agent_id}")
        
        # Verify fetched record has correct clerk_org_id
        if created_agent.get("clerk_org_id") != expected_clerk_org_id:
            logger.error(f"[AGENTS] [DRAFT] [ERROR] Fetched record has wrong clerk_org_id! | actual={created_agent.get('clerk_org_id')} | expected={expected_clerk_org_id}")
            raise ValidationError(f"Agent created with incorrect organization ID: {agent_id}")
        
        logger.info(f"[AGENTS] [DRAFT] [FETCH] ✅ Agent fetched successfully | agent_id={agent_id} | clerk_org_id={created_agent.get('clerk_org_id')}")
        
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
