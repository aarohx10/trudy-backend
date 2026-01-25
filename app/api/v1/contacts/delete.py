"""
Delete Contact Endpoint
DELETE /contacts/delete-contact/{contact_id} - Delete contact
Simple: Deletes contact by ID, verifies ownership.
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.delete("/delete-contact/{contact_id}")
async def delete_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete contact"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Verify contact exists and belongs to client
        contact = db.select_one("contacts", {"id": contact_id, "client_id": client_id})
        if not contact:
            raise NotFoundError("contact", contact_id)
        
        # Delete contact
        db.delete("contacts", {"id": contact_id})
        
        return {
            "data": {
                "contact_id": contact_id,
                "deleted": True,
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
            "contact_id": contact_id,
        }
        logger.error(f"[CONTACTS] [DELETE] Failed to delete contact (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to delete contact: {str(e)}")
