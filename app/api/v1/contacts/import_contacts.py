"""
Import Contacts Endpoint
POST /contacts/import-contacts - Import contacts from CSV file or direct array
Simple: Parses CSV or accepts contact array, validates, inserts into folder.
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
from app.core.storage import get_file_path
from app.models.schemas import (
    ResponseMeta,
    ContactImportRequest,
    ContactImportResponse,
)
from app.services.contact import parse_csv_contacts, validate_bulk_contacts

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/import-contacts", response_model=dict)
async def import_contacts(
    import_data: ContactImportRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Import contacts from CSV file or direct array"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        now = datetime.utcnow()
        
        # Verify folder exists and belongs to client
        folder = db.select_one("contact_folders", {"id": import_data.folder_id, "client_id": client_id})
        if not folder:
            raise NotFoundError("contact_folder", import_data.folder_id)
        
        contacts_to_import = []
        
        # Handle file upload
        if import_data.file_key:
            try:
                file_path = get_file_path("uploads", import_data.file_key)
                with open(file_path, 'r', encoding='utf-8') as f:
                    csv_content = f.read()
                
                # Parse CSV
                parsed_contacts = parse_csv_contacts(csv_content)
                
                # Add folder_id to all contacts
                for contact in parsed_contacts:
                    contact["folder_id"] = import_data.folder_id
                    contacts_to_import.append(contact)
                    
            except Exception as e:
                import traceback
                error_details_raw = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "full_traceback": traceback.format_exc(),
                    "file_key": import_data.file_key,
                }
                logger.error(f"[CONTACTS] [IMPORT] Failed to read CSV file (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                raise ValidationError(f"Failed to read CSV file: {str(e)}")
        
        # Handle direct contacts array
        elif import_data.contacts:
            for contact in import_data.contacts:
                contact_dict = contact.dict(exclude_none=True)
                contact_dict["folder_id"] = import_data.folder_id
                contacts_to_import.append(contact_dict)
        else:
            raise ValidationError("Either file_key or contacts array must be provided")
        
        if not contacts_to_import:
            raise ValidationError("No contacts to import")
        
        # Validate all contacts
        valid_contacts, invalid_contacts = validate_bulk_contacts(contacts_to_import)
        
        if not valid_contacts:
            raise ValidationError("No valid contacts to import")
        
        # Create all valid contacts
        successful = 0
        for contact_data in valid_contacts:
            try:
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
                successful += 1
            except Exception as e:
                logger.warning(f"Failed to create contact: {str(e)}")
                invalid_contacts.append({
                    "index": len(invalid_contacts),
                    "contact": contact_data,
                    "error": str(e)
                })
        
        # Build errors list for response
        errors = []
        for invalid in invalid_contacts:
            errors.append({
                "row": invalid.get("index", 0) + 1,
                "error": invalid.get("error", "Validation failed")
            })
        
        response_data = ContactImportResponse(
            successful=successful,
            failed=len(invalid_contacts),
            errors=errors if errors else None,
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
        logger.error(f"[CONTACTS] [IMPORT] Failed to import contacts (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError, NotFoundError)):
            raise
        raise ValidationError(f"Failed to import contacts: {str(e)}")
