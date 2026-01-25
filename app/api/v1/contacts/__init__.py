"""
Contacts Router
Aggregates all contact-related endpoints
"""
from fastapi import APIRouter
from app.api.v1.contacts import folders
from app.api.v1.contacts import list as list_contacts, create, get, update, delete
from app.api.v1.contacts import bulk_create, bulk_delete, import_contacts, export

router = APIRouter()

# Include folder router with prefix
router.include_router(folders.router, prefix="/folders", tags=["contact-folders"])

# Include contact routers
router.include_router(list_contacts.router, tags=["contacts"])
router.include_router(create.router, tags=["contacts"])
router.include_router(get.router, tags=["contacts"])
router.include_router(update.router, tags=["contacts"])
router.include_router(delete.router, tags=["contacts"])

# Include bulk operations
router.include_router(bulk_create.router, tags=["contacts"])
router.include_router(bulk_delete.router, tags=["contacts"])

# Include import/export
router.include_router(import_contacts.router, tags=["contacts"])
router.include_router(export.router, tags=["contacts"])
