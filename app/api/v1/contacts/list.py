"""
List Contacts Endpoint
GET /contacts - List contacts, optionally filtered by folder_id
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
async def list_contacts(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    folder_id: Optional[str] = Query(None, description="Filter by folder ID"),
    search: Optional[str] = Query(None, description="Search by name, email, or phone"),
    page: Optional[int] = Query(1, ge=1, description="Page number"),
    limit: Optional[int] = Query(50, ge=1, le=100, description="Items per page"),
):
    """List contacts, optionally filtered by folder_id, with search and pagination"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Build filter
        filter_dict = {"client_id": client_id}
        if folder_id:
            filter_dict["folder_id"] = folder_id
        
        # Get all contacts matching filter
        contacts = list(db.select("contacts", filter_dict, order_by="created_at DESC"))
        
        # Apply search filter if provided
        if search:
            search_lower = search.lower()
            contacts = [
                c for c in contacts
                if (
                    (c.get("first_name", "") or "").lower().find(search_lower) != -1 or
                    (c.get("last_name", "") or "").lower().find(search_lower) != -1 or
                    (c.get("email", "") or "").lower().find(search_lower) != -1 or
                    (c.get("phone_number", "") or "").find(search) != -1
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
        logger.error(f"[CONTACTS] [LIST] Failed to list contacts (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list contacts: {str(e)}")
