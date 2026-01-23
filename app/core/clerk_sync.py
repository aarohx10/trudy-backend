"""
Clerk Organization Metadata Sync
Syncs client_id to Clerk organization metadata for faster lookups
"""
import logging
import httpx
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


async def sync_client_id_to_org_metadata(clerk_org_id: str, client_id: str) -> bool:
    """
    Update Clerk organization metadata with client_id
    
    Args:
        clerk_org_id: Clerk organization ID
        client_id: Database client ID (UUID)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        clerk_secret_key = getattr(settings, 'CLERK_SECRET_KEY', '')
        if not clerk_secret_key:
            logger.warning("CLERK_SECRET_KEY not configured, skipping metadata sync")
            return False
        
        # Update organization metadata via Clerk API
        # Note: Clerk API endpoint is /v1/organizations/{org_id} with PATCH
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"https://api.clerk.dev/v1/organizations/{clerk_org_id}",
                headers={
                    "Authorization": f"Bearer {clerk_secret_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "public_metadata": {
                        "client_id": client_id,
                    }
                },
                timeout=10.0,
            )
            response.raise_for_status()
            logger.info(f"Synced client_id {client_id} to Clerk org {clerk_org_id} metadata")
            return True
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "clerk_org_id": clerk_org_id,
            "client_id": client_id,
        }
        logger.error(f"[CLERK_SYNC] Failed to sync client_id to Clerk org metadata (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        return False

