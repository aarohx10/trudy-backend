"""
AI Assistance Service
Centralized service for managing AI assistance chat sessions and generating prompt suggestions.
"""
import logging
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.core.database import get_supabase_admin_client
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_system_prompt_for_assistant() -> str:
    """
    Returns the system prompt for the AI assistant that helps users create/improve agents.
    
    Returns:
        str: System prompt for the AI assistant
    """
    return """You are an AI assistant helping users create and improve voice AI agents. Your role is to:
1. Understand the user's requirements for their agent
2. Analyze the current system prompt for gaps or improvements
3. Suggest specific, actionable changes to the system prompt
4. Ask clarifying questions when needed
5. Only suggest changes to the system_prompt field - never modify other agent settings

When suggesting a prompt change:
- Explain why the change is beneficial
- Show the improved prompt clearly
- Ask for user approval before applying changes
- Be concise but thorough
- Focus on making the prompt more effective, clear, and actionable

Important rules:
- NEVER modify agent settings other than system_prompt
- ALWAYS ask for approval before suggesting changes
- Provide clear explanations for your suggestions
- Be helpful and conversational, not robotic"""


async def create_chat_session(agent_id: str, client_id: str, model_id: str = "gpt-4o-mini", title: Optional[str] = None) -> str:
    """
    Create a new chat session for AI assistance.
    
    Args:
        agent_id: UUID of the agent
        client_id: UUID of the client
        model_id: AI model to use (default: "gpt-4o-mini")
        title: Optional session title (auto-generated if not provided)
    
    Returns:
        str: Session ID (UUID)
    """
    admin_db = get_supabase_admin_client()
    session_id = str(uuid.uuid4())
    
    session_data = {
        "id": session_id,
        "agent_id": agent_id,
        "client_id": client_id,
        "model_id": model_id,
        "title": title or f"Session {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    try:
        admin_db.table("agent_assistance_sessions").insert(session_data).execute()
        logger.info(f"Created assistance session {session_id} for agent {agent_id}")
        return session_id
    except Exception as e:
        logger.error(f"Failed to create assistance session: {e}", exc_info=True)
        raise


async def add_message(session_id: str, role: str, content: str, suggested_prompt_change: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Add a message to a chat session.
    
    Args:
        session_id: UUID of the session
        role: "user" or "assistant"
        content: Message content
        suggested_prompt_change: Optional suggested prompt change (for assistant messages)
        metadata: Optional metadata (model used, tokens, etc.)
    
    Returns:
        str: Message ID (UUID)
    """
    if role not in ["user", "assistant"]:
        raise ValueError(f"Invalid role: {role}. Must be 'user' or 'assistant'")
    
    admin_db = get_supabase_admin_client()
    message_id = str(uuid.uuid4())
    
    message_data = {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "suggested_prompt_change": suggested_prompt_change,
        "approval_state": "pending" if suggested_prompt_change else None,
        "metadata": metadata or {},
        "created_at": datetime.utcnow().isoformat(),
    }
    
    try:
        admin_db.table("agent_assistance_messages").insert(message_data).execute()
        
        # Update session updated_at timestamp
        admin_db.table("agent_assistance_sessions").update({
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", session_id).execute()
        
        logger.debug(f"Added {role} message to session {session_id}")
        return message_id
    except Exception as e:
        logger.error(f"Failed to add message to session {session_id}: {e}", exc_info=True)
        raise


async def get_chat_history(session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Get chat history for a session.
    
    Args:
        session_id: UUID of the session
        limit: Optional limit on number of messages (for context window management)
    
    Returns:
        List of message dictionaries ordered by created_at
    """
    admin_db = get_supabase_admin_client()
    
    query = admin_db.table("agent_assistance_messages").select("*").eq("session_id", session_id).order("created_at", desc=False)
    
    if limit:
        query = query.limit(limit)
    
    try:
        response = query.execute()
        messages = response.data if response.data else []
        logger.debug(f"Retrieved {len(messages)} messages for session {session_id}")
        return messages
    except Exception as e:
        logger.error(f"Failed to get chat history for session {session_id}: {e}", exc_info=True)
        raise


async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Get session details.
    
    Args:
        session_id: UUID of the session
    
    Returns:
        Session dictionary or None if not found
    """
    admin_db = get_supabase_admin_client()
    
    try:
        response = admin_db.table("agent_assistance_sessions").select("*").eq("id", session_id).single().execute()
        return response.data if response.data else None
    except Exception as e:
        logger.error(f"Failed to get session {session_id}: {e}", exc_info=True)
        return None


async def list_sessions(agent_id: str, client_id: str) -> List[Dict[str, Any]]:
    """
    List all sessions for an agent.
    
    Args:
        agent_id: UUID of the agent
        client_id: UUID of the client
    
    Returns:
        List of session dictionaries with message counts
    """
    admin_db = get_supabase_admin_client()
    
    try:
        # Get sessions
        sessions_response = admin_db.table("agent_assistance_sessions").select("*").eq("agent_id", agent_id).eq("client_id", client_id).order("created_at", desc=True).execute()
        sessions = sessions_response.data if sessions_response.data else []
        
        # Get message counts for each session
        for session in sessions:
            messages_response = admin_db.table("agent_assistance_messages").select("id", count="exact").eq("session_id", session["id"]).execute()
            session["message_count"] = messages_response.count if messages_response.count else 0
        
        logger.debug(f"Retrieved {len(sessions)} sessions for agent {agent_id}")
        return sessions
    except Exception as e:
        logger.error(f"Failed to list sessions for agent {agent_id}: {e}", exc_info=True)
        raise


async def update_session_title(session_id: str, title: str) -> bool:
    """
    Update session title.
    
    Args:
        session_id: UUID of the session
        title: New title
    
    Returns:
        bool: True if successful
    """
    admin_db = get_supabase_admin_client()
    
    try:
        admin_db.table("agent_assistance_sessions").update({
            "title": title,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", session_id).execute()
        logger.debug(f"Updated session {session_id} title to: {title}")
        return True
    except Exception as e:
        logger.error(f"Failed to update session title: {e}", exc_info=True)
        return False


async def delete_session(session_id: str) -> bool:
    """
    Delete a session and all its messages.
    
    Args:
        session_id: UUID of the session
    
    Returns:
        bool: True if successful
    """
    admin_db = get_supabase_admin_client()
    
    try:
        # Messages will be deleted via CASCADE
        admin_db.table("agent_assistance_sessions").delete().eq("id", session_id).execute()
        logger.info(f"Deleted session {session_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete session {session_id}: {e}", exc_info=True)
        return False


async def update_message_approval_state(message_id: str, approval_state: str) -> bool:
    """
    Update the approval state of a message with a suggested prompt change.
    
    Args:
        message_id: UUID of the message
        approval_state: "approved" or "rejected"
    
    Returns:
        bool: True if successful
    """
    if approval_state not in ["approved", "rejected"]:
        raise ValueError(f"Invalid approval_state: {approval_state}. Must be 'approved' or 'rejected'")
    
    admin_db = get_supabase_admin_client()
    
    try:
        admin_db.table("agent_assistance_messages").update({
            "approval_state": approval_state
        }).eq("id", message_id).execute()
        logger.info(f"Updated message {message_id} approval state to: {approval_state}")
        return True
    except Exception as e:
        logger.error(f"Failed to update message approval state: {e}", exc_info=True)
        return False


async def get_message_with_suggestion(message_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a message that contains a suggested prompt change.
    
    Args:
        message_id: UUID of the message
    
    Returns:
        Message dictionary or None if not found
    """
    admin_db = get_supabase_admin_client()
    
    try:
        response = admin_db.table("agent_assistance_messages").select("*").eq("id", message_id).single().execute()
        return response.data if response.data else None
    except Exception as e:
        logger.error(f"Failed to get message {message_id}: {e}", exc_info=True)
        return None
