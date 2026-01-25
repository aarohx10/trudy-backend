"""
Contact Folders Router
Aggregates all folder-related endpoints
"""
from fastapi import APIRouter
from app.api.v1.contacts.folders import create, list as list_folders, get, update, delete

router = APIRouter()

# Include all folder routers
router.include_router(create.router, tags=["contact-folders"])
router.include_router(list_folders.router, tags=["contact-folders"])
router.include_router(get.router, tags=["contact-folders"])
router.include_router(update.router, tags=["contact-folders"])
router.include_router(delete.router, tags=["contact-folders"])
