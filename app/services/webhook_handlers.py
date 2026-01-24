"""
Webhook Event Handlers (Strategy Pattern)
Handles different Ultravox webhook event types
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.database import DatabaseService, DatabaseAdminService
from app.services.ultravox import ultravox_client
from app.core.events import (
    emit_call_started,
    emit_call_completed,
    emit_call_failed,
    emit_voice_training_completed,
    emit_voice_training_failed,
)

logger = logging.getLogger(__name__)


async def handle_call_started(event_data: Dict[str, Any], db: DatabaseService) -> Optional[str]:
    """Handle call.started event"""
    ultravox_call_id = event_data.get("call_id") or event_data.get("callId")
    if not ultravox_call_id:
        logger.warning("call.started event missing call_id")
        return None
    
    call = db.select_one("calls", {"ultravox_call_id": ultravox_call_id})
    if not call:
        logger.warning(f"Call not found for ultravox_call_id: {ultravox_call_id}")
        return None
    
    db.update(
        "calls",
        {"id": call["id"]},
        {
            "status": "in_progress",
            "started_at": event_data.get("timestamp") or datetime.utcnow().isoformat(),
        },
    )
    
    await emit_call_started(call_id=call["id"], client_id=call["client_id"])
    return call["client_id"]


async def handle_call_ended(event_data: Dict[str, Any], db: DatabaseService) -> Optional[str]:
    """
    Handle call.ended event - sync transcript and recording
    """
    ultravox_call_id = event_data.get("call_id") or event_data.get("callId")
    if not ultravox_call_id:
        logger.warning("call.ended event missing call_id")
        return None
    
    call = db.select_one("calls", {"ultravox_call_id": ultravox_call_id})
    if not call:
        logger.warning(f"Call not found for ultravox_call_id: {ultravox_call_id}")
        return None
    
    client_id = call["client_id"]
    data = event_data.get("data", {})
    
    # Fetch transcript and recording from Ultravox
    transcript_text = None
    transcript_data = None
    recording_url = None
    
    try:
        # Get transcript
        transcript_data = await ultravox_client.get_call_transcript(ultravox_call_id)
        if transcript_data:
            # Extract transcript text (format may vary)
            transcript_text = transcript_data.get("transcript") or transcript_data.get("text")
            if isinstance(transcript_text, list):
                # If it's a list of messages, join them
                transcript_text = "\n".join([
                    msg.get("text", "") for msg in transcript_text if msg.get("text")
                ])
            elif not isinstance(transcript_text, str):
                # If it's not a string, try to extract from structured format
                if isinstance(transcript_data, dict):
                    messages = transcript_data.get("messages", [])
                    if messages:
                        transcript_text = "\n".join([
                            msg.get("text", "") for msg in messages if msg.get("text")
                        ])
        
        # Get recording URL
        recording_url = await ultravox_client.get_call_recording(ultravox_call_id)
        if not recording_url:
            # Fallback to data field
            recording_url = data.get("recording_url") or event_data.get("recording_url")
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "ultravox_call_id": ultravox_call_id,
            "call_id": call.get("id") if call else None,
        }
        logger.error(f"[WEBHOOK_HANDLERS] [CALL_ENDED] Failed to fetch transcript/recording (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        # Continue with available data
    
    # Extract other call data
    duration = data.get("duration_seconds") or event_data.get("duration_seconds", 0)
    cost = data.get("cost_usd") or event_data.get("cost_usd", 0)
    end_reason = data.get("end_reason") or event_data.get("endReason", "unknown")
    
    # Update call with all data
    update_data = {
        "status": "completed",
        "duration_seconds": int(duration) if duration else None,
        "cost_usd": float(cost) if cost else None,
        "ended_at": event_data.get("timestamp") or event_data.get("ended") or datetime.utcnow().isoformat(),
        "end_reason": end_reason,
    }
    
    if transcript_text:
        # Store transcript as JSONB (can be string or structured data)
        # If transcript_data is structured, use it; otherwise use plain text
        if isinstance(transcript_data, dict) and transcript_data:
            update_data["transcript"] = transcript_data
        else:
            # Store as simple text string in JSONB
            update_data["transcript"] = {"text": transcript_text}
    
    if recording_url:
        update_data["recording_url"] = recording_url
    
    db.update("calls", {"id": call["id"]}, update_data)
    
    # Emit event
    await emit_call_completed(
        call_id=call["id"],
        client_id=client_id,
        duration_seconds=int(duration) if duration else 0,
        cost_usd=float(cost) if cost else 0,
    )
    
    # Update campaign contact if applicable
    if call.get("context", {}).get("campaign_id"):
        campaign_id = call["context"]["campaign_id"]
        phone_number = call.get("phone_number")
        if phone_number:
            db.update(
                "campaign_contacts",
                {"campaign_id": campaign_id, "phone_number": phone_number},
                {"status": "completed", "call_id": call["id"]},
            )
            db.update_campaign_stats(campaign_id)
    
    # Trigger post-call intelligence analysis (background task)
    if transcript_text:
        try:
            # Run analysis in background (fire and forget)
            # In production, you might want to use a task queue like Celery
            import asyncio
            asyncio.create_task(
                _process_call_analysis_and_webhook(
                    call["id"],
                    transcript_text,
                    agent_id=call.get("agent_id"),
                )
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
                "call_id": call.get("id"),
            }
            logger.error(f"[WEBHOOK_HANDLERS] [CALL_ENDED] Failed to trigger call analysis (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            # Don't fail the webhook - analysis is secondary
    
    return client_id


async def _process_call_analysis_and_webhook(
    call_id: str,
    transcript_text: str,
    agent_id: Optional[str] = None,
):
    """
    Background task to process call analysis and trigger CRM webhook.
    This runs asynchronously after the webhook handler returns.
    """
    try:
        from app.services.analysis import process_call_metadata
        
        # Process analysis
        analysis_result = await process_call_metadata(call_id, transcript_text)
        
        # Note: CRM webhook functionality removed - was dependent on agents table
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "call_id": call_id,
            "agent_id": agent_id,
        }
        logger.error(f"[WEBHOOK_HANDLERS] [CALL_ANALYSIS] Error in background call analysis task (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)


async def handle_call_failed(event_data: Dict[str, Any], db: DatabaseService) -> Optional[str]:
    """Handle call.failed event"""
    ultravox_call_id = event_data.get("call_id") or event_data.get("callId")
    if not ultravox_call_id:
        logger.warning("call.failed event missing call_id")
        return None
    
    call = db.select_one("calls", {"ultravox_call_id": ultravox_call_id})
    if not call:
        logger.warning(f"Call not found for ultravox_call_id: {ultravox_call_id}")
        return None
    
    client_id = call["client_id"]
    error_message = event_data.get("data", {}).get("error_message") or event_data.get("error_message", "Call failed")
    
    db.update(
        "calls",
        {"id": call["id"]},
        {
            "status": "failed",
            "ended_at": event_data.get("timestamp") or datetime.utcnow().isoformat(),
            "error_message": error_message,
        },
    )
    
    await emit_call_failed(call_id=call["id"], client_id=client_id, error_message=error_message)
    
    # Update campaign contact if applicable
    if call.get("context", {}).get("campaign_id"):
        campaign_id = call["context"]["campaign_id"]
        phone_number = call.get("phone_number")
        if phone_number:
            db.update(
                "campaign_contacts",
                {"campaign_id": campaign_id, "phone_number": phone_number},
                {"status": "failed"},
            )
            db.update_campaign_stats(campaign_id)
    
    return client_id


async def handle_batch_status_changed(event_data: Dict[str, Any], db: DatabaseService) -> Optional[str]:
    """
    Handle batch.status.changed event - update campaign progress
    """
    batch_id = event_data.get("batch_id") or event_data.get("batchId")
    if not batch_id:
        logger.warning("batch.status.changed event missing batch_id")
        return None
    
    # Find campaign by batch_id
    campaigns = db.select("campaigns", {})
    campaign = None
    for c in campaigns:
        batch_ids = c.get("ultravox_batch_ids", [])
        if isinstance(batch_ids, list) and batch_id in batch_ids:
            campaign = c
            break
    
    if not campaign:
        logger.warning(f"Campaign not found for batch_id: {batch_id}")
        return None
    
    client_id = campaign["client_id"]
    data = event_data.get("data", {})
    new_status = data.get("status") or event_data.get("status")
    
    # Get ultravox_agent_id from campaign (stored directly in campaign data)
    ultravox_agent_id = campaign.get("ultravox_agent_id")
    if not ultravox_agent_id:
        logger.warning(f"Campaign {campaign['id']} has no ultravox_agent_id configured")
        return client_id
    
    try:
        # Fetch latest batch status from Ultravox
        ultravox_batch = await ultravox_client.get_batch(ultravox_agent_id, batch_id)
        
        if ultravox_batch:
            # Update campaign stats
            current_stats = campaign.get("stats", {"pending": 0, "calling": 0, "completed": 0, "failed": 0})
            
            # Map Ultravox batch stats to local stats
            completed_count = ultravox_batch.get("completedCount", 0)
            total_count = ultravox_batch.get("totalCount", 0)
            failed_count = ultravox_batch.get("failedCount", 0)
            
            current_stats["completed"] = completed_count
            current_stats["total"] = total_count
            current_stats["failed"] = failed_count
            current_stats["pending"] = max(0, total_count - completed_count - failed_count)
            
            # Update campaign
            update_data = {"stats": current_stats}
            
            # Update campaign status if batch is completed
            if new_status == "COMPLETED" or ultravox_batch.get("status") == "COMPLETED":
                if campaign.get("status") != "completed":
                    update_data["status"] = "completed"
                    update_data["updated_at"] = datetime.utcnow().isoformat()
            
            db.update("campaigns", {"id": campaign["id"]}, update_data)
            logger.info(f"Updated campaign {campaign['id']} stats from batch {batch_id}")
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "batch_id": batch_id,
        }
        logger.error(f"[WEBHOOK_HANDLERS] [BATCH_STATUS] Failed to fetch batch status (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
    
    return client_id


async def handle_voice_training_completed(event_data: Dict[str, Any], db: DatabaseService) -> Optional[str]:
    """Handle voice.training.completed event"""
    ultravox_voice_id = event_data.get("voice_id") or event_data.get("voiceId")
    if not ultravox_voice_id:
        logger.warning("voice.training.completed event missing voice_id")
        return None
    
    voice = db.select_one("voices", {"ultravox_voice_id": ultravox_voice_id})
    if not voice:
        logger.warning(f"Voice not found for ultravox_voice_id: {ultravox_voice_id}")
        return None
    
    client_id = voice["client_id"]
    
    db.update(
        "voices",
        {"id": voice["id"]},
        {
            "status": "active",
            "training_info": {
                "progress": 100,
                "completed_at": event_data.get("timestamp") or datetime.utcnow().isoformat(),
            },
        },
    )
    
    await emit_voice_training_completed(
        voice_id=voice["id"],
        client_id=client_id,
        ultravox_voice_id=ultravox_voice_id,
    )
    
    return client_id


async def handle_voice_training_failed(event_data: Dict[str, Any], db: DatabaseService) -> Optional[str]:
    """Handle voice.training.failed event"""
    ultravox_voice_id = event_data.get("voice_id") or event_data.get("voiceId")
    if not ultravox_voice_id:
        logger.warning("voice.training.failed event missing voice_id")
        return None
    
    voice = db.select_one("voices", {"ultravox_voice_id": ultravox_voice_id})
    if not voice:
        logger.warning(f"Voice not found for ultravox_voice_id: {ultravox_voice_id}")
        return None
    
    client_id = voice["client_id"]
    error_message = event_data.get("error_message") or event_data.get("data", {}).get("error_message", "Voice training failed")
    
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
    
    await emit_voice_training_failed(
        voice_id=voice["id"],
        client_id=client_id,
        ultravox_voice_id=ultravox_voice_id,
        error_message=error_message,
    )
    
    return client_id


# Event handler mapping (Strategy Pattern)
EVENT_HANDLERS = {
    "call.started": handle_call_started,
    "call.ended": handle_call_ended,
    "call.completed": handle_call_ended,  # Alias for call.ended
    "call.failed": handle_call_failed,
    "batch.status.changed": handle_batch_status_changed,
    "batch.completed": handle_batch_status_changed,
    "voice.training.completed": handle_voice_training_completed,
    "voice.training.failed": handle_voice_training_failed,
}
