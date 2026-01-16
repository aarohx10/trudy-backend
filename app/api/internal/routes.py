"""
Internal API Routes for Background Jobs
"""
from fastapi import APIRouter, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
from datetime import datetime
import uuid
import logging

from app.core.database import DatabaseAdminService
from app.core.exceptions import NotFoundError
from app.models.schemas import ResponseMeta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/health")
async def health_check():
    """Health check endpoint for deployment monitoring"""
    try:
        # Basic health check - can be extended with DB checks
        return JSONResponse(
            content={
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "service": "trudy-api",
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            },
            status_code=503
        )


@router.get("/ready")
async def readiness_check():
    """Readiness check endpoint (for Kubernetes/Docker health checks)"""
    try:
        # Check critical dependencies
        from app.core.config import settings
        from app.core.database import get_supabase_admin_client
        
        # Check database connection
        db = get_supabase_admin_client()
        # Simple query to verify DB connection
        db.table("users").select("id").limit(1).execute()
        
        return JSONResponse(
            content={
                "status": "ready",
                "timestamp": datetime.utcnow().isoformat(),
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            content={
                "status": "not_ready",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            },
            status_code=503
        )


def verify_internal_request(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
) -> bool:
    """Verify internal request key"""
    from app.core.config import settings
    
    internal_key = getattr(settings, "INTERNAL_API_KEY", None)
    if not internal_key:
        logger.warning("INTERNAL_API_KEY not configured, allowing all internal requests")
        return True
    
    if x_internal_key != internal_key:
        raise HTTPException(status_code=401, detail="Invalid internal API key")
    
    return True


@router.post("/voices/{voice_id}/update-status")
async def update_voice_status(
    voice_id: str,
    status_data: dict,
    _: bool = Depends(verify_internal_request),
):
    """Update voice status (called by background jobs or webhooks)"""
    db = DatabaseAdminService()
    
    # Check if voice exists
    voice = db.select_one("voices", {"id": voice_id})
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    # Prepare update data
    update_data = {
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    if "status" in status_data:
        update_data["status"] = status_data["status"]
    
    if "training_info" in status_data:
        update_data["training_info"] = status_data["training_info"]
    
    if "ultravox_voice_id" in status_data:
        update_data["ultravox_voice_id"] = status_data["ultravox_voice_id"]
    
    # Update voice
    db.update("voices", {"id": voice_id}, update_data)
    
    logger.info(f"Updated voice status: {voice_id} -> {status_data.get('status')}")
    
    return {
        "data": {"voice_id": voice_id, "status": status_data.get("status")},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/campaigns/{campaign_id}/update-stats")
async def update_campaign_stats(
    campaign_id: str,
    _: bool = Depends(verify_internal_request),
):
    """Update campaign statistics (called by background jobs)"""
    db = DatabaseAdminService()
    
    # Check if campaign exists
    campaign = db.select_one("campaigns", {"id": campaign_id})
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    # Calculate stats from contacts
    contacts = db.select("campaign_contacts", {"campaign_id": campaign_id})
    
    stats = {
        "pending": sum(1 for c in contacts if c.get("status") == "pending"),
        "calling": sum(1 for c in contacts if c.get("status") == "calling"),
        "completed": sum(1 for c in contacts if c.get("status") == "completed"),
        "failed": sum(1 for c in contacts if c.get("status") == "failed"),
    }
    
    # Update campaign
    db.update("campaigns", {"id": campaign_id}, {"stats": stats})
    
    logger.info(f"Updated campaign stats: {campaign_id} -> {stats}")
    
    return {
        "data": {"campaign_id": campaign_id, "stats": stats},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/agents/{agent_id}/update-status")
async def update_agent_status(
    agent_id: str,
    status_data: dict,
    _: bool = Depends(verify_internal_request),
):
    """Update agent status (called by background jobs or webhooks)"""
    db = DatabaseAdminService()
    
    # Check if agent exists
    agent = db.select_one("agents", {"id": agent_id})
    if not agent:
        raise NotFoundError("agent", agent_id)
    
    # Prepare update data
    update_data = {
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    if "status" in status_data:
        update_data["status"] = status_data["status"]
    
    if "ultravox_agent_id" in status_data:
        update_data["ultravox_agent_id"] = status_data["ultravox_agent_id"]
    
    # Update agent
    db.update("agents", {"id": agent_id}, update_data)
    
    logger.info(f"Updated agent status: {agent_id} -> {status_data.get('status')}")
    
    return {
        "data": {"agent_id": agent_id, "status": status_data.get("status")},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/calls/{call_id}/update-status")
async def update_call_status(
    call_id: str,
    status_data: dict,
    _: bool = Depends(verify_internal_request),
):
    """Update call status (called by background jobs or webhooks)"""
    db = DatabaseAdminService()
    
    # Check if call exists
    call = db.select_one("calls", {"id": call_id})
    if not call:
        raise NotFoundError("call", call_id)
    
    # Prepare update data
    update_data = {
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    if "status" in status_data:
        update_data["status"] = status_data["status"]
    
    if "started_at" in status_data:
        update_data["started_at"] = status_data["started_at"]
    
    if "ended_at" in status_data:
        update_data["ended_at"] = status_data["ended_at"]
    
    if "duration_seconds" in status_data:
        update_data["duration_seconds"] = status_data["duration_seconds"]
    
    if "cost_usd" in status_data:
        update_data["cost_usd"] = status_data["cost_usd"]
    
    if "recording_url" in status_data:
        update_data["recording_url"] = status_data["recording_url"]
    
    if "transcript" in status_data:
        update_data["transcript"] = status_data["transcript"]
    
    if "ultravox_call_id" in status_data:
        update_data["ultravox_call_id"] = status_data["ultravox_call_id"]
    
    # Update call
    db.update("calls", {"id": call_id}, update_data)
    
    logger.info(f"Updated call status: {call_id} -> {status_data.get('status')}")
    
    return {
        "data": {"call_id": call_id, "status": status_data.get("status")},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/campaigns/{campaign_id}/update-status")
async def update_campaign_status(
    campaign_id: str,
    status_data: dict,
    _: bool = Depends(verify_internal_request),
):
    """Update campaign status (called by background jobs or webhooks)"""
    db = DatabaseAdminService()
    
    # Check if campaign exists
    campaign = db.select_one("campaigns", {"id": campaign_id})
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    # Prepare update data
    update_data = {
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    if "status" in status_data:
        update_data["status"] = status_data["status"]
    
    if "ultravox_batch_ids" in status_data:
        update_data["ultravox_batch_ids"] = status_data["ultravox_batch_ids"]
    
    # Update campaign
    db.update("campaigns", {"id": campaign_id}, update_data)
    
    logger.info(f"Updated campaign status: {campaign_id} -> {status_data.get('status')}")
    
    return {
        "data": {"campaign_id": campaign_id, "status": status_data.get("status")},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/webhooks/deliver")
async def deliver_webhook_internal(
    delivery_data: dict,
    _: bool = Depends(verify_internal_request),
):
    """Deliver webhook (called by background jobs or webhooks)"""
    from app.core.webhooks import deliver_webhook
    
    webhook_endpoint_id = delivery_data.get("webhook_endpoint_id")
    event_type = delivery_data.get("event_type")
    payload = delivery_data.get("payload")
    
    if not webhook_endpoint_id or not event_type or not payload:
        raise HTTPException(status_code=400, detail="Missing required fields: webhook_endpoint_id, event_type, payload")
    
    # Deliver webhook
    result = await deliver_webhook(
        webhook_endpoint_id=webhook_endpoint_id,
        event_type=event_type,
        payload=payload,
    )
    
    logger.info(f"Delivered webhook: {webhook_endpoint_id} -> {event_type} (success: {result.get('success')})")
    
    return {
        "data": result,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/idempotency/cleanup")
async def cleanup_idempotency_keys(
    _: bool = Depends(verify_internal_request),
):
    """Cleanup expired idempotency keys (called by scheduled job)"""
    db = DatabaseAdminService()
    
    from datetime import datetime
    
    # Get expired keys
    expired_keys = db.select("idempotency_keys", {})
    
    # Filter expired keys
    now = datetime.utcnow()
    deleted_count = 0
    
    for key in expired_keys:
        ttl_at = datetime.fromisoformat(key["ttl_at"].replace("Z", "+00:00"))
        if now > ttl_at:
            db.delete("idempotency_keys", {"id": key["id"]})
            deleted_count += 1
    
    logger.info(f"Cleaned up {deleted_count} expired idempotency keys")
    
    return {
        "data": {"deleted_count": deleted_count},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

