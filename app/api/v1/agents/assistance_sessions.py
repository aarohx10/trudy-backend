"""
AI Assistance Session Management Endpoints
GET/POST /agents/{agent_id}/assistance/sessions - Manage chat sessions
GET /agents/{agent_id}/assistance/sessions/{session_id} - Get session with messages
"""
from fastapi import APIRouter, Depends, Header, Path
from typing import Optional
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import get_supabase_admin_client
from app.core.exceptions import NotFoundError, ValidationError
from app.models.schemas import ResponseMeta
from app.services.ai_assistance import (
    create_chat_session,
    list_sessions,
    get_session,
    get_chat_history,
    update_session_title,
    delete_session,
)
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="Session title (auto-generated if not provided)")
    model_id: str = Field("gpt-4o-mini", description="AI model to use")


class UpdateSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, description="Session title")


@router.get("/{agent_id}/assistance/sessions")
async def list_assistance_sessions(
    agent_id: str = Path(..., description="Agent ID"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    List all chat sessions for an agent.
    
    Returns:
    {
        "data": [
            {
                "id": "uuid",
                "title": "string",
                "model_id": "string",
                "created_at": "iso_datetime",
                "updated_at": "iso_datetime",
                "message_count": int
            }
        ]
    }
    """
    client_id = current_user.get("client_id")
    admin_db = get_supabase_admin_client()
    
    # Verify agent exists and belongs to client
    agent = admin_db.table("agents").select("*").eq("id", agent_id).eq("client_id", client_id).single().execute()
    if not agent.data:
        raise NotFoundError("agent", agent_id)
    
    try:
        sessions = await list_sessions(agent_id, client_id)
        
        return {
            "data": sessions,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to list sessions for agent {agent_id}: {e}", exc_info=True)
        raise ValidationError(f"Failed to list sessions: {str(e)}")


@router.post("/{agent_id}/assistance/sessions")
async def create_assistance_session(
    request_data: CreateSessionRequest,
    agent_id: str = Path(..., description="Agent ID"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Create a new chat session.
    
    Request Body:
    {
        "title": "string" (optional),
        "model_id": "gpt-4o" | "gpt-4o-mini"
    }
    
    Returns:
    {
        "data": {
            "id": "uuid",
            "title": "string",
            "model_id": "string",
            "created_at": "iso_datetime"
        }
    }
    """
    
    client_id = current_user.get("client_id")
    admin_db = get_supabase_admin_client()
    
    # Verify agent exists and belongs to client
    agent = admin_db.table("agents").select("*").eq("id", agent_id).eq("client_id", client_id).single().execute()
    if not agent.data:
        raise NotFoundError("agent", agent_id)
    
    if request_data.model_id not in ["gpt-4o", "gpt-4o-mini"]:
        raise ValidationError(f"Unsupported model: {request_data.model_id}. Supported models: gpt-4o, gpt-4o-mini")
    
    try:
        session_id = await create_chat_session(
            agent_id=agent_id,
            client_id=client_id,
            model_id=request_data.model_id,
            title=request_data.title
        )
        
        session = await get_session(session_id)
        
        return {
            "data": session,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to create session for agent {agent_id}: {e}", exc_info=True)
        raise ValidationError(f"Failed to create session: {str(e)}")


@router.get("/{agent_id}/assistance/sessions/{session_id}")
async def get_assistance_session(
    agent_id: str = Path(..., description="Agent ID"),
    session_id: str = Path(..., description="Session ID"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Get full chat history for a session.
    
    Returns:
    {
        "data": {
            "session": {
                "id": "uuid",
                "title": "string",
                "model_id": "string",
                "created_at": "iso_datetime",
                "updated_at": "iso_datetime"
            },
            "messages": [
                {
                    "id": "uuid",
                    "role": "user" | "assistant",
                    "content": "string",
                    "suggested_prompt_change": "string" | null,
                    "approval_state": "pending" | "approved" | "rejected" | null,
                    "created_at": "iso_datetime"
                }
            ]
        }
    }
    """
    client_id = current_user.get("client_id")
    
    # Verify agent exists and belongs to client
    admin_db = get_supabase_admin_client()
    agent = admin_db.table("agents").select("*").eq("id", agent_id).eq("client_id", client_id).single().execute()
    if not agent.data:
        raise NotFoundError("agent", agent_id)
    
    # Get session
    session = await get_session(session_id)
    if not session:
        raise NotFoundError("session", session_id)
    
    # Verify session belongs to agent
    if session.get("agent_id") != agent_id:
        raise NotFoundError("session", session_id)
    
    # Get messages
    messages = await get_chat_history(session_id)
    
    return {
        "data": {
            "session": session,
            "messages": messages,
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/{agent_id}/assistance/sessions/{session_id}")
async def update_assistance_session(
    request_data: UpdateSessionRequest,
    agent_id: str = Path(..., description="Agent ID"),
    session_id: str = Path(..., description="Session ID"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Update session (currently only title).
    
    Request Body:
    {
        "title": "string"
    }
    """
    client_id = current_user.get("client_id")
    
    # Verify session exists and belongs to agent
    session = await get_session(session_id)
    if not session or session.get("agent_id") != agent_id or session.get("client_id") != client_id:
        raise NotFoundError("session", session_id)
    
    title = request_data.title.strip()
    if not title:
        raise ValidationError("Title cannot be empty")
    
    success = await update_session_title(session_id, title)
    if not success:
        raise ValidationError("Failed to update session")
    
    updated_session = await get_session(session_id)
    
    return {
        "data": updated_session,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/{agent_id}/assistance/sessions/{session_id}")
async def delete_assistance_session(
    agent_id: str = Path(..., description="Agent ID"),
    session_id: str = Path(..., description="Session ID"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Delete a session and all its messages.
    """
    client_id = current_user.get("client_id")
    
    # Verify session exists and belongs to agent
    session = await get_session(session_id)
    if not session or session.get("agent_id") != agent_id or session.get("client_id") != client_id:
        raise NotFoundError("session", session_id)
    
    success = await delete_session(session_id)
    if not success:
        raise ValidationError("Failed to delete session")
    
    return {
        "data": {"deleted": True, "session_id": session_id},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
