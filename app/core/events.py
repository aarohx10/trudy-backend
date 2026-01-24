"""
Event Logging Service
Logs application events for monitoring and debugging
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


async def publish_event(
    event_type: str,
    event_data: Dict[str, Any],
    source: str = "trudy-backend",
) -> bool:
    """
    Log event for monitoring and debugging
    
    Args:
        event_type: Event type (e.g., "voice.training.completed")
        event_data: Event payload
        source: Event source (default: "trudy-backend")
    
    Returns:
        True if logged successfully, False otherwise
    """
    try:
        # Create event payload
        event = {
            "source": source,
            "type": event_type,
            "data": event_data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        # Log event
        logger.info(
            f"Event: {event_type}",
            extra={"event_type": event_type, "event_data": event_data, "event": event},
        )
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
            "event_type": event_type,
            "event_data": event_data,
            "source": source,
        }
        logger.error(f"[EVENTS] Error logging event (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        return False


# Convenience functions for common event types (keep same API)
async def emit_voice_training_started(voice_id: str, client_id: str, ultravox_voice_id: str) -> bool:
    """Emit voice.training.started event"""
    return await publish_event(
        "voice.training.started",
        {
            "voice_id": voice_id,
            "client_id": client_id,
            "ultravox_voice_id": ultravox_voice_id,
            "status": "training",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_voice_training_completed(voice_id: str, client_id: str, ultravox_voice_id: str) -> bool:
    """Emit voice.training.completed event"""
    return await publish_event(
        "voice.training.completed",
        {
            "voice_id": voice_id,
            "client_id": client_id,
            "ultravox_voice_id": ultravox_voice_id,
            "status": "active",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_voice_training_failed(voice_id: str, client_id: str, ultravox_voice_id: str, error_message: str) -> bool:
    """Emit voice.training.failed event"""
    return await publish_event(
        "voice.training.failed",
        {
            "voice_id": voice_id,
            "client_id": client_id,
            "ultravox_voice_id": ultravox_voice_id,
            "status": "failed",
            "error_message": error_message,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_voice_created(voice_id: str, client_id: str, ultravox_voice_id: str) -> bool:
    """Emit voice.created event"""
    return await publish_event(
        "voice.created",
        {
            "voice_id": voice_id,
            "client_id": client_id,
            "ultravox_voice_id": ultravox_voice_id,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_call_created(call_id: str, client_id: str, ultravox_call_id: str, phone_number: str, direction: str) -> bool:
    """Emit call.created event"""
    return await publish_event(
        "call.created",
        {
            "call_id": call_id,
            "client_id": client_id,
            "ultravox_call_id": ultravox_call_id,
            "phone_number": phone_number,
            "direction": direction,
            "status": "queued",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_call_started(call_id: str, client_id: str) -> bool:
    """Emit call.started event"""
    return await publish_event(
        "call.started",
        {
            "call_id": call_id,
            "client_id": client_id,
            "status": "in_progress",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_call_completed(call_id: str, client_id: str, duration_seconds: Optional[int] = None, cost_usd: Optional[float] = None) -> bool:
    """Emit call.completed event"""
    return await publish_event(
        "call.completed",
        {
            "call_id": call_id,
            "client_id": client_id,
            "status": "completed",
            "duration_seconds": duration_seconds,
            "cost_usd": cost_usd,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_call_failed(call_id: str, client_id: str, error_message: Optional[str] = None) -> bool:
    """Emit call.failed event"""
    return await publish_event(
        "call.failed",
        {
            "call_id": call_id,
            "client_id": client_id,
            "status": "failed",
            "error_message": error_message,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_campaign_created(campaign_id: str, client_id: str, name: str) -> bool:
    """Emit campaign.created event"""
    return await publish_event(
        "campaign.created",
        {
            "campaign_id": campaign_id,
            "client_id": client_id,
            "name": name,
            "status": "draft",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_campaign_scheduled(campaign_id: str, client_id: str, scheduled_at: str, contact_count: int, batch_ids: list) -> bool:
    """Emit campaign.scheduled event"""
    return await publish_event(
        "campaign.scheduled",
        {
            "campaign_id": campaign_id,
            "client_id": client_id,
            "scheduled_at": scheduled_at,
            "contact_count": contact_count,
            "batch_ids": batch_ids,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def emit_campaign_completed(campaign_id: str, client_id: str, stats: Dict[str, int]) -> bool:
    """Emit campaign.completed event"""
    return await publish_event(
        "campaign.completed",
        {
            "campaign_id": campaign_id,
            "client_id": client_id,
            "stats": stats,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )



