"""
AI Assistance Suggestion Approval Endpoints
POST /agents/{agent_id}/assistance/suggestions/{message_id}/approve - Approve prompt change
POST /agents/{agent_id}/assistance/suggestions/{message_id}/reject - Reject prompt change
"""
from fastapi import APIRouter, Depends, Header, Path
from typing import Optional
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import get_supabase_admin_client
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError
from app.models.schemas import ResponseMeta
from app.services.ai_assistance import (
    get_message_with_suggestion,
    update_message_approval_state,
    get_session,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{agent_id}/assistance/suggestions/{message_id}/approve")
async def approve_prompt_suggestion(
    agent_id: str = Path(..., description="Agent ID"),
    message_id: str = Path(..., description="Message ID with suggestion"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Approve a suggested prompt change and update the agent's system_prompt.
    
    Returns:
    {
        "data": {
            "message_id": "uuid",
            "approved": true,
            "updated_prompt": "string"
        }
    }
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    client_id = current_user.get("client_id")
    admin_db = get_supabase_admin_client()
    
    # Verify agent exists and belongs to client
    agent = admin_db.table("agents").select("*").eq("id", agent_id).eq("client_id", client_id).single().execute()
    if not agent.data:
        raise NotFoundError("agent", agent_id)
    
    # Get message with suggestion
    message = await get_message_with_suggestion(message_id)
    if not message:
        raise NotFoundError("message", message_id)
    
    # Verify message belongs to a session for this agent
    session = await get_session(message.get("session_id"))
    if not session or session.get("agent_id") != agent_id:
        raise NotFoundError("message", message_id)
    
    # Check if message has a suggestion
    suggested_prompt = message.get("suggested_prompt_change")
    if not suggested_prompt:
        raise ValidationError("Message does not contain a suggested prompt change")
    
    # Check if already approved/rejected
    approval_state = message.get("approval_state")
    if approval_state == "approved":
        raise ValidationError("This suggestion has already been approved")
    if approval_state == "rejected":
        raise ValidationError("This suggestion was rejected and cannot be approved")
    
    # Update agent's system_prompt
    try:
        admin_db.table("agents").update({
            "system_prompt": suggested_prompt,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", agent_id).execute()
        
        # Update message approval state
        await update_message_approval_state(message_id, "approved")
        
        logger.info(f"Approved prompt suggestion for agent {agent_id}, message {message_id}")
        
        return {
            "data": {
                "message_id": message_id,
                "approved": True,
                "updated_prompt": suggested_prompt,
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to approve suggestion: {e}", exc_info=True)
        raise ValidationError(f"Failed to approve suggestion: {str(e)}")


@router.post("/{agent_id}/assistance/suggestions/{message_id}/reject")
async def reject_prompt_suggestion(
    agent_id: str = Path(..., description="Agent ID"),
    message_id: str = Path(..., description="Message ID with suggestion"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Reject a suggested prompt change.
    
    Returns:
    {
        "data": {
            "message_id": "uuid",
            "rejected": true
        }
    }
    """
    client_id = current_user.get("client_id")
    
    # Verify agent exists and belongs to client
    admin_db = get_supabase_admin_client()
    agent = admin_db.table("agents").select("*").eq("id", agent_id).eq("client_id", client_id).single().execute()
    if not agent.data:
        raise NotFoundError("agent", agent_id)
    
    # Get message with suggestion
    message = await get_message_with_suggestion(message_id)
    if not message:
        raise NotFoundError("message", message_id)
    
    # Verify message belongs to a session for this agent
    session = await get_session(message.get("session_id"))
    if not session or session.get("agent_id") != agent_id:
        raise NotFoundError("message", message_id)
    
    # Check if message has a suggestion
    if not message.get("suggested_prompt_change"):
        raise ValidationError("Message does not contain a suggested prompt change")
    
    # Check if already approved/rejected
    approval_state = message.get("approval_state")
    if approval_state == "approved":
        raise ValidationError("This suggestion has already been approved and cannot be rejected")
    if approval_state == "rejected":
        # Already rejected, just return success
        return {
            "data": {
                "message_id": message_id,
                "rejected": True,
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    # Update message approval state
    try:
        await update_message_approval_state(message_id, "rejected")
        
        logger.info(f"Rejected prompt suggestion for agent {agent_id}, message {message_id}")
        
        return {
            "data": {
                "message_id": message_id,
                "rejected": True,
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to reject suggestion: {e}", exc_info=True)
        raise ValidationError(f"Failed to reject suggestion: {str(e)}")
