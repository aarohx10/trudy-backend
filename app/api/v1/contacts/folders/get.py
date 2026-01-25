"""
Get Contact Folder Endpoint
GET /contacts/folders/{folder_id} - Get single contact folder
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{folder_id}")
async def get_contact_folder(
    folder_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single contact folder with contact count"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get folder
        folder = db.select_one("contact_folders", {"id": folder_id, "client_id": client_id})
        if not folder:
            raise NotFoundError("contact_folder", folder_id)
        
        # Get contact count
        contact_count = db.count("contacts", {"folder_id": folder_id})
        folder["contact_count"] = contact_count
        
        return {
            "data": folder,
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
        logger.error(f"[CONTACTS] [FOLDERS] [GET] Failed to get folder (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to get folder: {str(e)}")
