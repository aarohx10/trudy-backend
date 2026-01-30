"""
Update Contact Endpoint
PUT /contacts/update-contact/{contact_id} - Update contact
Simple: Updates contact by ID, verifies ownership, validates data.
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


@router.put("/update-contact/{contact_id}")
async def update_contact(
    contact_id: str,
    contact_data: ContactUpdate,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Update contact"""
    # Permission check handled by require_admin_role dependency
    
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        
        # Verify contact exists and belongs to organization - filter by org_id instead of client_id
        contact = db.select_one("contacts", {"id": contact_id, "clerk_org_id": clerk_org_id})
        if not contact:
            raise NotFoundError("contact", contact_id)
        
        # If folder_id is being updated, verify new folder exists and belongs to organization
        if contact_data.folder_id and contact_data.folder_id != contact.get("folder_id"):
            folder = db.select_one("contact_folders", {"id": contact_data.folder_id, "clerk_org_id": clerk_org_id})
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
        
        # Update contact - filter by org_id to enforce org scoping
        db.update("contacts", {"id": contact_id, "clerk_org_id": clerk_org_id}, update_dict)
        
        # Get updated contact
        updated_contact = db.select_one("contacts", {"id": contact_id, "clerk_org_id": clerk_org_id})
        
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
