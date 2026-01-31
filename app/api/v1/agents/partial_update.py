"""
Partial Update Agent Endpoint
PATCH /agents/{agent_id} - Partial update agent (for auto-save)
"""
from fastapi import APIRouter, Depends

from app.core.permissions import require_admin_role
from app.models.schemas import AgentUpdate
from .update import update_agent

router = APIRouter()


@router.patch("/{agent_id}")
async def partial_update_agent(
    agent_id: str,
    agent_data: AgentUpdate,
    current_user: dict = Depends(require_admin_role),
):
    """Partial update agent (for auto-save) - same as PUT but more lenient"""
    # Reuse PUT logic - update_agent expects require_admin_role dependency
    return await update_agent(agent_id, agent_data, current_user)
