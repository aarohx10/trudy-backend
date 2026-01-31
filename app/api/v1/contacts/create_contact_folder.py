"""
Create Contact Folder Endpoint
POST /contacts/create-folder - Create new contact folder
Simple: Creates folder in Supabase, links to client_id. That's it.
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
from app.core.exceptions import ValidationError, ForbiddenError
from app.models.schemas import (
    ResponseMeta,
    ContactFolderCreate,
    ContactFolderResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/create-folder", response_model=dict)
async def create_contact_folder(
    folder_data: ContactFolderCreate,
    current_user: dict = Depends(require_admin_role),
):
    """
    Create new contact folder - Simple: Insert to Supabase with org_id.
    
    CRITICAL: Contact folders are shared across the organization.
    If User A uploads a CSV, User B must see that folder in the Contacts sidebar.
    """
    # Permission check handled by require_admin_role dependency
    
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        
        # STEP 1: Explicit validation BEFORE creating folder_record
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 1] Extracting clerk_org_id from current_user | clerk_org_id={clerk_org_id}")
        
        if not clerk_org_id:
            logger.error(f"[CONTACTS] [CREATE_FOLDER] [ERROR] Missing clerk_org_id in current_user | current_user_keys={list(current_user.keys())}")
            raise ValidationError("Missing organization ID in token")
        
        # Strip whitespace and validate it's not empty
        clerk_org_id = str(clerk_org_id).strip()
        if not clerk_org_id:
            logger.error(f"[CONTACTS] [CREATE_FOLDER] [ERROR] clerk_org_id is empty after stripping | original_value={current_user.get('clerk_org_id')}")
            raise ValidationError("Organization ID cannot be empty")
        
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 2] ✅ clerk_org_id validated | clerk_org_id={clerk_org_id}")
        
        # Use DatabaseAdminService to match list endpoint (ensures consistency)
        db = DatabaseAdminService()
        now = datetime.utcnow()
        
        # STEP 3: Build folder record - use clerk_org_id only (organization-first approach)
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 3] Building folder_record | clerk_org_id={clerk_org_id}")
        
        if not clerk_org_id or not clerk_org_id.strip():
            logger.error(f"[CONTACTS] [CREATE_FOLDER] [ERROR] Invalid clerk_org_id before creating folder_record | clerk_org_id={clerk_org_id}")
            raise ValidationError(f"Invalid clerk_org_id: '{clerk_org_id}' - cannot be empty")
        
        folder_id = str(uuid.uuid4())
        folder_record = {
            "id": folder_id,
            "clerk_org_id": clerk_org_id.strip(),  # CRITICAL: Organization ID for data partitioning - ensure no whitespace
            "name": folder_data.name,
            "description": folder_data.description,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # STEP 4: Explicit validation AFTER setting clerk_org_id in folder_record
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 4] Validating folder_record.clerk_org_id | value={folder_record.get('clerk_org_id')}")
        
        if "clerk_org_id" not in folder_record:
            logger.error(f"[CONTACTS] [CREATE_FOLDER] [ERROR] clerk_org_id key missing from folder_record | keys={list(folder_record.keys())}")
            raise ValidationError("clerk_org_id is missing from folder_record")
        
        if not folder_record["clerk_org_id"] or not str(folder_record["clerk_org_id"]).strip():
            logger.error(f"[CONTACTS] [CREATE_FOLDER] [ERROR] clerk_org_id is empty in folder_record | folder_record={folder_record}")
            raise ValidationError(f"clerk_org_id cannot be empty in folder_record: '{folder_record.get('clerk_org_id')}'")
        
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 4] ✅ folder_record.clerk_org_id validated | value={folder_record.get('clerk_org_id')}")
        
        # STEP 5: Log complete folder_record before insert
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 5] Complete folder_record before insert | folder_id={folder_id} | clerk_org_id={folder_record.get('clerk_org_id')}")
        
        # Insert into database
        created_folder = db.insert("contact_folders", folder_record)
        
        # STEP 6: Verify clerk_org_id was saved correctly
        saved_clerk_org_id = created_folder.get('clerk_org_id') if created_folder else None
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 6] Folder inserted | folder_id={folder_id} | saved_clerk_org_id={saved_clerk_org_id}")
        
        if not saved_clerk_org_id or not str(saved_clerk_org_id).strip():
            logger.error(f"[CONTACTS] [CREATE_FOLDER] [ERROR] clerk_org_id is empty after insert! | folder_id={folder_id} | created_folder={created_folder}")
            raise ValidationError(f"clerk_org_id was not saved correctly: '{saved_clerk_org_id}'")
        
        logger.info(f"[CONTACTS] [CREATE_FOLDER] [STEP 6] ✅ Folder created successfully | folder_id={folder_id} | clerk_org_id={saved_clerk_org_id}")
        
        # Get contact count (0 initially) - filter by org_id for consistency
        contact_count = db.count("contacts", {"folder_id": folder_id, "clerk_org_id": clerk_org_id})
        
        # Build response
        response_data = ContactFolderResponse(
            id=folder_id,
            name=folder_data.name,
            description=folder_data.description,
            contact_count=contact_count,
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
        logger.error(f"[CONTACTS] [CREATE_FOLDER] Failed to create folder (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to create folder: {str(e)}")
