"""
Contacts Router
Simple, flat structure with explicit endpoint paths.
No nested routers, no route ordering concerns - just clear, explicit paths.
"""
from fastapi import APIRouter
from . import create_contact_folder
from . import list_contact_folders
from . import list_contacts_by_folder
from . import add_contact_to_folder
from . import update
from . import delete
from . import import_contacts
from . import export as export_contacts

router = APIRouter()

# Simple flat router - all explicit paths, no conflicts
router.include_router(create_contact_folder.router, tags=["contacts"])
router.include_router(list_contact_folders.router, tags=["contacts"])
router.include_router(list_contacts_by_folder.router, tags=["contacts"])
router.include_router(add_contact_to_folder.router, tags=["contacts"])
router.include_router(update.router, tags=["contacts"])
router.include_router(delete.router, tags=["contacts"])
router.include_router(import_contacts.router, tags=["contacts"])
router.include_router(export_contacts.router, tags=["contacts"])
