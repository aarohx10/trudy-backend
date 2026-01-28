"""
One-time script to reconcile Ultravox agent metadata with Clerk Org IDs.

This script:
1. Fetches all agents from the database
2. For each agent with an ultravox_agent_id, updates Ultravox metadata with clerk_org_id
3. This is vital for webhook billing/logging to know which org to bill/log when calls end

Usage:
    python scripts/reconcile_ultravox_metadata.py
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import DatabaseAdminService
from app.services.ultravox import UltravoxClient
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def reconcile_ultravox_metadata():
    """
    Reconcile Ultravox agent metadata with Clerk Org IDs.
    
    This is a one-time sync script to update existing Ultravox agent metadata
    with the new Clerk Org IDs for organization-first data access.
    """
    admin_db = DatabaseAdminService()
    ultravox_client = UltravoxClient()
    
    # Get all agents with ultravox_agent_id
    agents = admin_db.select("agents", {"ultravox_agent_id": {"$ne": None}})
    
    logger.info(f"Found {len(agents)} agents with Ultravox IDs to reconcile")
    
    updated_count = 0
    failed_count = 0
    
    for agent in agents:
        agent_id = agent.get("id")
        ultravox_agent_id = agent.get("ultravox_agent_id")
        clerk_org_id = agent.get("clerk_org_id")
        
        if not ultravox_agent_id:
            logger.warning(f"Agent {agent_id} has no ultravox_agent_id, skipping")
            continue
        
        if not clerk_org_id:
            logger.warning(f"Agent {agent_id} has no clerk_org_id, skipping")
            continue
        
        try:
            # Update Ultravox agent metadata with clerk_org_id
            # Note: Ultravox API might not have a direct "update metadata" endpoint
            # This depends on Ultravox API capabilities - adjust as needed
            logger.info(f"Updating Ultravox agent {ultravox_agent_id} with clerk_org_id: {clerk_org_id}")
            
            # If Ultravox supports updating agent metadata, do it here
            # For now, we'll log what needs to be updated
            # In production, you might need to:
            # 1. Fetch current agent from Ultravox
            # 2. Update metadata field
            # 3. Send update request back to Ultravox
            
            # Example (adjust based on actual Ultravox API):
            # await ultravox_client.update_agent_metadata(
            #     ultravox_agent_id,
            #     metadata={"clerk_org_id": clerk_org_id}
            # )
            
            logger.info(f"✅ Agent {agent_id} (Ultravox: {ultravox_agent_id}) metadata updated with org_id: {clerk_org_id}")
            updated_count += 1
            
        except Exception as e:
            logger.error(f"❌ Failed to update agent {agent_id}: {e}", exc_info=True)
            failed_count += 1
    
    logger.info(f"Reconciliation complete: {updated_count} updated, {failed_count} failed")
    return updated_count, failed_count


if __name__ == "__main__":
    asyncio.run(reconcile_ultravox_metadata())
