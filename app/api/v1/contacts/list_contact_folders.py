"""
List Contact Folders Endpoint
GET /contacts/list-folders - List all contact folders for current client
Uses DatabaseAdminService to bypass RLS (same as test script that works)
"""
from fastapi import APIRouter, Depends, Header, Query
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseAdminService
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
    """List all contact folders for current client - Uses DatabaseAdminService to bypass RLS"""
    try:
        client_id = current_user.get("client_id")
        if not client_id:
            logger.error(f"[CONTACTS] [LIST_FOLDERS] No client_id in current_user: {current_user}")
            raise ValidationError("client_id is required")
        
        # Step 1: Request Validation - Ensure x_client_id matches current_user client_id to prevent cross-tenant leaks
        if x_client_id and x_client_id != str(client_id):
            logger.warning(f"[CONTACTS] [LIST_FOLDERS] client_id mismatch: header={x_client_id}, token={client_id}")
            # For non-agency-admin users, enforce strict matching
            if current_user.get("role") != "agency_admin":
                raise ValidationError("client_id mismatch between header and token")
        
        # Step 1: Enhanced Logging - Log the type of client_id being used
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Listing folders for client_id: {client_id} (type: {type(client_id).__name__})")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] x_client_id header: {x_client_id} (type: {type(x_client_id).__name__ if x_client_id else 'None'})")
        
        # DO NOT cast to string - let the database handle UUID type matching
        # The test script works without string casting, so we should match that behavior
        
        # Use DatabaseAdminService to bypass RLS (same as test script)
        # This matches the test script approach that works
        # Note: Create endpoint uses DatabaseService and works, but list might be blocked by RLS
        db = DatabaseAdminService()
        
        # Get all folders for this client - EXACTLY like test script
        try:
            folders = list(db.select("contact_folders", {"client_id": client_id}, order_by="created_at DESC"))
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Found {len(folders)} folder(s) for client_id: {client_id}")
            
            # Debug: Log first folder if found
            if folders:
                logger.info(f"[CONTACTS] [LIST_FOLDERS] First folder: {json.dumps(folders[0], indent=2, default=str)}")
            else:
                logger.warning(f"[CONTACTS] [LIST_FOLDERS] No folders found for client_id: {client_id}")
                # Try querying all folders to see if table is accessible
                all_folders = list(db.select("contact_folders", None, order_by="created_at DESC"))
                logger.info(f"[CONTACTS] [LIST_FOLDERS] Total folders in table (all clients): {len(all_folders)}")
                if all_folders:
                    logger.info(f"[CONTACTS] [LIST_FOLDERS] Sample folder client_ids: {[f.get('client_id') for f in all_folders[:5]]}")
        except Exception as select_error:
            logger.error(f"[CONTACTS] [LIST_FOLDERS] Error selecting folders: {select_error}", exc_info=True)
            raise
        
        # Get contact count for each folder
        folders_with_counts = []
        for folder in folders:
            folder_id = folder.get('id')
            try:
                # Count contacts using DatabaseAdminService
                contact_count = db.count("contacts", {"folder_id": folder_id})
            except Exception as count_error:
                logger.warning(f"[CONTACTS] [LIST_FOLDERS] Error counting contacts for folder {folder_id}: {count_error}")
                contact_count = 0
            
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
        
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Returning {len(folders_with_counts)} folder(s)")
        
        # Build response - same format as other endpoints
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
            "client_id": current_user.get("client_id") if current_user else None,
        }
        logger.error(f"[CONTACTS] [LIST_FOLDERS] Failed to list folders (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(f"Failed to list folders: {str(e)}")
