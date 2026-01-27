"""
AI Assistance Chat Endpoint
POST /agents/{agent_id}/assistance/chat - Handle chat messages with AI assistant
"""
from fastapi import APIRouter, Depends, Header, Path
from typing import Optional
from datetime import datetime
import uuid
import logging
import json
import re

from app.core.auth import get_current_user
from app.core.database import DatabaseService, get_supabase_admin_client
from app.core.exceptions import ValidationError, NotFoundError, ForbiddenError
from app.core.config import settings
from app.models.schemas import ResponseMeta
from pydantic import BaseModel, Field
from app.services.ai_assistance import (
    create_chat_session,
    add_message,
    get_chat_history,
    get_session,
    get_system_prompt_for_assistant,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Try to import OpenAI for AI assistance
try:
    import openai
    openai_available = True
except ImportError:
    openai_available = False
    logger.warning("OpenAI library not available. AI assistance features will be disabled.")


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="Session ID (null for new session)")
    message: str = Field(..., min_length=1, description="User's message")
    model_id: str = Field("gpt-4o-mini", description="AI model to use")


@router.post("/{agent_id}/assistance/chat")
async def chat_with_assistant(
    agent_id: str = Path(..., description="Agent ID"),
    request_data: ChatRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Chat with AI assistant for agent creation/editing.
    
    Request Body:
    {
        "session_id": "uuid" | null,  # null = new session
        "message": "string",
        "model_id": "gpt-4o" | "gpt-4o-mini"
    }
    
    Response:
    {
        "data": {
            "session_id": "uuid",
            "message_id": "uuid",
            "assistant_message": "string",
            "suggested_prompt_change": "string" | null,
            "requires_approval": bool
        }
    }
    """
    if not openai_available or not settings.OPENAI_API_KEY:
        raise ValidationError("OpenAI is not configured. AI assistance is unavailable.")
    
    session_id = request_data.session_id
    user_message = request_data.message.strip()
    model_id = request_data.model_id
    
    if not user_message:
        raise ValidationError("Message cannot be empty")
    
    if model_id not in ["gpt-4o", "gpt-4o-mini"]:
        raise ValidationError(f"Unsupported model: {model_id}. Supported models: gpt-4o, gpt-4o-mini")
    
    client_id = current_user.get("client_id")
    admin_db = get_supabase_admin_client()
    
    # Verify agent exists and belongs to client
    agent = admin_db.table("agents").select("*").eq("id", agent_id).eq("client_id", client_id).single().execute()
    if not agent.data:
        raise NotFoundError("agent", agent_id)
    
    agent_data = agent.data
    
    # Get or create session
    if not session_id:
        session_id = await create_chat_session(agent_id, client_id, model_id)
    else:
        # Verify session exists and belongs to agent
        session = await get_session(session_id)
        if not session or session.get("agent_id") != agent_id:
            raise NotFoundError("session", session_id)
    
    # Save user message
    user_message_id = await add_message(session_id, "user", user_message)
    
    # Load chat history (last 20 messages for context)
    chat_history = await get_chat_history(session_id, limit=20)
    
    # Get current agent system_prompt
    current_prompt = agent_data.get("system_prompt", "")
    
    # Build messages for OpenAI
    system_prompt = get_system_prompt_for_assistant()
    
    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # Add context about current agent
    context_message = f"""Current Agent Context:
- Name: {agent_data.get('name', 'Untitled')}
- Description: {agent_data.get('description', 'No description')}
- Current System Prompt:
{current_prompt}

Your task is to help the user improve this agent's system prompt. Analyze the current prompt and suggest improvements when appropriate."""
    
    messages.append({"role": "system", "content": context_message})
    
    # Add chat history (convert to OpenAI format)
    for msg in chat_history:
        if msg.get("role") in ["user", "assistant"]:
            messages.append({
                "role": msg["role"],
                "content": msg.get("content", "")
            })
    
    # Add current user message
    messages.append({"role": "user", "content": user_message})
    
    # Call OpenAI
    try:
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        response = await client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.7,
        )
        
        assistant_content = response.choices[0].message.content
        
        # Parse response for prompt suggestions
        suggested_prompt_change = None
        requires_approval = False
        
        # Look for prompt suggestions in the response
        # Pattern: Look for code blocks or explicit "Here's the improved prompt:" markers
        prompt_patterns = [
            r'```(?:system_prompt|prompt|text)?\n(.*?)\n```',  # Code blocks
            r'(?:Here\'s|Here is|Improved prompt|Suggested prompt)[:\s]+\n(.*?)(?:\n\n|\n$|$)',  # Explicit markers
            r'<system_prompt>(.*?)</system_prompt>',  # XML tags
        ]
        
        for pattern in prompt_patterns:
            matches = re.findall(pattern, assistant_content, re.DOTALL | re.IGNORECASE)
            if matches:
                suggested_prompt_change = matches[0].strip()
                requires_approval = True
                break
        
        # If no pattern match, check if response explicitly mentions suggesting a change
        if not suggested_prompt_change and any(keyword in assistant_content.lower() for keyword in ["suggest", "improve", "change", "update", "revise"]):
            # Try to extract the prompt from the response more liberally
            # Look for a substantial block of text that could be a prompt
            lines = assistant_content.split('\n')
            prompt_candidates = []
            in_prompt_section = False
            
            for i, line in enumerate(lines):
                if any(marker in line.lower() for marker in ["improved prompt", "suggested prompt", "new prompt", "updated prompt"]):
                    in_prompt_section = True
                    continue
                if in_prompt_section and line.strip():
                    prompt_candidates.append(line)
                elif in_prompt_section and not line.strip() and prompt_candidates:
                    break
            
            if prompt_candidates:
                suggested_prompt_change = '\n'.join(prompt_candidates).strip()
                requires_approval = True
        
        # Save assistant message
        metadata = {
            "model_id": model_id,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                "completion_tokens": response.usage.completion_tokens if response.usage else None,
                "total_tokens": response.usage.total_tokens if response.usage else None,
            }
        }
        
        assistant_message_id = await add_message(
            session_id,
            "assistant",
            assistant_content,
            suggested_prompt_change=suggested_prompt_change,
            metadata=metadata
        )
        
        return {
            "data": {
                "session_id": session_id,
                "message_id": assistant_message_id,
                "assistant_message": assistant_content,
                "suggested_prompt_change": suggested_prompt_change,
                "requires_approval": requires_approval,
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
    except Exception as e:
        import traceback
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
            "agent_id": agent_id,
            "session_id": session_id,
        }
        logger.error(f"[AGENTS] [ASSISTANCE_CHAT] Failed to process chat (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to process chat message: {str(e)}")
