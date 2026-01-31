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
from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
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
    current_user: dict = Depends(require_admin_role),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create call"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Check idempotency key
    body_dict = call_data.dict() if hasattr(call_data, 'dict') else json.loads(json.dumps(call_data, default=str))
    if idempotency_key:
        cached = await check_idempotency_key(
            clerk_org_id,  # CRITICAL: Use org_id for idempotency (organization-first approach)
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
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Create call record - use clerk_org_id only (organization-first approach)
    call_id = str(uuid.uuid4())
    call_record = {
        "id": call_id,
        "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
        "created_by_user_id": current_user.get("clerk_user_id"),  # Track which user created the call
        "agent_id": call_data.agent_id if call_data.agent_id else None,
        "phone_number": call_data.phone_number,
        "direction": call_data.direction.value,
        "status": "queued",
        "context": call_data.context or {},
        "call_settings": call_data.call_settings.dict() if call_data.call_settings else {},
    }
    
    db.insert("calls", call_record)
    
    # Get agent's outbound number if this is an outbound call
    caller_id = None
    if call_data.agent_id and call_data.direction.value == "outbound":
        agent = db.select_one("agents", {"id": call_data.agent_id, "clerk_org_id": clerk_org_id})
        if agent and agent.get("outbound_phone_number_id"):
            outbound_number = db.select_one("phone_numbers", {"id": agent["outbound_phone_number_id"]})
            if outbound_number:
                caller_id = outbound_number["phone_number"]
                logger.info(f"[CALLS] Using outbound number {caller_id} for agent {call_data.agent_id}")
    
    # Call Ultravox API
    # Note: ultravox_agent_id must be provided directly in call_data or call_settings
    ultravox_agent_id = getattr(call_data, 'ultravox_agent_id', None) or (call_data.call_settings.dict() if call_data.call_settings else {}).get('ultravox_agent_id')
    if ultravox_agent_id:
        try:
            ultravox_data = {
                "agent_id": ultravox_agent_id,
                "phone_number": call_data.phone_number,
                "direction": call_data.direction.value,
                "call_settings": call_data.call_settings.dict() if call_data.call_settings else {},
                "context": call_data.context or {},
            }
            # Add caller_id for outbound calls
            if caller_id:
                ultravox_data["caller_id"] = caller_id
            
            ultravox_response = await ultravox_client.create_call(ultravox_data)
            
            # Update with Ultravox ID
            db.update(
                "calls",
                {"id": call_id, "clerk_org_id": clerk_org_id},
                {"ultravox_call_id": ultravox_response.get("id")},
            )
            call_record["ultravox_call_id"] = ultravox_response.get("id")
            
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
                "ultravox_call_id": ultravox_response.get("id") if 'ultravox_response' in locals() else None,
            }
            # Log error but don't fail the request - call is created in DB
            logger.warning(f"[CALLS] [CREATE] Failed to create call in Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            # Update call status to failed
            db.update(
                "calls",
                {"id": call_id, "clerk_org_id": clerk_org_id},
                {"status": "failed"},
            )
            call_record["status"] = "failed"
    else:
        # No ultravox_agent_id provided - call created but marked as failed
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"No ultravox_agent_id provided - call created without Ultravox integration")
        db.update(
            "calls",
            {"id": call_id, "clerk_org_id": clerk_org_id},
            {"status": "failed"},
        )
        call_record["status"] = "failed"
    
    # Emit event (only if call was successfully created in Ultravox)
    if call_record.get("ultravox_call_id"):
        await emit_call_created(
            call_id=call_id,
            org_id=clerk_org_id,  # Organization ID
            ultravox_call_id=call_record["ultravox_call_id"],
            phone_number=call_data.phone_number,
            direction=call_data.direction.value,
        )
    
    # Fetch the call from database to get all fields including created_at (filtered by org_id via context)
    call = db.get_call(call_id, org_id=clerk_org_id)
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
            clerk_org_id,  # CRITICAL: Use org_id for idempotency (organization-first approach)
            idempotency_key,
            request,
            body_dict,
            response_data,
            201,
        )
    
    return response_data


