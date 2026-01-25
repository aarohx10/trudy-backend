"""
Update Contact Folder Endpoint
PUT /contacts/folders/{folder_id} - Update contact folder
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
    ContactFolderUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.put("/{folder_id}")
async def update_contact_folder(
    folder_id: str,
    folder_data: ContactFolderUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update contact folder"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Verify folder exists and belongs to client
        folder = db.select_one("contact_folders", {"id": folder_id, "client_id": client_id})
        if not folder:
            raise NotFoundError("contact_folder", folder_id)
        
        # Build update data
        update_data = folder_data.dict(exclude_none=True)
        if not update_data:
            raise ValidationError("No fields to update")
        
        update_data["updated_at"] = datetime.utcnow().isoformat()
        
        # Update folder
        db.update("contact_folders", {"id": folder_id}, update_data)
        
        # Get updated folder
        updated_folder = db.select_one("contact_folders", {"id": folder_id})
        contact_count = db.count("contacts", {"folder_id": folder_id})
        updated_folder["contact_count"] = contact_count
        
        return {
            "data": updated_folder,
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
            "folder_id": folder_id,
        }
        logger.error(f"[CONTACTS] [FOLDERS] [UPDATE] Failed to update folder (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ValidationError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to update folder: {str(e)}")
