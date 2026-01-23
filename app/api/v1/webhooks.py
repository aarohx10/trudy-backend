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
from app.core.database import DatabaseService, DatabaseAdminService
from app.core.config import settings
from app.core.webhooks import verify_ultravox_signature, verify_timestamp, verify_stripe_signature, verify_telnyx_signature, deliver_webhook
from app.core.events import (
    emit_voice_training_completed,
    emit_voice_training_failed,
    emit_call_started,
    emit_call_completed,
    emit_call_failed,
    emit_credits_purchased,
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
    db = DatabaseService()
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
    if client_id_for_webhook:
        try:
            await trigger_egress_webhooks(
                client_id=client_id_for_webhook,
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
    client_id: str,
    event_type: str,
    event_data: dict,
) -> None:
    """Trigger egress webhooks for a client"""
    from app.core.database import DatabaseAdminService
    
    db = DatabaseAdminService()
    
    # Get enabled webhook endpoints for this client and event type
    endpoints = db.select(
        "webhook_endpoints",
        {
            "client_id": client_id,
            "enabled": True,
        },
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


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
):
    """Receive webhook from Stripe"""
    # Get raw body for signature verification
    body_bytes = await request.body()
    body_str = body_bytes.decode('utf-8')
    
    # Verify signature
    if not stripe_signature:
        raise UnauthorizedError("Missing Stripe-Signature header")
    
    if not verify_stripe_signature(
        body_str,
        stripe_signature,
        settings.STRIPE_WEBHOOK_SECRET,
    ):
        raise UnauthorizedError("Invalid Stripe signature")
    
    # Parse JSON body
    body = json.loads(body_str)
    event_type = body.get("type")
    
    # Use admin client for webhook processing (bypasses RLS)
    from app.core.database import DatabaseAdminService
    db = DatabaseAdminService()
    
    if event_type == "payment_intent.succeeded":
        payment_intent = body.get("data", {}).get("object", {})
        amount_cents = payment_intent.get("amount", 0)
        amount_usd = amount_cents / 100
        credits = amount_cents // 100  # 1 credit = $1
        
        # Extract client_id from metadata
        metadata = payment_intent.get("metadata", {})
        client_id = metadata.get("client_id")
        
        if client_id:
            # Verify client exists
            client = db.select_one("clients", {"id": client_id})
            if client:
                # Add credit transaction
                db.insert(
                    "credit_transactions",
                    {
                        "client_id": client_id,
                        "type": "purchased",
                        "amount": credits,
                        "reference_type": "stripe_payment",
                        "reference_id": payment_intent.get("id"),
                        "description": f"Stripe payment: {payment_intent.get('id')}",
                    },
                )
                
                # Update client credits balance
                db.update(
                    "clients",
                    {"id": client_id},
                    {"credits_balance": client.get("credits_balance", 0) + credits},
                )
                
                # Emit event
                await emit_credits_purchased(
                    client_id=client_id,
                    amount=amount_usd,
                    credits=credits,
                    transaction_id=payment_intent.get("id"),
                )
                
                logger.info(f"Added {credits} credits to client {client_id} from Stripe payment")
            else:
                logger.warning(f"Client {client_id} not found for Stripe payment")
        else:
            logger.warning("No client_id in Stripe payment metadata")
    
    elif event_type == "customer.subscription.updated":
        subscription = body.get("data", {}).get("object", {})
        customer_id = subscription.get("customer")
        status = subscription.get("status")
        
        # Update client subscription status
        client = db.select_one("clients", {"stripe_customer_id": customer_id})
        if client:
            # Map Stripe status to our status
            status_map = {
                "active": "active",
                "trialing": "active",
                "past_due": "suspended",
                "canceled": "cancelled",
                "unpaid": "suspended",
            }
            mapped_status = status_map.get(status, "active")
            
            db.update(
                "clients",
                {"id": client["id"]},
                {"subscription_status": mapped_status},
            )
            
            logger.info(f"Updated subscription status for client {client['id']} to {mapped_status}")
    
    return {"status": "ok"}


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
        
        db = DatabaseService()
        
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
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create webhook endpoint"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Generate secret if not provided
    secret = webhook_data.secret or secrets.token_hex(16)
    
    webhook_record = db.insert(
        "webhook_endpoints",
        {
            "client_id": current_user["client_id"],
            "url": webhook_data.url,
            "event_types": webhook_data.event_types,
            "secret": secret,
            "enabled": webhook_data.enabled,
            "retry_config": webhook_data.retry_config or {"max_attempts": 10, "backoff_strategy": "exponential"},
        },
    )
    
    return {
        "data": WebhookEndpointResponse(
            id=webhook_record["id"],
            client_id=webhook_record["client_id"],
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
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List webhook endpoints"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    webhooks = db.select("webhook_endpoints", {"client_id": current_user["client_id"]})
    
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
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single webhook endpoint"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    webhook = db.select_one("webhook_endpoints", {"id": webhook_id, "client_id": current_user["client_id"]})
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
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update webhook endpoint"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if webhook exists
    webhook = db.select_one("webhook_endpoints", {"id": webhook_id, "client_id": current_user["client_id"]})
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
    
    # Update database
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("webhook_endpoints", {"id": webhook_id}, update_data)
    
    # Get updated webhook
    updated_webhook = db.select_one("webhook_endpoints", {"id": webhook_id, "client_id": current_user["client_id"]})
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
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete webhook endpoint"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    webhook = db.select_one("webhook_endpoints", {"id": webhook_id})
    if not webhook:
        raise NotFoundError("webhook_endpoint", webhook_id)
    
    db.delete("webhook_endpoints", {"id": webhook_id})
    
    return {"status": "deleted"}

