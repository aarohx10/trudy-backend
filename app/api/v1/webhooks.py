"""
Webhook Endpoints (Ingress & Egress)
"""
from fastapi import APIRouter, Header, Depends
from starlette.requests import Request
from typing import Optional
from datetime import datetime
import uuid
import secrets
import json
import logging
import time

from app.core.auth import get_current_user
from app.core.permissions import require_admin_role
from app.core.database import DatabaseService, DatabaseAdminService
from app.core.config import settings
from app.core.webhooks import verify_ultravox_signature, verify_timestamp, verify_telnyx_signature, deliver_webhook
from app.core.events import (
    emit_voice_training_completed,
    emit_voice_training_failed,
    emit_call_started,
    emit_call_completed,
    emit_call_failed,
)
from app.core.exceptions import UnauthorizedError, ForbiddenError, NotFoundError, ValidationError
from app.services.webhook_handlers import EVENT_HANDLERS
import time

logger = logging.getLogger(__name__)
from app.models.schemas import (
    WebhookEndpointCreate,
    WebhookEndpointUpdate,
    WebhookEndpointResponse,
    ResponseMeta,
)
from app.core.config import settings

router = APIRouter()


# ============================================
# Webhook Ingress (from external services)
# ============================================

@router.post("/ultravox")
async def ultravox_webhook(
    request: Request,
    x_ultravox_signature: Optional[str] = Header(None),
    x_ultravox_timestamp: Optional[str] = Header(None),
):
    """
    Centralized Ultravox webhook orchestrator.
    Uses Strategy Pattern to route events to appropriate handlers.
    """
    start_time = time.time()
    admin_db = DatabaseAdminService()
    
    # Get raw body
    body = await request.body()
    body_str = body.decode("utf-8")
    
    # Get request headers for logging
    headers_dict = dict(request.headers)
    
    # Verify signature
    signature_valid = False
    if x_ultravox_signature and x_ultravox_timestamp:
        signature_valid = verify_ultravox_signature(
            x_ultravox_signature,
            x_ultravox_timestamp,
            body_str,
            settings.ULTRAVOX_WEBHOOK_SECRET,
        )
        
        if not signature_valid:
            logger.error("Invalid Ultravox webhook signature")
            raise UnauthorizedError("Invalid signature")
        
        if not verify_timestamp(x_ultravox_timestamp):
            logger.error("Ultravox webhook timestamp verification failed")
            raise UnauthorizedError("Timestamp verification failed")
    else:
        logger.error("Missing Ultravox webhook signature or timestamp")
        raise UnauthorizedError("Missing signature or timestamp")
    
    # Parse event
    try:
        event_data = json.loads(body_str)
    except json.JSONDecodeError as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "body_str": body_str[:500] if 'body_str' in locals() else None,
        }
        logger.error(f"[WEBHOOKS] [ULTRAVOX] Failed to parse webhook JSON (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError("Invalid JSON payload")
    
    event_type = event_data.get("event") or event_data.get("eventType")
    event_id = event_data.get("id") or event_data.get("event_id")
    
    if not event_type:
        logger.error("Ultravox webhook missing event type")
        raise ValidationError("Missing event type")
    
    # Log webhook event
    try:
        admin_db.insert(
            "webhook_logs",
            {
                "provider": "ultravox",
                "event_type": event_type,
                "event_id": event_id,
                "payload": event_data,
                "headers": headers_dict,
                "signature_valid": signature_valid,
                "processing_status": "pending",
            },
        )
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "event_type": event_type if 'event_type' in locals() else None,
            "event_id": event_id if 'event_id' in locals() else None,
        }
        logger.error(f"[WEBHOOKS] [ULTRAVOX] Failed to log webhook event (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        # Continue processing even if logging fails
    
    # Route to appropriate handler (Strategy Pattern)
    # Use DatabaseAdminService for webhook handlers (no user context)
    from app.core.database import DatabaseAdminService
    db = DatabaseAdminService()
    client_id_for_webhook = None
    processing_error = None
    
    try:
        handler = EVENT_HANDLERS.get(event_type)
        if handler:
            client_id_for_webhook = await handler(event_data, db)
            logger.info(f"Processed Ultravox webhook: {event_type} (client_id: {client_id_for_webhook})")
        else:
            logger.warning(f"Unknown Ultravox webhook event type: {event_type}")
            # Log but don't fail - new event types may be added by Ultravox
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "event_type": event_type,
            "event_id": event_id if 'event_id' in locals() else None,
        }
        processing_error = str(e)
        logger.error(f"[WEBHOOKS] [ULTRAVOX] Error processing webhook (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        # Don't raise - we want to return 200 to Ultravox even if processing fails
        # This prevents Ultravox from retrying
    
    # Update webhook log with processing result
    processing_time_ms = int((time.time() - start_time) * 1000)
    try:
        admin_db.update(
            "webhook_logs",
            {"event_id": event_id, "event_type": event_type, "provider": "ultravox"},
            {
                "processing_status": "processed" if not processing_error else "failed",
                "error_message": processing_error,
                "processing_time_ms": processing_time_ms,
            },
        )
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "log_id": log_id if 'log_id' in locals() else None,
        }
        logger.error(f"[WEBHOOKS] [ULTRAVOX] Failed to update webhook log (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
    
    # Trigger egress webhooks
    # CRITICAL: Use org_id for organization-first approach
    org_id_for_webhook = client_id_for_webhook  # Handler returns org_id (variable name kept for backward compatibility)
    if org_id_for_webhook:
        try:
            await trigger_egress_webhooks(
                org_id=org_id_for_webhook,  # CRITICAL: Organization ID (primary)
                event_type=event_type,
                event_data=event_data,
            )
        except Exception as e:
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "event_type": event_type,
            }
            logger.error(f"[WEBHOOKS] [ULTRAVOX] Failed to trigger egress webhooks (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            # Don't fail the webhook - egress is secondary
    
    return {"status": "ok"}


async def trigger_egress_webhooks(
    org_id: Optional[str] = None,
    event_type: str = "",
    event_data: dict = {},
) -> None:
    """
    Trigger egress webhooks for an organization.
    
    CRITICAL: Filters by clerk_org_id to trigger webhooks for the organization.
    Organization-first approach - org_id is required.
    """
    from app.core.database import DatabaseAdminService
    
    if not org_id:
        logger.warning("[WEBHOOKS] trigger_egress_webhooks called without org_id")
        return
    
    db = DatabaseAdminService()
    
    # CRITICAL: Filter by org_id (organization-first approach)
    filters = {"enabled": True, "clerk_org_id": org_id}
    
    # Get enabled webhook endpoints for this organization and event type
    endpoints = db.select(
        "webhook_endpoints",
        filters,
    )
    
    # Filter endpoints that subscribe to this event type
    matching_endpoints = [
        ep for ep in endpoints
        if event_type in (ep.get("event_types") or [])
    ]
    
    # Create webhook delivery tasks
    for endpoint in matching_endpoints:
        delivery_id = str(uuid.uuid4())
        
        # Create delivery record
        db.insert(
            "webhook_deliveries",
            {
                "id": delivery_id,
                "webhook_endpoint_id": endpoint["id"],
                "event_type": event_type,
                "payload": event_data,
                "status": "pending",
                "attempt": 1,
            },
        )
        
        # Queue webhook delivery
        # For now, we'll deliver directly. In production, use a queue system
        try:
            success, status_code, error = await deliver_webhook(
                url=endpoint["url"],
                payload={
                    "event": event_type,
                    "data": event_data,
                    "timestamp": datetime.utcnow().isoformat(),
                },
                secret=endpoint["secret"],
            )
            
            # Update delivery status
            if success:
                db.update(
                    "webhook_deliveries",
                    {"id": delivery_id},
                    {
                        "status": "delivered",
                        "response_code": status_code,
                        "delivered_at": datetime.utcnow().isoformat(),
                    },
                )
            else:
                db.update(
                    "webhook_deliveries",
                    {"id": delivery_id},
                    {
                        "status": "failed",
                        "response_code": status_code,
                        "error_message": error,
                    },
                )
                
        except Exception as e:
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "endpoint_id": endpoint_id if 'endpoint_id' in locals() else None,
                "webhook_url": endpoint.get("url") if 'endpoint' in locals() else None,
            }
            logger.error(f"[WEBHOOKS] [DELIVER] Error delivering webhook (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            db.update(
                "webhook_deliveries",
                {"id": delivery_id},
                {
                    "status": "failed",
                    "error_message": str(e),
                },
            )


