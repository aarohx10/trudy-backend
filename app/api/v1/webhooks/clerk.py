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
from app.core.clerk_sync import sync_client_id_to_org_metadata, get_clerk_org_metadata

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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[WEBHOOKS] [CLERK] Error verifying webhook signature (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "error_module": getattr(e, '__module__', None),
            "error_class": type(e).__name__,
            "full_traceback": traceback.format_exc(),
            "event_type": event_type if 'event_type' in locals() else None,
            "svix_id": svix_id,
        }
        logger.error(f"[WEBHOOKS] [CLERK] Error handling webhook (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
    """Handle organizationMembership.created event - create user and link to client
    
    CRITICAL: Uses client_id from Clerk org metadata (SINGLE CLIENT ID POLICY)
    If metadata is missing, logs critical error instead of creating rogue client
    """
    import asyncio
    clerk_user_id = data.get("public_user_data", {}).get("user_id", "")
    clerk_org_id = data.get("organization_id", "")
    role = data.get("role", "org:member")
    
    logger.info(f"Organization membership created: user={clerk_user_id}, org={clerk_org_id}, role={role}")
    
    # STEP 1: Check Clerk org metadata for client_id (SINGLE CLIENT ID POLICY)
    org_metadata = await get_clerk_org_metadata(clerk_org_id)
    client_id = None
    
    if org_metadata and org_metadata.get("public_metadata", {}).get("client_id"):
        metadata_client_id = org_metadata["public_metadata"]["client_id"]
        logger.info(f"Found client_id in Clerk org metadata: {metadata_client_id}")
        
        # Verify this client exists in database and is linked to this org
        org_client = admin_db.table("clients").select("*").eq("id", metadata_client_id).eq("clerk_organization_id", clerk_org_id).execute()
        if org_client.data:
            client_id = metadata_client_id
            logger.info(f"Using client_id from Clerk org metadata: {client_id}")
        else:
            logger.critical(f"CRITICAL: Client {metadata_client_id} from org metadata not found in database or not linked to org {clerk_org_id}. This indicates metadata desync!")
            # Don't create a new client - this is a critical error that needs manual intervention
            raise ValueError(f"Client {metadata_client_id} from org metadata not found in database. Metadata desync detected!")
    else:
        # Metadata missing - this is a critical error
        logger.critical(f"CRITICAL: Clerk org {clerk_org_id} has no client_id in public_metadata. This will cause 'ghost member' problem!")
        logger.critical(f"CRITICAL: Refusing to create user {clerk_user_id} without valid client_id. Admin must sync org metadata first.")
        # Don't create a rogue client - log critical error
        raise ValueError(f"Clerk org {clerk_org_id} missing client_id in metadata. Cannot create user without valid client_id. Please sync org metadata first.")
    
    if not client_id:
        raise ValueError(f"Failed to get client_id for organization: {clerk_org_id}")
    
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

