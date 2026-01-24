"""
Agent Templates Endpoints
Read-only endpoints for agent templates.
"""
from fastapi import APIRouter, Depends
from typing import Optional
from datetime import datetime
import uuid

from app.core.auth import get_current_user
from app.core.exceptions import NotFoundError, ValidationError
from app.core.database import DatabaseService
from app.models.schemas import ResponseMeta, AgentTemplateResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_agent_templates(
    current_user: dict = Depends(get_current_user),
):
    """List all available agent templates"""
    try:
        db = DatabaseService()
        # Templates are global (no client_id filter)
        templates = db.select(
            "agent_templates",
            {"is_active": True},
            order_by="name ASC"
        )
        
        return {
            "data": list(templates),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[AGENT_TEMPLATES] [LIST] Failed to list templates (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list agent templates: {str(e)}")


@router.get("/{template_id}")
async def get_agent_template(
    template_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get a single agent template"""
    try:
        db = DatabaseService()
        
        template = db.select_one("agent_templates", {"id": template_id, "is_active": True})
        
        if not template:
            raise NotFoundError("agent_template", template_id)
        
        return {
            "data": template,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "template_id": template_id,
        }
        logger.error(f"[AGENT_TEMPLATES] [GET] Failed to get template (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to get agent template: {str(e)}")