@router.post("/telnyx")
async def telnyx_webhook(
    request: Request,
    x_telnyx_signature: Optional[str] = Header(None, alias="Telnyx-Signature"),
    x_telnyx_timestamp: Optional[str] = Header(None, alias="Telnyx-Timestamp"),
):
    """Receive webhook from Telnyx"""
    # Get raw body for signature verification
    body = await request.body()
    body_str = body.decode("utf-8")
    
    # Verify signature if secret is configured
    if settings.TELNYX_WEBHOOK_SECRET:
        if not x_telnyx_signature:
            logger.warning("Telnyx webhook received without signature header")
            raise UnauthorizedError("Missing signature")
        
        # Use timestamp from header or current time
        timestamp = x_telnyx_timestamp or str(int(time.time()))
        
        if not verify_telnyx_signature(
            x_telnyx_signature,
            timestamp,
            body_str,
            settings.TELNYX_WEBHOOK_SECRET,
        ):
            logger.error("Telnyx webhook signature verification failed")
            raise UnauthorizedError("Invalid signature")
        
        # Verify timestamp if provided
        if x_telnyx_timestamp and not verify_timestamp(x_telnyx_timestamp):
            logger.error("Telnyx webhook timestamp verification failed")
            raise UnauthorizedError("Timestamp verification failed")
        
        logger.info("Telnyx webhook signature verified successfully")
    else:
        logger.warning("TELNYX_WEBHOOK_SECRET not configured, skipping signature verification")
    
    # Parse event
    try:
        event_data = json.loads(body_str)
        event_type = event_data.get("event_type") or event_data.get("event", {}).get("event_type")
        
        logger.info(f"Received Telnyx webhook: {event_type}")
        
        # Use DatabaseAdminService for webhook handlers (no user context)
        from app.core.database import DatabaseAdminService
        db = DatabaseAdminService()
        
        # Handle Telnyx events (number events, call events, etc.)
        # Implementation details to be defined based on Telnyx webhook requirements
        # For now, just log the event
        
        return {"status": "ok"}
    except json.JSONDecodeError as e:
        import traceback
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "body_str": body_str[:500] if 'body_str' in locals() else None,
        }
        logger.error(f"[WEBHOOKS] [TELNYX] Failed to parse webhook body (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError("Invalid JSON payload")


# ============================================
# Webhook Egress (client webhooks)
# ============================================

@router.post("")
async def create_webhook_endpoint(
    webhook_data: WebhookEndpointCreate,
    current_user: dict = Depends(require_admin_role),
):
    """Create webhook endpoint"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    
    # STEP 1: Explicit validation BEFORE creating webhook_record
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 1] Extracting clerk_org_id from current_user | clerk_org_id={clerk_org_id}")
    
    if not clerk_org_id:
        logger.error(f"[WEBHOOKS] [CREATE] [ERROR] Missing clerk_org_id in current_user | current_user_keys={list(current_user.keys())}")
        raise ValidationError("Missing organization ID in token")
    
    # Strip whitespace and validate it's not empty
    clerk_org_id = str(clerk_org_id).strip()
    if not clerk_org_id:
        logger.error(f"[WEBHOOKS] [CREATE] [ERROR] clerk_org_id is empty after stripping | original_value={current_user.get('clerk_org_id')}")
        raise ValidationError("Organization ID cannot be empty")
    
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 2] ✅ clerk_org_id validated | clerk_org_id={clerk_org_id}")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Generate secret if not provided
    secret = webhook_data.secret or secrets.token_hex(16)
    
    # STEP 3: Build webhook record - use clerk_org_id only (organization-first approach)
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 3] Building webhook_record | clerk_org_id={clerk_org_id}")
    
    if not clerk_org_id or not clerk_org_id.strip():
        logger.error(f"[WEBHOOKS] [CREATE] [ERROR] Invalid clerk_org_id before creating webhook_record | clerk_org_id={clerk_org_id}")
        raise ValidationError(f"Invalid clerk_org_id: '{clerk_org_id}' - cannot be empty")
    
    webhook_record_data = {
        "clerk_org_id": clerk_org_id.strip(),  # CRITICAL: Organization ID for data partitioning - ensure no whitespace
            "url": webhook_data.url,
            "event_types": webhook_data.event_types,
            "secret": secret,
            "enabled": webhook_data.enabled,
            "retry_config": webhook_data.retry_config or {"max_attempts": 10, "backoff_strategy": "exponential"},
    }
    
    # STEP 4: Explicit validation AFTER setting clerk_org_id in webhook_record_data
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 4] Validating webhook_record_data.clerk_org_id | value={webhook_record_data.get('clerk_org_id')}")
    
    if "clerk_org_id" not in webhook_record_data:
        logger.error(f"[WEBHOOKS] [CREATE] [ERROR] clerk_org_id key missing from webhook_record_data | keys={list(webhook_record_data.keys())}")
        raise ValidationError("clerk_org_id is missing from webhook_record_data")
    
    if not webhook_record_data["clerk_org_id"] or not str(webhook_record_data["clerk_org_id"]).strip():
        logger.error(f"[WEBHOOKS] [CREATE] [ERROR] clerk_org_id is empty in webhook_record_data | webhook_record_data={webhook_record_data}")
        raise ValidationError(f"clerk_org_id cannot be empty in webhook_record_data: '{webhook_record_data.get('clerk_org_id')}'")
    
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 4] ✅ webhook_record_data.clerk_org_id validated | value={webhook_record_data.get('clerk_org_id')}")
    
    # STEP 5: Log complete webhook_record_data before insert
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 5] Complete webhook_record_data before insert | clerk_org_id={webhook_record_data.get('clerk_org_id')}")
    
    # Create webhook endpoint - use clerk_org_id only (organization-first approach)
    webhook_record = db.insert("webhook_endpoints", webhook_record_data)
    
    # STEP 6: Verify clerk_org_id was saved correctly
    saved_clerk_org_id = webhook_record.get('clerk_org_id') if webhook_record else None
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 6] Webhook inserted | webhook_id={webhook_record.get('id')} | saved_clerk_org_id={saved_clerk_org_id}")
    
    if not saved_clerk_org_id or not str(saved_clerk_org_id).strip():
        logger.error(f"[WEBHOOKS] [CREATE] [ERROR] clerk_org_id is empty after insert! | webhook_record={webhook_record}")
        raise ValidationError(f"clerk_org_id was not saved correctly: '{saved_clerk_org_id}'")
    
    logger.info(f"[WEBHOOKS] [CREATE] [STEP 6] ✅ Webhook created successfully | webhook_id={webhook_record.get('id')} | clerk_org_id={saved_clerk_org_id}")
    
    return {
        "data": WebhookEndpointResponse(
            id=webhook_record["id"],
            url=webhook_record["url"],
            event_types=webhook_record["event_types"],
            secret=webhook_record["secret"],
            enabled=webhook_record["enabled"],
            created_at=webhook_record["created_at"],
        ),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("")
async def list_webhook_endpoints(
    current_user: dict = Depends(require_admin_role),
):
    """List webhook endpoints"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Filter by org_id instead of client_id
    webhooks = db.select("webhook_endpoints", {"clerk_org_id": clerk_org_id})
    
    # Don't return secrets
    for wh in webhooks:
        wh.pop("secret", None)
    
    return {
        "data": [WebhookEndpointResponse(**wh) for wh in webhooks],
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/{webhook_id}")
async def get_webhook_endpoint(
    webhook_id: str,
    current_user: dict = Depends(require_admin_role),
):
    """Get single webhook endpoint"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Filter by org_id instead of client_id
    webhook = db.select_one("webhook_endpoints", {"id": webhook_id, "clerk_org_id": clerk_org_id})
    if not webhook:
        raise NotFoundError("webhook_endpoint", webhook_id)
    
    # Don't return secret
    webhook.pop("secret", None)
    
    return {
        "data": WebhookEndpointResponse(**webhook),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/{webhook_id}")
async def update_webhook_endpoint(
    webhook_id: str,
    webhook_data: WebhookEndpointUpdate,
    current_user: dict = Depends(require_admin_role),
):
    """Update webhook endpoint"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Check if webhook exists - filter by org_id instead of client_id
    webhook = db.select_one("webhook_endpoints", {"id": webhook_id, "clerk_org_id": clerk_org_id})
    if not webhook:
        raise NotFoundError("webhook_endpoint", webhook_id)
    
    # Prepare update data (only non-None fields)
    update_data = webhook_data.dict(exclude_unset=True)
    if not update_data:
        # No updates provided
        webhook.pop("secret", None)
        return {
            "data": WebhookEndpointResponse(**webhook),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    # Update database - filter by org_id to enforce org scoping
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("webhook_endpoints", {"id": webhook_id, "clerk_org_id": clerk_org_id}, update_data)
    
    # Get updated webhook
    updated_webhook = db.select_one("webhook_endpoints", {"id": webhook_id, "clerk_org_id": clerk_org_id})
    updated_webhook.pop("secret", None)
    
    return {
        "data": WebhookEndpointResponse(**updated_webhook),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/{webhook_id}")
async def delete_webhook_endpoint(
    webhook_id: str,
    current_user: dict = Depends(require_admin_role),
):
    """Delete webhook endpoint"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    webhook = db.select_one("webhook_endpoints", {"id": webhook_id, "clerk_org_id": clerk_org_id})
    if not webhook:
        raise NotFoundError("webhook_endpoint", webhook_id)
    
    db.delete("webhook_endpoints", {"id": webhook_id, "clerk_org_id": clerk_org_id})
    
    return {"status": "deleted"}

