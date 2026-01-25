"""
Get Contact Endpoint
GET /contacts/{contact_id} - Get single contact
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{contact_id}")
async def get_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single contact with folder info"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get contact
        contact = db.select_one("contacts", {"id": contact_id, "client_id": client_id})
        if not contact:
            raise NotFoundError("contact", contact_id)
        
        # Get folder info
        if contact.get("folder_id"):
            folder = db.select_one("contact_folders", {"id": contact["folder_id"]})
            if folder:
                contact["folder"] = {
                    "id": folder["id"],
                    "name": folder.get("name"),
                }
        
        return {
            "data": contact,
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
        logger.error(f"[CONTACTS] [GET] Failed to get contact (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to get contact: {str(e)}")
