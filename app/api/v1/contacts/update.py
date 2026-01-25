"""
Update Contact Endpoint
PUT /contacts/{contact_id} - Update contact
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
from app.models.schemas import (
    ResponseMeta,
    ContactUpdate,
)
from app.services.contact import validate_contact_data

logger = logging.getLogger(__name__)

router = APIRouter()


@router.put("/{contact_id}")
async def update_contact(
    contact_id: str,
    contact_data: ContactUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update contact"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Verify contact exists and belongs to client
        contact = db.select_one("contacts", {"id": contact_id, "client_id": client_id})
        if not contact:
            raise NotFoundError("contact", contact_id)
        
        # If folder_id is being updated, verify new folder exists and belongs to client
        if contact_data.folder_id and contact_data.folder_id != contact.get("folder_id"):
            folder = db.select_one("contact_folders", {"id": contact_data.folder_id, "client_id": client_id})
            if not folder:
                raise NotFoundError("contact_folder", contact_data.folder_id)
        
        # Build update data
        update_dict = contact_data.dict(exclude_none=True)
        if not update_dict:
            raise ValidationError("No fields to update")
        
        # If phone_number is being updated, validate it
        if "phone_number" in update_dict:
            # Merge with existing contact data for validation
            merged_data = {**contact, **update_dict}
            validated_data = validate_contact_data(merged_data)
            update_dict["phone_number"] = validated_data["phone_number"]
        
        update_dict["updated_at"] = datetime.utcnow().isoformat()
        
        # Update contact
        db.update("contacts", {"id": contact_id}, update_dict)
        
        # Get updated contact
        updated_contact = db.select_one("contacts", {"id": contact_id})
        
        return {
            "data": updated_contact,
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
        logger.error(f"[CONTACTS] [UPDATE] Failed to update contact (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ValidationError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to update contact: {str(e)}")
