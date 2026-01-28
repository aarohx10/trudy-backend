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
# Note: Parent router in __init__.py adds /agents prefix, so these paths will be /agents + their path
router.include_router(list_module.router, tags=["agents"])
router.include_router(get_module.router, tags=["agents"])
router.include_router(create_draft.router, tags=["agents"])
router.include_router(create.router, tags=["agents"])
router.include_router(update.router, tags=["agents"])
router.include_router(partial_update.router, tags=["agents"])
router.include_router(delete.router, tags=["agents"])
router.include_router(sync.router, tags=["agents"])
router.include_router(test_call.router, tags=["agents"])
router.include_router(ai_assist.router, tags=["agents"])
