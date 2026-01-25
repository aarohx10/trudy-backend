"""
AI Assist Endpoint
POST /agents/ai-assist - AI assistance for agent creation/editing (uses OpenAI)
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json
import re

from app.core.auth import get_current_user
from app.core.exceptions import ValidationError
from app.core.config import settings
from app.models.schemas import (
    ResponseMeta,
    AgentAIAssistRequest,
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
