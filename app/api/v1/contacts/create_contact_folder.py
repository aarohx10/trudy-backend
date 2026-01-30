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
    x_client_id: Optional[str] = Header(None),
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
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        client_id = current_user.get("client_id")  # Legacy field
        
        # Use DatabaseAdminService to match list endpoint (ensures consistency)
        db = DatabaseAdminService()
        now = datetime.utcnow()
        
        # Create folder record
        folder_id = str(uuid.uuid4())
        folder_record = {
            "id": folder_id,
            "client_id": client_id,  # Legacy field
            "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
            "name": folder_data.name,
            "description": folder_data.description,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # Insert into database
        db.insert("contact_folders", folder_record)
        
        # Get contact count (0 initially) - filter by org_id for consistency
        contact_count = db.count("contacts", {"folder_id": folder_id, "clerk_org_id": clerk_org_id})
        
        # Build response
        response_data = ContactFolderResponse(
            id=folder_id,
            client_id=client_id,
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
