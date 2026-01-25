"""
Test Call Endpoint
POST /agents/{agent_id}/test-call - Create WebRTC test call for agent
"""
from fastapi import APIRouter, Depends, Header, Body
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ProviderError
from app.models.schemas import (
    ResponseMeta,
    AgentTestCallRequest,
)
from app.services.ultravox import ultravox_client

logger = logging.getLogger(__name__)

router = APIRouter()


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
