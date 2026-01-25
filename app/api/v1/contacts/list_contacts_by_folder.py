"""
List Contacts by Folder Endpoint
GET /contacts/list-contacts?folder_id={id} - List contacts in a folder
Simple: Gets folder_id from query param, lists contacts matching folder_id and client_id.
"""
from fastapi import APIRouter, Depends, Header, Query
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, NotFoundError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/list-contacts", response_model=dict)
async def list_contacts_by_folder(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    folder_id: Optional[str] = Query(None, description="Folder ID to filter contacts"),
    search: Optional[str] = Query(None, description="Search by name, email, or phone"),
    page: Optional[int] = Query(1, ge=1, description="Page number"),
    limit: Optional[int] = Query(50, ge=1, le=100, description="Items per page"),
):
    """List contacts by folder - Simple: Filter by folder_id and client_id, optional search"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # If folder_id provided, verify it exists and belongs to client
        if folder_id:
            folder = db.select_one("contact_folders", {"id": folder_id, "client_id": client_id})
            if not folder:
                raise NotFoundError("contact_folder", folder_id)
        
        # Build filter
        filter_dict = {"client_id": client_id}
        if folder_id:
            filter_dict["folder_id"] = folder_id
        
        # Get all contacts matching filter
        contacts = list(db.select("contacts", filter_dict, order_by="created_at DESC"))
        
        # Apply search filter if provided (include new standard fields)
        if search:
            search_lower = search.lower()
            contacts = [
                c for c in contacts
                if (
                    (c.get("first_name", "") or "").lower().find(search_lower) != -1 or
                    (c.get("last_name", "") or "").lower().find(search_lower) != -1 or
                    (c.get("email", "") or "").lower().find(search_lower) != -1 or
                    (c.get("phone_number", "") or "").find(search) != -1 or
                    (c.get("company_name", "") or "").lower().find(search_lower) != -1 or
                    (c.get("industry", "") or "").lower().find(search_lower) != -1 or
                    (c.get("location", "") or "").lower().find(search_lower) != -1
                )
            ]
        
        # Get total count before pagination
        total = len(contacts)
        
        # Apply pagination
        start = (page - 1) * limit
        end = start + limit
        paginated_contacts = contacts[start:end]
        
        # Get folder info for each contact
        for contact in paginated_contacts:
            if contact.get("folder_id"):
                folder = db.select_one("contact_folders", {"id": contact["folder_id"]})
                if folder:
                    contact["folder"] = {
                        "id": folder["id"],
                        "name": folder.get("name"),
                    }
        
        return {
            "data": paginated_contacts,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }
        
    except Exception as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[CONTACTS] [LIST_CONTACTS] Failed to list contacts (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to list contacts: {str(e)}")
