"""
Create Contact Folder Endpoint
POST /contacts/folders - Create new contact folder
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, ForbiddenError
from app.models.schemas import (
    ResponseMeta,
    ContactFolderCreate,
    ContactFolderResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", response_model=dict)
async def create_contact_folder(
    folder_data: ContactFolderCreate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create new contact folder"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        now = datetime.utcnow()
        
        # Create folder record
        folder_id = str(uuid.uuid4())
        folder_record = {
            "id": folder_id,
            "client_id": client_id,
            "name": folder_data.name,
            "description": folder_data.description,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # Insert into database
        db.insert("contact_folders", folder_record)
        
        # Get contact count (0 initially)
        contact_count = db.count("contacts", {"folder_id": folder_id})
        
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
        logger.error(f"[CONTACTS] [FOLDERS] [CREATE] Failed to create folder (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to create folder: {str(e)}")
