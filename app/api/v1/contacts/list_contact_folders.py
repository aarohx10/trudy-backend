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
            logger.error(f"[CONTACTS] [LIST_FOLDERS] No client_id in current_user: {current_user}")
            raise ValidationError("client_id is required")
        
        logger.info(f"[CONTACTS] [LIST_FOLDERS] ===== STARTING LIST REQUEST =====")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] client_id={client_id} (type: {type(client_id)})")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] current_user keys: {list(current_user.keys())}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] current_user full: {json.dumps(current_user, indent=2, default=str)}")
        
        # Use admin client directly - EXACTLY like test script
        try:
            supabase = get_supabase_admin_client()
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Admin client obtained successfully")
        except Exception as client_error:
            logger.error(f"[CONTACTS] [LIST_FOLDERS] Failed to get admin client: {client_error}", exc_info=True)
            raise
        
        # Query EXACTLY like test script - but also try without filter to debug
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Executing query: table('contact_folders').select('*').eq('client_id', '{client_id}')")
        
        # First, try querying ALL folders to see if we can access the table at all
        try:
            all_folders_response = supabase.table("contact_folders").select("*").limit(10).execute()
            logger.info(f"[CONTACTS] [LIST_FOLDERS] DEBUG: All folders query returned {len(all_folders_response.data) if all_folders_response.data else 0} folders")
            if all_folders_response.data:
                for f in all_folders_response.data:
                    logger.info(f"[CONTACTS] [LIST_FOLDERS] DEBUG: Found folder - ID: {f.get('id')}, client_id: {f.get('client_id')}, name: {f.get('name')}")
        except Exception as debug_error:
            logger.error(f"[CONTACTS] [LIST_FOLDERS] DEBUG: Failed to query all folders: {debug_error}", exc_info=True)
        
        # Now query with client_id filter
        try:
            response = supabase.table("contact_folders").select("*").eq("client_id", client_id).execute()
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Query executed, response type: {type(response)}")
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Response has data attribute: {hasattr(response, 'data')}")
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Response object: {response}")
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Response.data type: {type(response.data) if hasattr(response, 'data') else 'NO DATA ATTR'}")
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Response.data value: {response.data}")
        except Exception as query_error:
            logger.error(f"[CONTACTS] [LIST_FOLDERS] Query failed: {query_error}", exc_info=True)
            raise
        
        folders = list(response.data) if response.data else []
        
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Raw response.data type: {type(response.data)}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Raw response.data length: {len(response.data) if response.data else 0}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Processed folders list length: {len(folders)}")
        
        if folders:
            logger.info(f"[CONTACTS] [LIST_FOLDERS] Folder IDs: {[f.get('id') for f in folders]}")
            logger.info(f"[CONTACTS] [LIST_FOLDERS] First folder sample: {json.dumps(folders[0], indent=2, default=str)}")
        else:
            logger.warning(f"[CONTACTS] [LIST_FOLDERS] NO FOLDERS FOUND - This is the problem!")
            logger.warning(f"[CONTACTS] [LIST_FOLDERS] response.data value: {response.data}")
            logger.warning(f"[CONTACTS] [LIST_FOLDERS] response object: {response}")
            logger.warning(f"[CONTACTS] [LIST_FOLDERS] client_id being queried: {client_id}")
            logger.warning(f"[CONTACTS] [LIST_FOLDERS] client_id type: {type(client_id)}")
        
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
        
        # Build response - ensure it's a proper dict
        result_data = {
            "data": folders_with_counts,
            "meta": {
                "request_id": str(uuid.uuid4()),
                "ts": datetime.utcnow().isoformat(),
            }
        }
        
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Returning result with {len(folders_with_counts)} folder(s)")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Result keys: {list(result_data.keys())}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Result data length: {len(result_data.get('data', []))}")
        if folders_with_counts:
            logger.info(f"[CONTACTS] [LIST_FOLDERS] First folder in result: {json.dumps(folders_with_counts[0], indent=2, default=str)}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] Full result JSON: {json.dumps(result_data, indent=2, default=str)}")
        logger.info(f"[CONTACTS] [LIST_FOLDERS] ===== ENDING LIST REQUEST =====")
        
        return result_data
        
    except Exception as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[CONTACTS] [LIST_FOLDERS] Failed to list folders (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list folders: {str(e)}")
