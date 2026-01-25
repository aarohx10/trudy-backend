"""
Contacts Router
Simple, flat structure with explicit endpoint paths.
No nested routers, no route ordering concerns - just clear, explicit paths.
"""
from fastapi import APIRouter
from app.api.v1.contacts import (
    create_contact_folder,
    list_contact_folders,
    list_contacts_by_folder,
    add_contact_to_folder,
    update_contact,
    delete_contact,
    import_contacts,
    export,
)

router = APIRouter()

# Simple flat router - all explicit paths, no conflicts
router.include_router(create_contact_folder.router, tags=["contacts"])
router.include_router(list_contact_folders.router, tags=["contacts"])
router.include_router(list_contacts_by_folder.router, tags=["contacts"])
router.include_router(add_contact_to_folder.router, tags=["contacts"])
router.include_router(update_contact.router, tags=["contacts"])
router.include_router(delete_contact.router, tags=["contacts"])
router.include_router(import_contacts.router, tags=["contacts"])
router.include_router(export.router, tags=["contacts"])
