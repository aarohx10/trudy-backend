"""
List Contact Folders Endpoint
GET /contacts/list-folders - List all contact folders for current client
Simple: Lists folders matching client_id, calculates contact_count for each.
EXACTLY like the test script - direct Supabase client usage.
"""
from fastapi import APIRouter, Depends, Header, Query
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import get_supabase_admin_client
from app.core.exceptions import ValidationError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/list-folders", response_model=dict)
async def list_contact_folders(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    sort_by: Optional[str] = Query("created_at", description="Sort by: name, created_at, contact_count"),
    order: Optional[str] = Query("desc", description="Order: asc or desc"),
):
    """List all contact folders for current client - EXACTLY like test script"""
    try:
        client_id = current_user.get("client_id")
        if not client_id:
            raise ValidationError("client_id is required")
        
        # Use admin client directly - EXACTLY like test script
        supabase = get_supabase_admin_client()
        
        # Query EXACTLY like test script
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Querying for client_id={client_id}")
        response = supabase.table("contact_folders").select("*").eq("client_id", client_id).execute()
        
        folders = list(response.data) if response.data else []
        
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Found {len(folders)} folder(s)")
        
        if folders:
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Folder IDs: {[f.get('id') for f in folders]}")
        
        # Get contact count for each folder - EXACTLY like test script
        folders_with_counts = []
        for folder in folders:
            folder_id = folder.get('id')
            # Count contacts EXACTLY like test script
            contacts_response = supabase.table("contacts").select("*", count="exact").eq("folder_id", folder_id).execute()
            contact_count = contacts_response.count if hasattr(contacts_response, 'count') else (len(contacts_response.data) if contacts_response.data else 0)
            
            # Create a new dict to avoid mutating the original
            folder_dict = dict(folder)
            folder_dict["contact_count"] = contact_count
            folders_with_counts.append(folder_dict)
        
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
            ).dict(),
        }
        
    except Exception as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[CONTACTS] [LIST_FOLDERS] Failed to list folders (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list folders: {str(e)}")
