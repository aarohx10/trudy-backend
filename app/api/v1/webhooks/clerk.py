"""
Clerk Webhook Handler
Handles Clerk webhook events to sync user and organization data with database
"""
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional
import logging
import hmac
import hashlib
import json
from datetime import datetime
import uuid

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.core.exceptions import UnauthorizedError
from app.core.clerk_sync import sync_client_id_to_org_metadata

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/clerk", tags=["webhooks"])


def verify_clerk_webhook(request_body: bytes, svix_id: str, svix_timestamp: str, svix_signature: str) -> bool:
    """Verify Clerk webhook signature using Svix"""
    try:
        # Clerk uses Svix for webhook signing
        # The signature is in format: v1,<signature>
        if not svix_signature.startswith("v1,"):
            return False
        
        # Extract signature
        signature = svix_signature[3:]
        
        # Create signed payload
        signed_payload = f"{svix_id}.{svix_timestamp}.{request_body.decode('utf-8')}"
        
        # Get webhook secret from settings
        webhook_secret = getattr(settings, 'CLERK_WEBHOOK_SECRET', '')
        if not webhook_secret:
            logger.warning("CLERK_WEBHOOK_SECRET not configured, skipping webhook verification")
            return True  # Allow in development
        
        # Compute expected signature
        expected_signature = hmac.new(
            webhook_secret.encode('utf-8'),
            signed_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Compare signatures (constant-time comparison)
        return hmac.compare_digest(signature, expected_signature)
    except Exception as e:
        logger.error(f"Error verifying Clerk webhook signature: {e}")
        return False


@router.post("")
async def handle_clerk_webhook(
    request: Request,
    svix_id: Optional[str] = Header(None, alias="svix-id"),
    svix_timestamp: Optional[str] = Header(None, alias="svix-timestamp"),
    svix_signature: Optional[str] = Header(None, alias="svix-signature"),
):
    """Handle Clerk webhook events"""
    try:
        # Get request body
        body = await request.body()
        
        # Verify webhook signature
        if not verify_clerk_webhook(body, svix_id or "", svix_timestamp or "", svix_signature or ""):
            logger.warning("Invalid Clerk webhook signature")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        
        # Parse webhook payload
        payload = json.loads(body.decode('utf-8'))
        event_type = payload.get("type")
        data = payload.get("data", {})
        
        logger.info(f"Received Clerk webhook: {event_type}")
        
        # Use admin client to bypass RLS
        admin_db = get_supabase_admin_client()
        
        # Handle different event types
        if event_type == "user.created":
            await handle_user_created(admin_db, data)
        elif event_type == "user.updated":
            await handle_user_updated(admin_db, data)
        elif event_type == "user.deleted":
            await handle_user_deleted(admin_db, data)
        elif event_type == "organization.created":
            await handle_organization_created(admin_db, data)
        elif event_type == "organizationMembership.created":
            await handle_organization_membership_created(admin_db, data)
        elif event_type == "organizationMembership.updated":
            await handle_organization_membership_updated(admin_db, data)
        elif event_type == "organizationMembership.deleted":
            await handle_organization_membership_deleted(admin_db, data)
        else:
            logger.info(f"Unhandled Clerk webhook event type: {event_type}")
        
        return {"received": True}
        
    except Exception as e:
        logger.error(f"Error handling Clerk webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")


async def handle_user_created(admin_db, data: dict):
    """Handle user.created event - user will be created via /auth/me on first login"""
    clerk_user_id = data.get("id")
    email = data.get("email_addresses", [{}])[0].get("email_address", "") if data.get("email_addresses") else ""
    
    logger.info(f"User created in Clerk: {clerk_user_id}, email: {email}")
    # User will be created in database on first API call via /auth/me endpoint
    # This webhook is mainly for logging/monitoring


async def handle_user_updated(admin_db, data: dict):
    """Handle user.updated event - update user email if changed"""
    clerk_user_id = data.get("id")
    email = data.get("email_addresses", [{}])[0].get("email_address", "") if data.get("email_addresses") else ""
    
    # Update user email if exists
    user = admin_db.table("users").select("*").eq("clerk_user_id", clerk_user_id).execute()
    if user.data:
        admin_db.table("users").update({"email": email}).eq("clerk_user_id", clerk_user_id).execute()
        logger.info(f"Updated user email: {clerk_user_id} -> {email}")


async def handle_user_deleted(admin_db, data: dict):
    """Handle user.deleted event - soft delete user"""
    clerk_user_id = data.get("id")
    
    # Soft delete user (mark as deleted, don't hard delete to preserve audit trail)
    user = admin_db.table("users").select("*").eq("clerk_user_id", clerk_user_id).execute()
    if user.data:
        admin_db.table("users").update({
            "deleted_at": datetime.utcnow().isoformat(),
            "clerk_user_id": None  # Clear clerk_user_id to allow reuse
        }).eq("clerk_user_id", clerk_user_id).execute()
        logger.info(f"Soft deleted user: {clerk_user_id}")


async def handle_organization_created(admin_db, data: dict):
    """Handle organization.created event - create or link client"""
    clerk_org_id = data.get("id")
    org_name = data.get("name", "")
    org_slug = data.get("slug", "")
    
    logger.info(f"Organization created in Clerk: {clerk_org_id}, name: {org_name}")
    
    # Check if client already exists for this org
    existing = admin_db.table("clients").select("*").eq("clerk_organization_id", clerk_org_id).execute()
    if existing.data:
        logger.info(f"Client already exists for org: {clerk_org_id}")
        return
    
    # Create new client linked to organization
    # Note: We don't have email here, it will be set when first user joins
    client_id = str(uuid.uuid4())
    client_data = {
        "id": client_id,
        "name": org_name or org_slug or "New Organization",
        "email": "",  # Will be updated when first user joins
        "clerk_organization_id": clerk_org_id,
        "subscription_status": "active",
        "credits_balance": 0,
        "credits_ceiling": 10000,
    }
    admin_db.table("clients").insert(client_data).execute()
    logger.info(f"Created client for Clerk organization: {client_id}, org: {clerk_org_id}")
    
    # Sync client_id to organization metadata
    await sync_client_id_to_org_metadata(clerk_org_id, client_id)


async def handle_organization_membership_created(admin_db, data: dict):
    """Handle organizationMembership.created event - create user and link to client"""
    import asyncio
    clerk_user_id = data.get("public_user_data", {}).get("user_id", "")
    clerk_org_id = data.get("organization_id", "")
    role = data.get("role", "org:member")
    
    logger.info(f"Organization membership created: user={clerk_user_id}, org={clerk_org_id}, role={role}")
    
    # Retry logic to handle race conditions when multiple users join simultaneously
    max_retries = 3
    client_id = None
    
    for attempt in range(max_retries):
        # Find client by organization
        org_client = admin_db.table("clients").select("*").eq("clerk_organization_id", clerk_org_id).execute()
        
        if org_client.data:
            # Client exists - use it
            client_id = org_client.data[0].get("id")
            logger.info(f"Found existing client for org: {clerk_org_id}, client_id: {client_id}")
            break
        
        # Client doesn't exist - wait before retrying (exponential backoff)
        if attempt < max_retries - 1:
            wait_time = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
            logger.info(f"Client not found for org: {clerk_org_id}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait_time)
            continue
        
        # Last attempt - create client if still doesn't exist
        logger.warning(f"Client not found for org: {clerk_org_id} after {max_retries} attempts, creating new client...")
        client_id = str(uuid.uuid4())
        client_data = {
            "id": client_id,
            "name": "New Organization",
            "email": "",
            "clerk_organization_id": clerk_org_id,
            "subscription_status": "active",
            "credits_balance": 0,
            "credits_ceiling": 10000,
        }
        
        try:
            admin_db.table("clients").insert(client_data).execute()
            logger.info(f"Created new client for org: {clerk_org_id}, client_id: {client_id}")
            # Sync client_id to organization metadata
            await sync_client_id_to_org_metadata(clerk_org_id, client_id)
        except Exception as e:
            # If insert fails (e.g., duplicate key), try to fetch existing client one more time
            error_str = str(e)
            if "23505" in error_str or "duplicate" in error_str.lower() or "unique" in error_str.lower():
                logger.info(f"Client creation failed (likely race condition), fetching existing client...")
                org_client = admin_db.table("clients").select("*").eq("clerk_organization_id", clerk_org_id).execute()
                if org_client.data:
                    client_id = org_client.data[0].get("id")
                    logger.info(f"Found existing client after race condition: {client_id}")
                    break
            raise e
    
    if not client_id:
        raise ValueError(f"Failed to get or create client for organization: {clerk_org_id}")
    
    # Check if user exists
    user = admin_db.table("users").select("*").eq("clerk_user_id", clerk_user_id).execute()
    if user.data:
        # Update existing user's client_id and role
        db_role = "client_admin" if role == "org:admin" else "client_user"
        admin_db.table("users").update({
            "client_id": client_id,
            "role": db_role
        }).eq("clerk_user_id", clerk_user_id).execute()
        logger.info(f"Updated user client and role: {clerk_user_id} -> client: {client_id}, role: {db_role}")
    else:
        # User will be created on first login via /auth/me
        logger.info(f"User not found in database, will be created on first login: {clerk_user_id}")


async def handle_organization_membership_updated(admin_db, data: dict):
    """Handle organizationMembership.updated event - update user role"""
    clerk_user_id = data.get("public_user_data", {}).get("user_id", "")
    role = data.get("role", "org:member")
    
    # Update user role
    db_role = "client_admin" if role == "org:admin" else "client_user"
    user = admin_db.table("users").select("*").eq("clerk_user_id", clerk_user_id).execute()
    if user.data:
        admin_db.table("users").update({"role": db_role}).eq("clerk_user_id", clerk_user_id).execute()
        logger.info(f"Updated user role: {clerk_user_id} -> {db_role}")


async def handle_organization_membership_deleted(admin_db, data: dict):
    """Handle organizationMembership.deleted event - remove user from organization"""
    clerk_user_id = data.get("public_user_data", {}).get("user_id", "")
    clerk_org_id = data.get("organization_id", "")
    
    logger.info(f"Organization membership deleted: user={clerk_user_id}, org={clerk_org_id}")
    
    # Find user and their client
    user = admin_db.table("users").select("*").eq("clerk_user_id", clerk_user_id).execute()
    if user.data:
        client_id = user.data[0].get("client_id")
        # Check if client is linked to this org
        client = admin_db.table("clients").select("*").eq("id", client_id).eq("clerk_organization_id", clerk_org_id).execute()
        if client.data:
            # User removed from organization - we could soft delete or keep for audit
            # For now, just log it - user might still have access via other means
            logger.info(f"User removed from organization: {clerk_user_id}, org: {clerk_org_id}")

