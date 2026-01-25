"""
Bulk Create Contacts Endpoint
POST /contacts/bulk - Create multiple contacts in one request
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
    ContactBulkCreate,
)
from app.services.contact import validate_bulk_contacts

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/bulk")
async def bulk_create_contacts(
    bulk_data: ContactBulkCreate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create multiple contacts in one request"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        now = datetime.utcnow()
        
        if not bulk_data.contacts:
            raise ValidationError("No contacts provided")
        
        # Verify all folders exist and belong to client
        folder_ids = set(c.folder_id for c in bulk_data.contacts)
        for folder_id in folder_ids:
            folder = db.select_one("contact_folders", {"id": folder_id, "client_id": client_id})
            if not folder:
                raise NotFoundError("contact_folder", folder_id)
        
        # Validate all contacts before creating any
        contacts_dict = [c.dict(exclude_none=True) for c in bulk_data.contacts]
        valid_contacts, invalid_contacts = validate_bulk_contacts(contacts_dict)
        
        if not valid_contacts:
            raise ValidationError("No valid contacts to create")
        
        # Create all valid contacts
        created_contacts = []
        for contact_data in valid_contacts:
            contact_id = str(uuid.uuid4())
            contact_record = {
                "id": contact_id,
                "client_id": client_id,
                "folder_id": contact_data["folder_id"],
                "first_name": contact_data.get("first_name"),
                "last_name": contact_data.get("last_name"),
                "email": contact_data.get("email"),
                "phone_number": contact_data["phone_number"],
                "metadata": contact_data.get("metadata"),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            db.insert("contacts", contact_record)
            created_contacts.append(contact_record)
        
        return {
            "data": {
                "created": created_contacts,
                "successful": len(created_contacts),
                "failed": len(invalid_contacts),
                "errors": invalid_contacts,
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
        }
        logger.error(f"[CONTACTS] [BULK_CREATE] Failed to bulk create contacts (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError, NotFoundError)):
            raise
        raise ValidationError(f"Failed to bulk create contacts: {str(e)}")
