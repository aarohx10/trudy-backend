"""
Call Endpoints
"""
from fastapi import APIRouter, Header, Depends
from starlette.requests import Request
from typing import Optional
from datetime import datetime
import uuid
import json
import logging
import httpx

logger = logging.getLogger(__name__)

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ForbiddenError, PaymentRequiredError, ValidationError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_call_created
from app.core.storage import upload_bytes
from app.core.config import settings
from app.services.ultravox import ultravox_client
from app.models.schemas import (
    CallCreate,
    CallUpdate,
    CallResponse,
    TranscriptResponse,
    RecordingResponse,
    BulkDeleteRequest,
    BulkDeleteResponse,
    ResponseMeta,
)

router = APIRouter()


@router.post("")
async def create_call(
    call_data: CallCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create call"""
    # Check idempotency key
    body_dict = call_data.dict() if hasattr(call_data, 'dict') else json.loads(json.dumps(call_data, default=str))
    if idempotency_key:
        cached = await check_idempotency_key(
            current_user["client_id"],
            idempotency_key,
            request,
            body_dict,
        )
        if cached:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                content=cached["response_body"],
                status_code=cached["status_code"],
            )
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Validate agent
    agent = db.get_agent(call_data.agent_id, current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", call_data.agent_id)
    if agent.get("status") != "active":
        raise ValidationError("Agent must be active", {"agent_status": agent.get("status")})
    
    # Credit check for outbound calls
    if call_data.direction == "outbound":
        client = db.get_client(current_user["client_id"])
        if not client or client.get("credits_balance", 0) < 1:
            raise PaymentRequiredError(
                "Insufficient credits for outbound call",
                {"required": 1, "available": client.get("credits_balance", 0) if client else 0},
            )
    
    # Create call record
    call_id = str(uuid.uuid4())
    call_record = {
        "id": call_id,
        "client_id": current_user["client_id"],
        "agent_id": call_data.agent_id,
        "phone_number": call_data.phone_number,
        "direction": call_data.direction.value,
        "status": "queued",
        "context": call_data.context or {},
        "call_settings": call_data.call_settings.dict() if call_data.call_settings else {},
    }
    
    db.insert("calls", call_record)
    
    # Call Ultravox API
    ultravox_agent_id = agent.get("ultravox_agent_id")
    if ultravox_agent_id:
        try:
            ultravox_data = {
                "agent_id": ultravox_agent_id,
                "phone_number": call_data.phone_number,
                "direction": call_data.direction.value,
                "call_settings": call_data.call_settings.dict() if call_data.call_settings else {},
                "context": call_data.context or {},
            }
            ultravox_response = await ultravox_client.create_call(ultravox_data)
            
            # Update with Ultravox ID
            db.update(
                "calls",
                {"id": call_id},
                {"ultravox_call_id": ultravox_response.get("id")},
            )
            call_record["ultravox_call_id"] = ultravox_response.get("id")
            
        except Exception as e:
            # Log error but don't fail the request - call is created in DB
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to create call in Ultravox: {e}")
            # Update call status to failed
            db.update(
                "calls",
                {"id": call_id},
                {"status": "failed"},
            )
            call_record["status"] = "failed"
    else:
        # Agent doesn't have ultravox_agent_id - call created but marked as failed
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Agent {call_data.agent_id} does not have ultravox_agent_id - call created without Ultravox integration")
        db.update(
            "calls",
            {"id": call_id},
            {"status": "failed"},
        )
        call_record["status"] = "failed"
    
    # Emit event (only if call was successfully created in Ultravox)
    if call_record.get("ultravox_call_id"):
        await emit_call_created(
            call_id=call_id,
            client_id=current_user["client_id"],
            agent_id=call_data.agent_id,
            ultravox_call_id=call_record["ultravox_call_id"],
            phone_number=call_data.phone_number,
            direction=call_data.direction.value,
        )
    
    # Fetch the call from database to get all fields including created_at
    call = db.get_call(call_id, current_user["client_id"])
    if not call:
        raise NotFoundError("call", call_id)
    
    response_data = {
        "data": CallResponse(**call),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
    
    # Store idempotency response
    if idempotency_key:
        await store_idempotency_response(
            current_user["client_id"],
            idempotency_key,
            request,
            body_dict,
            response_data,
            201,
        )
    
    return response_data


@router.get("")
async def list_calls(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List calls with filtering and pagination"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Build filters
    filters = {"client_id": current_user["client_id"]}
    if agent_id:
        filters["agent_id"] = agent_id
    if status:
        filters["status"] = status
    if direction:
        filters["direction"] = direction
    
    # Get calls with pagination
    # Note: Supabase PostgREST supports limit/offset via query params
    all_calls = db.select("calls", filters, order_by="created_at")
    
    # Apply pagination manually (since db.select doesn't support limit/offset directly)
    total = len(all_calls)
    paginated_calls = all_calls[offset:offset + limit]
    
    return {
        "data": [CallResponse(**call) for call in paginated_calls],
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.get("/{call_id}")
async def get_call(
    call_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    refresh: bool = False,
):
    """Get call with optional status refresh from Ultravox"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    call = db.get_call(call_id, current_user["client_id"])
    if not call:
        raise NotFoundError("call", call_id)
    
    # Optionally refresh from Ultravox if status is active and refresh flag is set
    if refresh and call.get("ultravox_call_id") and call.get("status") in ["queued", "ringing", "in_progress"]:
        try:
            ultravox_call = await ultravox_client.get_call(call["ultravox_call_id"])
            
            # Update local database with latest status
            update_data = {}
            if ultravox_call.get("status"):
                update_data["status"] = ultravox_call["status"]
            if ultravox_call.get("started_at"):
                update_data["started_at"] = ultravox_call["started_at"]
            if ultravox_call.get("ended_at"):
                update_data["ended_at"] = ultravox_call["ended_at"]
            if ultravox_call.get("duration_seconds") is not None:
                update_data["duration_seconds"] = ultravox_call["duration_seconds"]
            if ultravox_call.get("cost_usd") is not None:
                update_data["cost_usd"] = ultravox_call["cost_usd"]
            
            if update_data:
                db.update("calls", {"id": call_id}, update_data)
                # Refresh call data
                call = db.get_call(call_id, current_user["client_id"])
        except Exception as e:
            # Log error but don't fail the request
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to refresh call status from Ultravox: {e}")
    
    return {
        "data": CallResponse(**call),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get call transcript"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    call = db.get_call(call_id, current_user["client_id"])
    if not call:
        raise NotFoundError("call", call_id)
    
    # Check cache
    if call.get("transcript"):
        transcript_data = call["transcript"]
    else:
        # Fetch from Ultravox
        if not call.get("ultravox_call_id"):
            raise NotFoundError("transcript")
        
        try:
            transcript_data = await ultravox_client.get_call_transcript(call["ultravox_call_id"])
            # Update cache
            db.update("calls", {"id": call_id}, {"transcript": transcript_data})
        except Exception as e:
            raise NotFoundError("transcript")
    
    return {
        "data": TranscriptResponse(
            call_id=call_id,
            transcript=transcript_data.get("transcript", []),
            summary=transcript_data.get("summary"),
        ),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/{call_id}/recording")
async def get_call_recording(
    call_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get call recording URL"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    call = db.get_call(call_id, current_user["client_id"])
    if not call:
        raise NotFoundError("call", call_id)
    
    # Check if recording URL exists
    if call.get("recording_url"):
        recording_url = call["recording_url"]
    else:
        # Fetch from Ultravox
        if not call.get("ultravox_call_id"):
            raise NotFoundError("recording")
        
        try:
            # Get recording URL from Ultravox
            ultravox_recording_url = await ultravox_client.get_call_recording(call["ultravox_call_id"])
            
            if not ultravox_recording_url:
                raise NotFoundError("recording")
            
            # Download recording from Ultravox
            logger.info(f"Downloading call recording from Ultravox: {ultravox_recording_url}")
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(ultravox_recording_url)
                response.raise_for_status()
                recording_data = response.content
                content_type = response.headers.get("content-type", "audio/mpeg")
            
            # Determine file extension from content type
            file_ext = "mp3"  # default
            if "wav" in content_type.lower():
                file_ext = "wav"
            elif "mpeg" in content_type.lower() or "mp3" in content_type.lower():
                file_ext = "mp3"
            elif "ogg" in content_type.lower():
                file_ext = "ogg"
            
            # Generate storage key: recordings/client_id/calls/call_id/recording.{ext}
            client_id = current_user["client_id"]
            storage_key = f"recordings/{client_id}/calls/{call_id}/recording.{file_ext}"
            
            # Upload recording
            logger.info(f"Uploading call recording: {storage_key} ({len(recording_data)} bytes)")
            storage_url = upload_bytes(
                bucket=settings.STORAGE_BUCKET_RECORDINGS,
                key=storage_key,
                data=recording_data,
                content_type=content_type,
            )
            
            # Update database with storage URL
            db.update("calls", {"id": call_id}, {"recording_url": storage_url})
            logger.info(f"Call recording uploaded to storage and database updated: {storage_url}")
            
            recording_url = storage_url
        except httpx.HTTPError as e:
            logger.error(f"Failed to download recording from Ultravox: {e}")
            raise NotFoundError("recording")
        except Exception as e:
            logger.error(f"Error processing call recording: {e}", exc_info=True)
            raise NotFoundError("recording")
    
    return {
        "data": RecordingResponse(
            call_id=call_id,
            recording_url=recording_url,
            format="mp3",
            duration_seconds=call.get("duration_seconds"),
        ),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/{call_id}")
async def update_call(
    call_id: str,
    call_data: CallUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update call (context and settings only)"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if call exists
    call = db.get_call(call_id, current_user["client_id"])
    if not call:
        raise NotFoundError("call", call_id)
    
    # Only allow updating context and call_settings
    # Status and other fields are controlled by the system/webhooks
    update_data = call_data.dict(exclude_unset=True)
    if not update_data:
        # No updates provided
        return {
            "data": CallResponse(**call),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    # Convert call_settings to dict if it's a Pydantic model
    if "call_settings" in update_data and update_data["call_settings"]:
        if hasattr(update_data["call_settings"], "dict"):
            update_data["call_settings"] = update_data["call_settings"].dict()
    
    # Update database
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("calls", {"id": call_id}, update_data)
    
    # Get updated call
    updated_call = db.get_call(call_id, current_user["client_id"])
    
    return {
        "data": CallResponse(**updated_call),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/bulk")
async def bulk_delete_calls(
    request_data: BulkDeleteRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Bulk delete calls"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    deleted_ids = []
    failed_ids = []
    
    for call_id in request_data.ids:
        try:
            call = db.get_call(call_id, current_user["client_id"])
            if not call:
                failed_ids.append(call_id)
                continue
            
            # Only allow deletion for completed or failed calls
            # Don't allow deletion of active calls
            current_status = call.get("status")
            if current_status in ["queued", "ringing", "in_progress"]:
                failed_ids.append(call_id)
                continue
            
            # Delete call
            db.delete("calls", {"id": call_id})
            deleted_ids.append(call_id)
        except Exception as e:
            logger.error(f"Failed to delete call {call_id}: {e}")
            failed_ids.append(call_id)
    
    return {
        "data": BulkDeleteResponse(
            deleted_count=len(deleted_ids),
            failed_count=len(failed_ids),
            deleted_ids=deleted_ids,
            failed_ids=failed_ids,
        ),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/{call_id}")
async def delete_call(
    call_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete call"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if call exists
    call = db.get_call(call_id, current_user["client_id"])
    if not call:
        raise NotFoundError("call", call_id)
    
    # Only allow deletion for completed or failed calls
    # Don't allow deletion of active calls
    current_status = call.get("status")
    if current_status in ["queued", "ringing", "in_progress"]:
        raise ValidationError(
            f"Cannot delete call while it is {current_status}. Wait for call to complete or fail."
        )
    
    # Delete call
    db.delete("calls", {"id": call_id})
    
    return {
        "data": {"id": call_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

