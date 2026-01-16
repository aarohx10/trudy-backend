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
from app.core.database import DatabaseService
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
    """Receive webhook from Ultravox"""
    # Get raw body
    body = await request.body()
    body_str = body.decode("utf-8")
    
    # Verify signature
    if not x_ultravox_signature or not x_ultravox_timestamp:
        raise UnauthorizedError("Missing signature or timestamp")
    
    if not verify_ultravox_signature(
        x_ultravox_signature,
        x_ultravox_timestamp,
        body_str,
        settings.ULTRAVOX_WEBHOOK_SECRET,
    ):
        raise UnauthorizedError("Invalid signature")
    
    if not verify_timestamp(x_ultravox_timestamp):
        raise UnauthorizedError("Timestamp verification failed")
    
    # Parse event
    event_data = json.loads(body_str)
    event_type = event_data.get("event")
    
    db = DatabaseService()
    
    # Track client_id for webhook triggering
    client_id_for_webhook = None
    
    # Route by event type
    if event_type == "call.started":
        # Update call status
        ultravox_call_id = event_data.get("call_id")
        call = db.select_one("calls", {"ultravox_call_id": ultravox_call_id})
        
        if call:
            client_id_for_webhook = call["client_id"]
            db.update(
                "calls",
                {"id": call["id"]},
                {
                    "status": "in_progress",
                    "started_at": event_data.get("timestamp"),
                },
            )
            
            # Emit event
            await emit_call_started(call_id=call["id"], client_id=client_id_for_webhook)
    
    elif event_type == "call.completed":
        # Update call status
        ultravox_call_id = event_data.get("call_id")
        call = db.select_one("calls", {"ultravox_call_id": ultravox_call_id})
        
        if call:
            client_id_for_webhook = call["client_id"]
            duration = event_data.get("data", {}).get("duration_seconds", 0)
            cost = event_data.get("data", {}).get("cost_usd", 0)
            
            # Debit credits
            credits = max(1, (duration + 59) // 60)  # Round up to minutes
            db.insert(
                "credit_transactions",
                {
                    "client_id": call["client_id"],
                    "type": "spent",
                    "amount": credits,
                    "reference_type": "call",
                    "reference_id": call["id"],
                    "description": f"Call duration: {credits} minutes",
                },
            )
            
            client = db.get_client(call["client_id"])
            if client:
                db.update(
                    "clients",
                    {"id": call["client_id"]},
                    {"credits_balance": client["credits_balance"] - credits},
                )
            
            # Update call
            db.update(
                "calls",
                {"id": call["id"]},
                {
                    "status": "completed",
                    "duration_seconds": duration,
                    "cost_usd": cost,
                    "ended_at": event_data.get("timestamp"),
                    "recording_url": event_data.get("data", {}).get("recording_url"),
                },
            )
            
            # Emit event
            await emit_call_completed(
                call_id=call["id"],
                client_id=call["client_id"],
                duration_seconds=duration,
                cost_usd=cost,
            )
            
            # Update campaign contact if applicable
            if call.get("context", {}).get("campaign_id"):
                campaign_id = call["context"]["campaign_id"]
                phone_number = call["phone_number"]
                db.update(
                    "campaign_contacts",
                    {"campaign_id": campaign_id, "phone_number": phone_number},
                    {"status": "completed", "call_id": call["id"]},
                )
                db.update_campaign_stats(campaign_id)
    
    elif event_type == "call.failed":
        # Update call status
        ultravox_call_id = event_data.get("call_id")
        call = db.select_one("calls", {"ultravox_call_id": ultravox_call_id})
        
        if call:
            client_id_for_webhook = call["client_id"]
            error_message = event_data.get("data", {}).get("error_message", "Call failed")
            db.update(
                "calls",
                {"id": call["id"]},
                {
                    "status": "failed",
                    "ended_at": event_data.get("timestamp"),
                    "error_message": error_message,
                },
            )
            
            # Emit event
            await emit_call_failed(call_id=call["id"], client_id=client_id_for_webhook, error_message=error_message)
            
            # Update campaign contact if applicable
            if call.get("context", {}).get("campaign_id"):
                campaign_id = call["context"]["campaign_id"]
                phone_number = call["phone_number"]
                db.update(
                    "campaign_contacts",
                    {"campaign_id": campaign_id, "phone_number": phone_number},
                    {"status": "failed"},
                )
                db.update_campaign_stats(campaign_id)
    
    elif event_type == "voice.training.completed":
        # Update voice status
        ultravox_voice_id = event_data.get("voice_id")
        voice = db.select_one("voices", {"ultravox_voice_id": ultravox_voice_id})
        
        if voice:
            client_id_for_webhook = voice["client_id"]
            db.update(
                "voices",
                {"id": voice["id"]},
                {
                    "status": "active",
                    "training_info": {
                        "progress": 100,
                        "completed_at": event_data.get("timestamp"),
                    },
                },
            )
            
            # Emit event
            await emit_voice_training_completed(
                voice_id=voice["id"],
                client_id=client_id_for_webhook,
                ultravox_voice_id=ultravox_voice_id,
            )
    
    elif event_type == "voice.training.failed":
        # Update voice status
        ultravox_voice_id = event_data.get("voice_id")
        voice = db.select_one("voices", {"ultravox_voice_id": ultravox_voice_id})
        
        if voice:
            client_id_for_webhook = voice["client_id"]
            error_message = event_data.get("error_message", "Voice training failed")
            db.update(
                "voices",
                {"id": voice["id"]},
                {
                    "status": "failed",
                    "training_info": {
                        "error_message": error_message,
                    },
                },
            )
            
            # Emit event
            await emit_voice_training_failed(
                voice_id=voice["id"],
                client_id=client_id_for_webhook,
                ultravox_voice_id=ultravox_voice_id,
                error_message=error_message,
            )
    
    # Trigger egress webhooks
    if client_id_for_webhook:
        await trigger_egress_webhooks(
            client_id=client_id_for_webhook,
            event_type=event_type,
            event_data=event_data,
        )
    
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
            logger.error(f"Error delivering webhook: {e}")
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
        logger.error(f"Failed to parse Telnyx webhook body: {e}")
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

