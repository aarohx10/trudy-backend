"""
Add Contact to Folder Endpoint
POST /contacts/add-contact - Add contact to a folder
Simple: Gets folder_id from request body, validates folder belongs to client, inserts contact with that folder_id.
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, ForbiddenError, NotFoundError
from app.models.schemas import (
    ResponseMeta,
    ContactCreate,
    ContactResponse,
)
from app.services.contact import validate_contact_data

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/add-contact", response_model=dict)
async def add_contact_to_folder(
    contact_data: ContactCreate,
    current_user: dict = Depends(require_admin_role),
):
    """
    Add contact to folder - Simple: Verify folder, validate contact, insert with folder_id.
    
    CRITICAL: Contacts are shared across the organization.
    """
    # Permission check handled by require_admin_role dependency
    
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        
        # Verify folder exists and belongs to organization
        folder = db.select_one("contact_folders", {"id": contact_data.folder_id, "clerk_org_id": clerk_org_id})
        if not folder:
            raise NotFoundError("contact_folder", contact_data.folder_id)
        
        # Validate and normalize contact data (phone/email validation)
        contact_dict = contact_data.dict(exclude_none=True)
        validated_contact = validate_contact_data(contact_dict)
        
        # Create contact record (include new standard fields)
        now = datetime.utcnow()
        contact_id = str(uuid.uuid4())
        contact_record = {
            "id": contact_id,
            "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
            "folder_id": validated_contact["folder_id"],
            "first_name": validated_contact.get("first_name"),
            "last_name": validated_contact.get("last_name"),
            "email": validated_contact.get("email"),
            "phone_number": validated_contact["phone_number"],
            # New standard fields
            "company_name": validated_contact.get("company_name"),
            "industry": validated_contact.get("industry"),
            "location": validated_contact.get("location"),
            "pin_code": validated_contact.get("pin_code"),
            "keywords": validated_contact.get("keywords"),  # Array type
            "metadata": validated_contact.get("metadata", {}) if validated_contact.get("metadata") else None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # Insert into database
        db.insert("contacts", contact_record)
        
        # Build response (include new standard fields)
        response_data = ContactResponse(
            id=contact_id,
            folder_id=validated_contact["folder_id"],
            first_name=validated_contact.get("first_name"),
            last_name=validated_contact.get("last_name"),
            email=validated_contact.get("email"),
            phone_number=validated_contact["phone_number"],
            company_name=validated_contact.get("company_name"),
            industry=validated_contact.get("industry"),
            location=validated_contact.get("location"),
            pin_code=validated_contact.get("pin_code"),
            keywords=validated_contact.get("keywords"),
            metadata=validated_contact.get("metadata"),
            created_at=now,
            updated_at=now,
        )
        
        return {
            "data": response_data.dict(),
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
        logger.error(f"[CONTACTS] [ADD_CONTACT] Failed to add contact (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError, NotFoundError)):
            raise
        raise ValidationError(f"Failed to add contact: {str(e)}")
