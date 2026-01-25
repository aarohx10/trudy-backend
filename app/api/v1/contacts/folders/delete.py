"""
Delete Contact Folder Endpoint
DELETE /contacts/folders/{folder_id} - Delete contact folder and all associated contacts
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
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.delete("/{folder_id}")
async def delete_contact_folder(
    folder_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete contact folder and all associated contacts (CASCADE)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Verify folder exists and belongs to client
        folder = db.select_one("contact_folders", {"id": folder_id, "client_id": client_id})
        if not folder:
            raise NotFoundError("contact_folder", folder_id)
        
        # Get contact count before deletion
        contact_count = db.count("contacts", {"folder_id": folder_id})
        
        # Delete all contacts in folder (CASCADE)
        db.delete("contacts", {"folder_id": folder_id})
        
        # Delete folder
        db.delete("contact_folders", {"id": folder_id})
        
        return {
            "data": {
                "folder_id": folder_id,
                "deleted": True,
                "contacts_deleted": contact_count,
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
            "folder_id": folder_id,
        }
        logger.error(f"[CONTACTS] [FOLDERS] [DELETE] Failed to delete folder (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (NotFoundError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to delete folder: {str(e)}")
