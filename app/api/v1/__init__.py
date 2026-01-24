"""
API v1 Router
"""
from fastapi import APIRouter
from app.api.v1 import auth, voices, voice_clone, knowledge_bases, calls, campaigns, tools, telephony, dashboard, export, files, logs, contacts, agents, agent_templates
from app.api.v1.webhooks import clerk as clerk_webhooks

api_router = APIRouter()

# Include all routers
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(voices.router, prefix="/voices", tags=["voices"])
api_router.include_router(voice_clone.router, prefix="/voice-clone", tags=["voice-clone"])
api_router.include_router(knowledge_bases.router, prefix="/kb", tags=["knowledge-bases"])
api_router.include_router(calls.router, prefix="/calls", tags=["calls"])
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(clerk_webhooks.router, tags=["webhooks"])  # Clerk webhooks at /api/v1/webhooks/clerk
api_router.include_router(tools.router, prefix="/tools", tags=["tools"])
api_router.include_router(contacts.router, prefix="/contacts", tags=["contacts"])
api_router.include_router(telephony.router, prefix="/telephony", tags=["telephony"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(export.router, prefix="/export", tags=["export"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(logs.router, tags=["logs"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(agent_templates.router, prefix="/agent-templates", tags=["agent-templates"])

