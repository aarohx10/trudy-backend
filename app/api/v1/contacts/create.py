"""
Create Contact Endpoint
POST /contacts - Create new contact
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
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


@router.post("/", response_model=dict)
async def create_contact(
    contact_data: ContactCreate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create new contact"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Verify folder exists and belongs to client
        folder = db.select_one("contact_folders", {"id": contact_data.folder_id, "client_id": client_id})
        if not folder:
            raise NotFoundError("contact_folder", contact_data.folder_id)
        
        # Validate and normalize contact data
        contact_dict = contact_data.dict(exclude_none=True)
        validated_contact = validate_contact_data(contact_dict)
        
        # Create contact record
        now = datetime.utcnow()
        contact_id = str(uuid.uuid4())
        contact_record = {
            "id": contact_id,
            "client_id": client_id,
            "folder_id": validated_contact["folder_id"],
            "first_name": validated_contact.get("first_name"),
            "last_name": validated_contact.get("last_name"),
            "email": validated_contact.get("email"),
            "phone_number": validated_contact["phone_number"],
            "metadata": validated_contact.get("metadata"),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # Insert into database
        db.insert("contacts", contact_record)
        
        # Build response
        response_data = ContactResponse(
            id=contact_id,
            client_id=client_id,
            folder_id=validated_contact["folder_id"],
            first_name=validated_contact.get("first_name"),
            last_name=validated_contact.get("last_name"),
            email=validated_contact.get("email"),
            phone_number=validated_contact["phone_number"],
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
        logger.error(f"[CONTACTS] [CREATE] Failed to create contact (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError, NotFoundError)):
            raise
        raise ValidationError(f"Failed to create contact: {str(e)}")
