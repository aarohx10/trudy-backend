"""
Bulk Delete Contacts Endpoint
DELETE /contacts/bulk - Delete multiple contacts by IDs
"""
from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, ForbiddenError
from app.models.schemas import (
    ResponseMeta,
    ContactBulkDelete,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.delete("/bulk")
async def bulk_delete_contacts(
    bulk_data: ContactBulkDelete,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete multiple contacts by IDs"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        if not bulk_data.contact_ids:
            raise ValidationError("No contact IDs provided")
        
        # Verify all contacts belong to client
        # Get all contacts for client and filter by IDs
        all_contacts = db.select("contacts", {"client_id": client_id})
        contact_ids_found = {
            c["id"] for c in all_contacts 
            if c["id"] in bulk_data.contact_ids
        }
        contact_ids_not_found = set(bulk_data.contact_ids) - contact_ids_found
        
        if contact_ids_not_found:
            logger.warning(f"[CONTACTS] [BULK_DELETE] Some contacts not found: {contact_ids_not_found}")
        
        # Delete all found contacts
        deleted_count = 0
        for contact_id in contact_ids_found:
            db.delete("contacts", {"id": contact_id})
            deleted_count += 1
        
        return {
            "data": {
                "deleted": deleted_count,
                "requested": len(bulk_data.contact_ids),
                "not_found": list(contact_ids_not_found),
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
        }
        logger.error(f"[CONTACTS] [BULK_DELETE] Failed to bulk delete contacts (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, (ValidationError, ForbiddenError)):
            raise
        raise ValidationError(f"Failed to bulk delete contacts: {str(e)}")
