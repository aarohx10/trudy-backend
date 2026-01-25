"""
Agents Package
Modular agent endpoints organized by operation.
"""
from fastapi import APIRouter

# Import all sub-routers
from app.api.v1.agents import (
    list as list_module,
    get as get_module,
    create_draft,
    create,
    update,
    partial_update,
    delete,
    sync,
    test_call,
    ai_assist,
)

# Create main router
router = APIRouter()

# Include all sub-routers (they define their own paths)
router.include_router(list_module.router)
router.include_router(get_module.router)
router.include_router(create_draft.router)
router.include_router(create.router)
router.include_router(update.router)
router.include_router(partial_update.router)
router.include_router(delete.router)
router.include_router(sync.router)
router.include_router(test_call.router)
router.include_router(ai_assist.router)
