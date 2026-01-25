"""
List Contact Folders Endpoint
GET /contacts/folders - List all contact folders for current client
"""
from fastapi import APIRouter, Depends, Header, Query
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def list_contact_folders(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    sort_by: Optional[str] = Query("created_at", description="Sort by: name, created_at, contact_count"),
    order: Optional[str] = Query("desc", description="Order: asc or desc"),
):
    """List all contact folders for current client with contact counts"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get all folders for client
        folders = db.select("contact_folders", {"client_id": client_id})
        
        # Get contact count for each folder
        folders_with_counts = []
        for folder in folders:
            contact_count = db.count("contacts", {"folder_id": folder["id"]})
            folder["contact_count"] = contact_count
            folders_with_counts.append(folder)
        
        # Sort folders
        reverse_order = order.lower() == "desc"
        if sort_by == "name":
            folders_with_counts.sort(key=lambda x: x.get("name", "").lower(), reverse=reverse_order)
        elif sort_by == "contact_count":
            folders_with_counts.sort(key=lambda x: x.get("contact_count", 0), reverse=reverse_order)
        else:  # created_at (default)
            folders_with_counts.sort(key=lambda x: x.get("created_at", ""), reverse=reverse_order)
        
        return {
            "data": folders_with_counts,
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
        logger.error(f"[CONTACTS] [FOLDERS] [LIST] Failed to list folders (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list folders: {str(e)}")