@router.get("")
async def list_calls(
    current_user: dict = Depends(require_admin_role),
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    List calls with filtering and pagination.
    
    CRITICAL: Filters by clerk_org_id to allow teammates to see each other's calls.
    Removed user_id filter - all calls in the organization are visible.
    """
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Build filters - filter by org_id instead of client_id/user_id
    filters = {"clerk_org_id": clerk_org_id}  # CRITICAL: Organization-scoped filtering
    # Note: agent_id filtering removed - agent functionality has been removed
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
    current_user: dict = Depends(require_admin_role),
    refresh: bool = False,
):
    """Get call with optional status refresh from Ultravox"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Filter by org_id via context (no need for explicit client_id filter)
    call = db.get_call(call_id, org_id=clerk_org_id)
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
                db.update("calls", {"id": call_id, "clerk_org_id": clerk_org_id}, update_data)
                call = db.get_call(call_id, org_id=clerk_org_id)
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
                "ultravox_call_id": call.get("ultravox_call_id"),
            }
            # Log error but don't fail the request
            logger.error(f"[CALLS] [GET] Failed to refresh call status from Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
    
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
    current_user: dict = Depends(require_admin_role),
):
    """Get call transcript"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Filter by org_id via context
    call = db.get_call(call_id, org_id=clerk_org_id)
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
            db.update("calls", {"id": call_id, "clerk_org_id": clerk_org_id}, {"transcript": transcript_data})
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
                "ultravox_call_id": call.get("ultravox_call_id"),
            }
            logger.error(f"[CALLS] [GET_TRANSCRIPT] Failed to fetch transcript (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
    current_user: dict = Depends(require_admin_role),
):
    """Get call recording URL"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Filter by org_id via context
    call = db.get_call(call_id, org_id=clerk_org_id)
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
            
            # Generate storage key: recordings/org_id/calls/call_id/recording.{ext}
            # Use org_id instead of client_id for organization-first approach
            storage_key = f"recordings/{clerk_org_id}/calls/{call_id}/recording.{file_ext}"
            
            # Upload recording
            logger.info(f"Uploading call recording: {storage_key} ({len(recording_data)} bytes)")
            storage_url = upload_bytes(
                bucket=settings.STORAGE_BUCKET_RECORDINGS,
                key=storage_key,
                data=recording_data,
                content_type=content_type,
            )
            
            # Update database with storage URL
            db.update("calls", {"id": call_id, "clerk_org_id": clerk_org_id}, {"recording_url": storage_url})
            logger.info(f"Call recording uploaded to storage and database updated: {storage_url}")
            
            recording_url = storage_url
        except httpx.HTTPError as e:
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "status_code": e.response.status_code if hasattr(e, 'response') and hasattr(e.response, 'status_code') else None,
                "full_traceback": traceback.format_exc(),
                "call_id": call_id,
                "ultravox_call_id": call.get("ultravox_call_id"),
            }
            logger.error(f"[CALLS] [GET_RECORDING] Failed to download recording from Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            raise NotFoundError("recording")
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
                "call_id": call_id,
                "ultravox_call_id": call.get("ultravox_call_id"),
            }
            logger.error(f"[CALLS] [GET_RECORDING] Error processing call recording (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
    current_user: dict = Depends(require_admin_role),
):
    """Update call (context and settings only)"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Check if call exists (filtered by org_id via context)
    call = db.get_call(call_id, org_id=clerk_org_id)
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
    db.update("calls", {"id": call_id, "clerk_org_id": clerk_org_id}, update_data)
    
    # Get updated call (filtered by org_id via context)
    updated_call = db.get_call(call_id, org_id=clerk_org_id)
    
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
    current_user: dict = Depends(require_admin_role),
):
    """Bulk delete calls"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    deleted_ids = []
    failed_ids = []
    
    for call_id in request_data.ids:
        try:
            # Filter by org_id via context
            call = db.get_call(call_id, org_id=clerk_org_id)
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
            db.delete("calls", {"id": call_id, "clerk_org_id": clerk_org_id})
            deleted_ids.append(call_id)
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
            }
            logger.error(f"[CALLS] [BULK_DELETE] Failed to delete call (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
    current_user: dict = Depends(require_admin_role),
):
    """Delete call"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Check if call exists (filtered by org_id via context)
    call = db.get_call(call_id, org_id=clerk_org_id)
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
    db.delete("calls", {"id": call_id, "clerk_org_id": clerk_org_id})
    
    return {
        "data": {"id": call_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

