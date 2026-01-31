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
from app.core.permissions import require_admin_role
from app.core.database import DatabaseAdminService
from app.core.exceptions import ValidationError, ForbiddenError, NotFoundError
from app.core.storage import get_file_path
from app.models.schemas import (
    ResponseMeta,
    ContactImportRequest,
    ContactImportResponse,
)
from app.services.contact import parse_csv_contacts, validate_bulk_contacts
import base64

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/import-contacts", response_model=dict)
async def import_contacts(
    import_data: ContactImportRequest,
    current_user: dict = Depends(require_admin_role),
):
    """Import contacts from CSV file (base64) or direct array with dynamic field mapping"""
    # Permission check handled by require_admin_role dependency
    
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Use DatabaseAdminService for bulk operations and consistency
        db = DatabaseAdminService()
        now = datetime.utcnow()
        
        # Verify folder exists and belongs to organization - filter by org_id instead of client_id
        folder = db.select_one("contact_folders", {"id": import_data.folder_id, "clerk_org_id": clerk_org_id})
        if not folder:
            raise NotFoundError("contact_folder", import_data.folder_id)
        
        contacts_to_import = []
        
        # Handle base64 CSV file upload (NEW - preferred method)
        if import_data.base64_file:
            try:
                # Decode base64 to get CSV content
                csv_content_bytes = base64.b64decode(import_data.base64_file)
                csv_content = csv_content_bytes.decode('utf-8')
                
                logger.info(f"[CONTACTS] [IMPORT] Decoded base64 CSV, length: {len(csv_content)} chars")
                
                # Parse CSV with mapping config
                parsed_contacts = parse_csv_contacts(csv_content, import_data.mapping_config)
                
                # Add folder_id to all contacts (clerk_org_id will be added during insert)
                for contact in parsed_contacts:
                    contact["folder_id"] = import_data.folder_id
                    contacts_to_import.append(contact)
                    
                logger.info(f"[CONTACTS] [IMPORT] Parsed {len(contacts_to_import)} contacts from base64 CSV")
                    
            except Exception as e:
                import traceback
                error_details_raw = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "full_traceback": traceback.format_exc(),
                    "filename": import_data.filename,
                }
                logger.error(f"[CONTACTS] [IMPORT] Failed to decode/parse base64 CSV (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                raise ValidationError(f"Failed to decode/parse CSV file: {str(e)}")
        
        # Handle legacy file_key upload
        elif import_data.file_key:
            try:
                file_path = get_file_path("uploads", import_data.file_key)
                with open(file_path, 'r', encoding='utf-8') as f:
                    csv_content = f.read()
                
                # Parse CSV with mapping config
                parsed_contacts = parse_csv_contacts(csv_content, import_data.mapping_config)
                
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
        
        # Handle direct contacts array (legacy)
        elif import_data.contacts:
            for contact in import_data.contacts:
                contact_dict = contact.dict(exclude_none=True)
                contact_dict["folder_id"] = import_data.folder_id
                contacts_to_import.append(contact_dict)
        else:
            raise ValidationError("Either base64_file, file_key, or contacts array must be provided")
        
        if not contacts_to_import:
            raise ValidationError("No contacts to import")
        
        # Validate all contacts
        valid_contacts, invalid_contacts = validate_bulk_contacts(contacts_to_import)
        
        if not valid_contacts:
            raise ValidationError("No valid contacts to import")
        
        # Bulk Upsert: Use DatabaseAdminService bulk_insert for performance
        logger.info(f"[CONTACTS] [IMPORT] Bulk inserting {len(valid_contacts)} contacts")
        
        # Prepare records for bulk insert - use clerk_org_id only (organization-first approach)
        contact_records = []
        for contact_data in valid_contacts:
            contact_id = str(uuid.uuid4())
            contact_record = {
                "id": contact_id,
                "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
                "folder_id": contact_data["folder_id"],
                "first_name": contact_data.get("first_name"),
                "last_name": contact_data.get("last_name"),
                "email": contact_data.get("email"),
                "phone_number": contact_data["phone_number"],
                # New standard fields
                "company_name": contact_data.get("company_name"),
                "industry": contact_data.get("industry"),
                "location": contact_data.get("location"),
                "pin_code": contact_data.get("pin_code"),
                "keywords": contact_data.get("keywords"),  # Array type
                # Metadata JSONB for custom fields (store as dict, Supabase handles JSONB conversion)
                "metadata": contact_data.get("metadata", {}) if contact_data.get("metadata") else None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            contact_records.append(contact_record)
        
        # Perform bulk insert
        try:
            inserted_records = db.bulk_insert("contacts", contact_records)
            successful = len(inserted_records)
            logger.info(f"[CONTACTS] [IMPORT] Successfully bulk inserted {successful} contacts")
        except Exception as bulk_error:
            logger.error(f"[CONTACTS] [IMPORT] Bulk insert failed: {bulk_error}", exc_info=True)
            # Fallback to individual inserts
            logger.info(f"[CONTACTS] [IMPORT] Falling back to individual inserts")
            successful = 0
            for contact_record in contact_records:
                try:
                    db.insert("contacts", contact_record)
                    successful += 1
                except Exception as e:
                    logger.warning(f"Failed to create contact: {str(e)}")
                    invalid_contacts.append({
                        "index": len(invalid_contacts),
                        "contact": contact_record,
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
