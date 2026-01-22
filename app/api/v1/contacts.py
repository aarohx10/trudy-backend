"""
Contact Endpoints - Folder-Based Management
"""
from fastapi import APIRouter, Header, Depends, Query
from starlette.requests import Request
from typing import Optional, List
from datetime import datetime
import uuid

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.models.schemas import ResponseMeta
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# Folder Endpoints
# ============================================

@router.get("/folders")
async def list_folders(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List all contact folders for the current client"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    folders = db.select(
        "contact_folders",
        {"client_id": current_user["client_id"]},
        order_by="created_at DESC"
    )
    
    # Get contact counts for each folder
    folders_with_counts = []
    for folder in folders:
        contact_count = len(db.select("contacts", {"folder_id": folder["id"], "client_id": current_user["client_id"]}))
        folder["contact_count"] = contact_count
        folders_with_counts.append(folder)
    
    return {
        "data": folders_with_counts,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/folders")
async def create_folder(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create a new contact folder"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    body = await request.json()
    name = body.get("name", "").strip()
    description = body.get("description", "").strip() or None
    
    if not name:
        raise ValidationError("Folder name is required")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    folder_id = str(uuid.uuid4())
    folder_data = {
        "id": folder_id,
        "client_id": current_user["client_id"],
        "name": name,
        "description": description,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    db.insert("contact_folders", folder_data)
    
    return {
        "data": folder_data,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete a contact folder (orphans contacts by setting folder_id to NULL)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Verify folder exists and belongs to client
    folder = db.select_one("contact_folders", {"id": folder_id, "client_id": current_user["client_id"]})
    if not folder:
        raise NotFoundError("folder", folder_id)
    
    # Set folder_id to NULL for all contacts in this folder (orphan them)
    contacts = db.select("contacts", {"folder_id": folder_id, "client_id": current_user["client_id"]})
    for contact in contacts:
        db.update("contacts", {"id": contact["id"]}, {"folder_id": None})
    
    # Delete the folder
    db.delete("contact_folders", {"id": folder_id})
    
    return {
        "data": {"id": folder_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


# ============================================
# Contact Endpoints
# ============================================

@router.get("")
async def list_contacts(
    folder_id: Optional[str] = Query(None, description="Filter contacts by folder ID"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List contacts, optionally filtered by folder_id"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    filters = {"client_id": current_user["client_id"]}
    if folder_id:
        filters["folder_id"] = folder_id
    
    contacts = db.select("contacts", filters, order_by="created_at DESC")
    
    return {
        "data": contacts,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/{contact_id}")
async def get_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get a single contact"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    contact = db.select_one("contacts", {"id": contact_id, "client_id": current_user["client_id"]})
    if not contact:
        raise NotFoundError("contact", contact_id)
    
    return {
        "data": contact,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("")
async def create_contact(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create a new contact"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    body = await request.json()
    phone_number = body.get("phone_number", "").strip()
    folder_id = body.get("folder_id")
    
    if not phone_number:
        raise ValidationError("Phone number is required")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Validate folder if provided
    if folder_id:
        folder = db.select_one("contact_folders", {"id": folder_id, "client_id": current_user["client_id"]})
        if not folder:
            raise NotFoundError("folder", folder_id)
    
    contact_id = str(uuid.uuid4())
    contact_data = {
        "id": contact_id,
        "client_id": current_user["client_id"],
        "folder_id": folder_id,
        "phone_number": phone_number,
        "first_name": body.get("first_name") or None,
        "last_name": body.get("last_name") or None,
        "email": body.get("email") or None,
        "custom_fields": body.get("custom_fields") or {},
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    db.insert("contacts", contact_data)
    
    return {
        "data": contact_data,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/{contact_id}")
async def update_contact(
    contact_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update a contact"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Verify contact exists
    contact = db.select_one("contacts", {"id": contact_id, "client_id": current_user["client_id"]})
    if not contact:
        raise NotFoundError("contact", contact_id)
    
    body = await request.json()
    update_data = {}
    
    # Validate folder if being updated
    if "folder_id" in body:
        folder_id = body.get("folder_id")
        if folder_id:
            folder = db.select_one("contact_folders", {"id": folder_id, "client_id": current_user["client_id"]})
            if not folder:
                raise NotFoundError("folder", folder_id)
        update_data["folder_id"] = folder_id
    
    # Update other fields
    for field in ["first_name", "last_name", "email", "phone_number", "custom_fields"]:
        if field in body:
            update_data[field] = body[field]
    
    if not update_data:
        return {
            "data": contact,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("contacts", {"id": contact_id}, update_data)
    
    # Get updated contact
    updated_contact = db.select_one("contacts", {"id": contact_id, "client_id": current_user["client_id"]})
    
    return {
        "data": updated_contact,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/{contact_id}")
async def delete_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete a contact"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Verify contact exists
    contact = db.select_one("contacts", {"id": contact_id, "client_id": current_user["client_id"]})
    if not contact:
        raise NotFoundError("contact", contact_id)
    
    db.delete("contacts", {"id": contact_id})
    
    return {
        "data": {"id": contact_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
