"""
Contacts Router
Aggregates all contact-related endpoints
"""
from fastapi import APIRouter
from app.api.v1.contacts import folders
from app.api.v1.contacts import list as list_contacts, create, get, update, delete
from app.api.v1.contacts import bulk_create, bulk_delete
from app.api.v1.contacts import import_contacts
from app.api.v1.contacts import export

router = APIRouter()

# IMPORTANT: Include folder router FIRST before any parameterized routes
# This ensures /contacts/folders matches before /contacts/{contact_id}
router.include_router(folders.router, prefix="/folders", tags=["contact-folders"])

# Include import/export routes BEFORE parameterized routes
router.include_router(import_contacts.router, tags=["contacts"])
router.include_router(export.router, tags=["contacts"])

# Include bulk operations BEFORE parameterized routes
router.include_router(bulk_create.router, tags=["contacts"])
router.include_router(bulk_delete.router, tags=["contacts"])

# Include list route (no parameters)
router.include_router(list_contacts.router, tags=["contacts"])

# Include parameterized routes LAST (these match /contacts/{contact_id})
router.include_router(create.router, tags=["contacts"])
router.include_router(get.router, tags=["contacts"])
router.include_router(update.router, tags=["contacts"])
router.include_router(delete.router, tags=["contacts"])
