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
    """
    List all contact folders for current organization.
    
    CRITICAL: Filters by clerk_org_id to show shared contact folders across the team.
    If User A uploads a CSV, User B must see that folder in the Contacts sidebar.
    """
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Listing folders for org_id: {clerk_org_id}")
        
        # Use DatabaseAdminService to bypass RLS
        db = DatabaseAdminService()
        
        # CRITICAL: Filter by org_id instead of client_id - shows all organization folders
        try:
            folders = list(db.select("contact_folders", {"clerk_org_id": clerk_org_id}, order_by="created_at DESC"))
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Found {len(folders)} folder(s) for clerk_org_id: {clerk_org_id}")
            
            # Debug: Log first folder if found
            if folders:
                logger.info(f"[CONTACTS] [LIST_FOLDERS] First folder: {json.dumps(folders[0], indent=2, default=str)}")
            else:
                logger.warning(f"[CONTACTS] [LIST_FOLDERS] No folders found for clerk_org_id: {clerk_org_id}")
        except Exception as select_error:
            logger.error(f"[CONTACTS] [LIST_FOLDERS] Error selecting folders: {select_error}", exc_info=True)
            raise
        
        # Get contact count for each folder
        folders_with_counts = []
        for folder in folders:
            folder_id = folder.get('id')
            try:
                # Count contacts - filter by org_id to stay within organization
                contact_count = db.count("contacts", {"folder_id": folder_id, "clerk_org_id": clerk_org_id})
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
        
        # Step 1: Explicit Data Keys - Ensure exact response format
        response_payload = {
            "data": folders_with_counts,  # This must be a list
            "meta": {
                "request_id": str(uuid.uuid4()),
                "ts": datetime.utcnow().isoformat()
            }
        }
        
        # Step 1: Log the Raw Payload - Verify structure before return
        logger.info(f"[CONTACTS] [LIST_FOLDERS] FINAL_RESPONSE_PAYLOAD (first item): {json.dumps(folders_with_counts[:1] if folders_with_counts else [], indent=2, default=str)}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Response data type: {type(folders_with_counts).__name__}, length: {len(folders_with_counts)}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Full response structure: {json.dumps({'data_count': len(response_payload['data']), 'has_meta': 'meta' in response_payload, 'meta_keys': list(response_payload['meta'].keys())}, indent=2)}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Complete response payload: {json.dumps(response_payload, indent=2, default=str)}")
        
        return response_payload
        
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
