"""
Partial Update Agent Endpoint
PATCH /agents/{agent_id} - Partial update agent (for auto-save)
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional

from app.core.auth import get_current_user
from app.models.schemas import AgentUpdate
from .update import update_agent

router = APIRouter()


@router.patch("/{agent_id}")
async def partial_update_agent(
    agent_id: str,
    agent_data: AgentUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Partial update agent (for auto-save) - same as PUT but more lenient"""
    # Reuse PUT logic
    return await update_agent(agent_id, agent_data, current_user)
