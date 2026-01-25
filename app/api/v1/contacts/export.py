"""
Export Contacts Endpoint
GET /contacts/export-contacts - Export contacts as CSV
Simple: Gets contacts by folder_id (optional), generates CSV, returns file.
"""
from fastapi import APIRouter, Depends, Header, Query, Response
from typing import Optional
from datetime import datetime
import uuid
import logging
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError
from app.models.schemas import ResponseMeta
from app.services.contact import generate_csv_contacts

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/export-contacts")
async def export_contacts(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    folder_id: Optional[str] = Query(None, description="Filter by folder ID"),
):
    """Export contacts as CSV, optionally filtered by folder_id"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Build filter
        filter_dict = {"client_id": client_id}
        if folder_id:
            # Verify folder exists and belongs to client
            folder = db.select_one("contact_folders", {"id": folder_id, "client_id": client_id})
            if not folder:
                raise ValidationError(f"Folder not found: {folder_id}")
            filter_dict["folder_id"] = folder_id
        
        # Get all contacts matching filter
        contacts = list(db.select("contacts", filter_dict, order_by="created_at DESC"))
        
        if not contacts:
            raise ValidationError("No contacts found to export")
        
        # Generate CSV
        csv_content = generate_csv_contacts(contacts)
        
        # Always return CSV content directly for simplicity
        # If return_url is requested, we can add that functionality later
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="contacts{f"_folder_{folder_id}" if folder_id else ""}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv"'
            }
        )
        
    except Exception as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[CONTACTS] [EXPORT] Failed to export contacts (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(f"Failed to export contacts: {str(e)}")
