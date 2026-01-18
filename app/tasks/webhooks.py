"""
Outbound Webhook Tasks
Handles sending call data to external CRM systems (HighLevel, Hubspot, Zapier, etc.)
"""
import logging
import httpx
import json
import time
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.database import DatabaseAdminService
from app.core.webhooks import generate_webhook_signature

logger = logging.getLogger(__name__)


async def trigger_crm_webhook(
    call_id: str,
    agent_id: str,
    analysis_result: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Trigger outbound webhook to CRM system if agent has crm_webhook_url configured.
    Only triggers for successful calls (is_success = true).
    
    Returns True if webhook was sent successfully, False otherwise.
    """
    admin_db = DatabaseAdminService()
    
    # Get agent to check for CRM webhook URL
    agent = admin_db.select_one("agents", {"id": agent_id})
    if not agent:
        logger.warning(f"Agent {agent_id} not found for CRM webhook")
        return False
    
    crm_webhook_url = agent.get("crm_webhook_url")
    if not crm_webhook_url:
        # No CRM webhook configured for this agent
        return False
    
    # Get call data
    call = admin_db.select_one("calls", {"id": call_id})
    if not call:
        logger.warning(f"Call {call_id} not found for CRM webhook")
        return False
    
    # Only send for successful calls
    if not call.get("is_success"):
        logger.debug(f"Call {call_id} not marked as successful, skipping CRM webhook")
        return False
    
    try:
        # Prepare webhook payload
        payload = {
            "event": "call.completed",
            "call_id": call_id,
            "agent_id": agent_id,
            "agent_name": agent.get("name"),
            "phone_number": call.get("phone_number"),
            "direction": call.get("direction"),
            "duration_seconds": call.get("duration_seconds"),
            "cost_usd": float(call.get("cost_usd", 0)) if call.get("cost_usd") else None,
            "started_at": call.get("started_at"),
            "ended_at": call.get("ended_at"),
            "recording_url": call.get("recording_url"),
            "summary": call.get("summary"),
            "sentiment": call.get("sentiment"),
            "structured_data": call.get("structured_data") or {},
            "is_success": call.get("is_success"),
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        # Add transcript if available (may be large, so make it optional)
        transcript = call.get("transcript")
        if transcript:
            if isinstance(transcript, dict):
                payload["transcript"] = transcript.get("text") or transcript
            else:
                payload["transcript"] = transcript
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Trudy-Backend/1.0",
        }
        
        # Add signature if secret is configured
        crm_webhook_secret = agent.get("crm_webhook_secret")
        if crm_webhook_secret:
            signature, timestamp = generate_webhook_signature(payload, crm_webhook_secret)
            headers["X-Trudy-Timestamp"] = timestamp
            headers["X-Trudy-Signature"] = signature
        
        # Send webhook
        timeout = 10  # 10 second timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                crm_webhook_url,
                json=payload,
                headers=headers,
            )
            
            if 200 <= response.status_code < 300:
                logger.info(f"✅ CRM webhook sent successfully for call {call_id} to {crm_webhook_url}")
                return True
            else:
                logger.warning(
                    f"CRM webhook returned non-2xx status for call {call_id}: "
                    f"{response.status_code} - {response.text[:200]}"
                )
                return False
                
    except httpx.TimeoutException:
        logger.error(f"CRM webhook timeout for call {call_id} to {crm_webhook_url}")
        return False
    except httpx.RequestError as e:
        logger.error(f"CRM webhook request error for call {call_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending CRM webhook for call {call_id}: {e}", exc_info=True)
        return False
