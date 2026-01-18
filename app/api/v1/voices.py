"""
Voice Endpoints
"""
from fastapi import APIRouter, Header, Depends, Query, Request, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.background import BackgroundTasks
from starlette.requests import Request
from typing import Optional, List
from datetime import datetime
import uuid
import json
import logging
import httpx
import os
import traceback

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.storage import generate_presigned_url, check_object_exists
from app.core.exceptions import NotFoundError, ValidationError, PaymentRequiredError, ForbiddenError, ProviderError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_voice_training_started, emit_voice_created
from app.core.encryption import decrypt_api_key
from app.core.db_logging import log_to_database
from app.services.ultravox import ultravox_client
from app.models.schemas import (
    VoiceCreate,
    VoiceUpdate,
    VoiceResponse,
    VoicePresignRequest,
    PresignResponse,
    ResponseMeta,
)
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/files/presign")
async def presign_voice_files(
    request_data: VoicePresignRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get presigned URLs for voice sample uploads"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Generate presigned URLs
    uploads = []
    for i, file in enumerate(request_data.files):
        doc_id = str(uuid.uuid4())
        storage_key = f"uploads/client_{current_user['client_id']}/voices/{doc_id}/sample_{i}.{file.filename.split('.')[-1]}"
        
        url = generate_presigned_url(
            bucket=settings.STORAGE_BUCKET_UPLOADS,
            key=storage_key,
            operation="put_object",
            expires_in=3600,
            content_type=file.content_type,
        )
        
        uploads.append({
            "doc_id": doc_id,
            "storage_key": storage_key,
            "url": url,
            "headers": {"Content-Type": file.content_type},
        })
    
    return {
        "data": {"uploads": uploads},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("")
async def create_voice(
    voice_data: VoiceCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Create voice (native clone or external reference)"""
    request_id = getattr(request.state, "request_id", None)
    client_id = current_user.get("client_id")
    user_id = current_user.get("user_id")
    
    logger.info(f"[VOICES] [CREATE] Starting voice creation | name={voice_data.name} | strategy={voice_data.strategy} | client_id={client_id} | request_id={request_id}")
    
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        error_msg = "Insufficient permissions for voice creation"
        logger.warning(f"[VOICES] [CREATE] Permission denied | client_id={client_id} | user_id={user_id} | request_id={request_id}")
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="WARNING",
            category="voices_create",
            message=error_msg,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices",
            method="POST",
            status_code=403,
        )
        raise ForbiddenError("Insufficient permissions")
    
    # Check idempotency key
    body_dict = voice_data.dict() if hasattr(voice_data, 'dict') else json.loads(json.dumps(voice_data, default=str))
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
    
    # Check for duplicate external voice (same provider_voice_id for same client)
    if voice_data.strategy != "native" and voice_data.source.provider_voice_id:
        existing_voices = db.select(
            "voices",
            {
                "client_id": current_user["client_id"],
                "provider_voice_id": voice_data.source.provider_voice_id,
            }
        )
        if existing_voices and len(existing_voices) > 0:
            # Return existing voice instead of creating duplicate
            existing_voice = existing_voices[0]
            logger.info(f"[VOICES] [CREATE] Voice already exists | provider_voice_id={voice_data.source.provider_voice_id} | existing_id={existing_voice['id']} | request_id={request_id}")
            background_tasks.add_task(
                log_to_database,
                source="backend",
                level="INFO",
                category="voices_create",
                message=f"Voice creation skipped - already exists",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint="/api/v1/voices",
                method="POST",
                context={
                    "provider_voice_id": voice_data.source.provider_voice_id,
                    "existing_voice_id": existing_voice['id'],
                    "strategy": voice_data.strategy,
                },
            )
            return {
                "data": VoiceResponse(**existing_voice),
                "meta": ResponseMeta(
                    request_id=request_id or str(uuid.uuid4()),
                    ts=datetime.utcnow(),
                ),
            }
    
    # Credit check for native training
    client = None
    if voice_data.strategy == "native":
        client = db.get_client(current_user["client_id"])
        if not client or client.get("credits_balance", 0) < 50:
            raise PaymentRequiredError(
                "Insufficient credits for voice training. Required: 50",
                {"required": 50, "available": client.get("credits_balance", 0) if client else 0},
            )
    
    # ATOMIC RESOURCE CREATION (Saga Pattern)
    # Step 1: Insert record with status='creating' (temporary state)
    voice_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    provider = voice_data.provider_overrides.get("provider", "elevenlabs") if voice_data.provider_overrides else "elevenlabs"
    voice_type = "custom" if voice_data.strategy == "native" else "reference"
    
    # Prepare voice record for database (use ISO strings for storage)
    voice_db_record = {
        "id": voice_id,
        "client_id": current_user["client_id"],
        "name": voice_data.name,
        "provider": provider,
        "type": voice_type,
        "language": "en-US",
        "status": "creating",  # Temporary status - will be updated after Ultravox call
        "training_info": {
            "progress": 0,
            "started_at": now.isoformat(),
        } if voice_data.strategy == "native" else {},
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    
    # Store provider_voice_id for external voices (ElevenLabs voice ID)
    if voice_data.strategy != "native" and voice_data.source.provider_voice_id:
        voice_db_record["provider_voice_id"] = voice_data.source.provider_voice_id
        logger.info(f"Storing provider_voice_id: {voice_data.source.provider_voice_id} for voice {voice_id}")
    
    # Insert temporary record
    db.insert("voices", voice_db_record)
    logger.info(f"[VOICES] [CREATE] Voice record created (temporary) | voice_id={voice_id} | name={voice_data.name} | strategy={voice_data.strategy} | provider={provider} | request_id={request_id}")
    
    # Step 2: Call Ultravox API
    ultravox_voice_id = None
    provider_error_details = None
    
    try:
        logger.info(f"[VOICES] [CREATE] Calling Ultravox API | voice_id={voice_id} | strategy={voice_data.strategy} | request_id={request_id}")
        # Generate presigned URLs for Ultravox (for native voices)
        training_samples = []
        if voice_data.strategy == "native" and voice_data.source.samples:
            for sample in voice_data.source.samples:
                # Check storage file exists
                if not check_object_exists(settings.STORAGE_BUCKET_UPLOADS, sample.storage_key):
                    raise NotFoundError("voice sample", sample.storage_key)
                
                # Generate read-only presigned URL
                audio_url = generate_presigned_url(
                    bucket=settings.STORAGE_BUCKET_UPLOADS,
                    key=sample.storage_key,
                    operation="get_object",
                    expires_in=86400,
                )
                
                training_samples.append({
                    "text": sample.text,
                    "audio_url": audio_url,
                    "duration_seconds": sample.duration_seconds,
                })
        
        # Call Ultravox API
        if voice_data.strategy == "native":
            # Native voices MUST be created in Ultravox
            if not settings.ULTRAVOX_API_KEY:
                raise ValidationError("Ultravox API key is not configured. Native voice cloning requires Ultravox.")
            
            ultravox_data = {
                "name": voice_data.name,
                "provider": provider,
                "type": "custom",
                "language": "en-US",
                "training_samples": training_samples,
            }
            ultravox_response = await ultravox_client.create_voice(ultravox_data)
            # Ultravox returns "voiceId" (camelCase), not "id"
            ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
            if ultravox_voice_id:
                logger.info(f"[VOICES] [CREATE] Ultravox voice created | voice_id={voice_id} | ultravox_id={ultravox_voice_id} | request_id={request_id}")
            else:
                error_msg = "Ultravox response missing voice ID"
                logger.error(f"[VOICES] [CREATE] {error_msg} | voice_id={voice_id} | response={ultravox_response} | request_id={request_id}")
                raise ValueError(error_msg)
        else:
            # External voices can be created without Ultravox (optional)
            if settings.ULTRAVOX_API_KEY:
                ultravox_data = {
                    "name": voice_data.name,
                    "provider": provider,
                    "type": "reference",
                }
                if voice_data.source.provider_voice_id:
                    ultravox_data["provider_voice_id"] = voice_data.source.provider_voice_id
                ultravox_response = await ultravox_client.create_voice(ultravox_data)
                # Ultravox returns "voiceId" (camelCase), not "id"
                ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
                if ultravox_voice_id:
                    logger.info(f"[VOICES] [CREATE] Ultravox voice created (reference) | voice_id={voice_id} | ultravox_id={ultravox_voice_id} | request_id={request_id}")
        
        # Step 3: Update record to 'active' with ultravox_id (success path)
        update_data = {
            "status": "training" if voice_data.strategy == "native" else "active",
            "updated_at": now.isoformat(),
        }
        if ultravox_voice_id:
            update_data["ultravox_voice_id"] = ultravox_voice_id
        
        db.update("voices", {"id": voice_id}, update_data)
        voice_db_record.update(update_data)
        logger.info(f"[VOICES] [CREATE] Voice status updated | voice_id={voice_id} | status={update_data.get('status')} | ultravox_id={ultravox_voice_id} | request_id={request_id}")
        
    except Exception as e:
        # Step 4: Rollback - delete the temporary record and return error
        error_msg = f"Failed to create voice in Ultravox: {str(e)}"
        logger.error(f"[VOICES] [CREATE] {error_msg} | voice_id={voice_id} | strategy={voice_data.strategy} | request_id={request_id}", exc_info=True)
        
        # Extract error details if it's a ProviderError
        if isinstance(e, ProviderError):
            provider_error_details = e.details.get("provider_details", {})
            # Delete the temporary record
            db.delete("voices", {"id": voice_id, "client_id": current_user["client_id"]})
            logger.warning(f"[VOICES] [CREATE] Voice creation rolled back (ProviderError) | voice_id={voice_id} | request_id={request_id}")
            
            # Log error to database
            background_tasks.add_task(
                log_to_database,
                source="backend",
                level="ERROR",
                category="voices_create",
                message=f"Voice creation failed in Ultravox: {str(e)}",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint="/api/v1/voices",
                method="POST",
                status_code=e.details.get("httpStatus", 500),
                error_details={
                    "error_type": "ProviderError",
                    "provider": "ultravox",
                    "error_message": str(e),
                    "provider_details": provider_error_details,
                    "voice_id": voice_id,
                    "strategy": voice_data.strategy,
                    "traceback": traceback.format_exc(),
                },
            )
            
            # Re-raise with full error details
            raise ProviderError(
                provider="ultravox",
                message=str(e),
                http_status=e.details.get("httpStatus", 500),
                details=provider_error_details,
            )
        else:
            # Delete the temporary record
            db.delete("voices", {"id": voice_id, "client_id": current_user["client_id"]})
            logger.warning(f"[VOICES] [CREATE] Voice creation rolled back (Exception) | voice_id={voice_id} | request_id={request_id}")
            
            # Raise appropriate error
            error_msg = str(e)
            if not settings.ULTRAVOX_API_KEY and voice_data.strategy == "native":
                error_detail = "Ultravox API key is not configured. Native voice cloning requires Ultravox."
                background_tasks.add_task(
                    log_to_database,
                    source="backend",
                    level="ERROR",
                    category="voices_create",
                    message=error_detail,
                    request_id=request_id,
                    client_id=client_id,
                    user_id=user_id,
                    endpoint="/api/v1/voices",
                    method="POST",
                    status_code=400,
                    error_details={
                        "error_type": "ValidationError",
                        "error_message": error_detail,
                        "voice_id": voice_id,
                        "strategy": voice_data.strategy,
                    },
                )
                raise ValidationError(error_detail)
            
            # Log error to database
            background_tasks.add_task(
                log_to_database,
                source="backend",
                level="ERROR",
                category="voices_create",
                message=f"Voice creation failed: {error_msg}",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint="/api/v1/voices",
                method="POST",
                status_code=500,
                error_details={
                    "error_type": type(e).__name__,
                    "error_message": error_msg,
                    "voice_id": voice_id,
                    "strategy": voice_data.strategy,
                    "traceback": traceback.format_exc(),
                },
            )
            
            raise ProviderError(
                provider="ultravox",
                message=f"Failed to create voice in Ultravox: {error_msg}",
                http_status=500,
                details={"error": error_msg},
            )
    
    # Prepare voice record for response (use datetime objects for Pydantic)
    voice_record = voice_db_record.copy()
    voice_record["created_at"] = now
    voice_record["updated_at"] = now
    if ultravox_voice_id:
        voice_record["ultravox_voice_id"] = ultravox_voice_id
    
    # Debit credits if native
    if voice_data.strategy == "native" and client:
        db.insert(
            "credit_transactions",
            {
                "client_id": current_user["client_id"],
                "type": "spent",
                "amount": 50,
                "reference_type": "voice_training",
                "reference_id": voice_id,
                "description": f"Voice training: {voice_data.name}",
            },
        )
        db.update(
            "clients",
            {"id": current_user["client_id"]},
            {"credits_balance": client["credits_balance"] - 50},
        )
    
    logger.info(f"[VOICES] [CREATE] Voice creation successful | voice_id={voice_id} | name={voice_data.name} | status={voice_record.get('status')} | ultravox_id={ultravox_voice_id} | request_id={request_id}")
    
    response_data = {
        "data": VoiceResponse(**voice_record),
        "meta": ResponseMeta(
            request_id=request_id or str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
    
    # Log success to database
    background_tasks.add_task(
        log_to_database,
        source="backend",
        level="INFO",
        category="voices_create",
        message=f"Voice created successfully: {voice_data.name}",
        request_id=request_id,
        client_id=client_id,
        user_id=user_id,
        endpoint="/api/v1/voices",
        method="POST",
        status_code=201,
        context={
            "voice_id": voice_id,
            "voice_name": voice_data.name,
            "strategy": voice_data.strategy,
            "provider": provider,
            "type": voice_type,
            "status": voice_record.get("status"),
            "ultravox_voice_id": ultravox_voice_id,
            "provider_voice_id": voice_record.get("provider_voice_id"),
        },
    )
    
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
async def list_voices(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    ownership: Optional[str] = Query("public", description="Filter by ownership: 'public' or 'private'"),
    source: Optional[str] = Query(None, description="Filter by source: 'ultravox' (pre-loaded voices) or 'custom' (user-created voices)"),
):
    """
    List voices - supports two sources:
    - source='ultravox' or not provided: Returns voices from Ultravox API (for Explore tab)
    - source='custom': Returns custom voices from database (for My Voices tab)
    """
    request_id = getattr(request.state, "request_id", None)
    client_id = current_user.get("client_id")
    user_id = current_user.get("user_id")
    
    try:
        # If source is 'custom', fetch from database
        if source == "custom":
            logger.info(f"[VOICES] [LIST] Fetching custom voices from database | client_id={client_id} | request_id={request_id}")
            
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
            # Query database for custom voices (type='custom' and client_id matches)
            custom_voices = db.select(
                "voices",
                {
                    "client_id": client_id,
                    "type": "custom"
                },
                order_by="created_at DESC"
            )
            
            # Convert database records to VoiceResponse format
            voices_data = []
            for voice_record in custom_voices:
                try:
                    # Parse datetime fields safely
                    created_at = voice_record.get("created_at")
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            created_at = datetime.utcnow()
                    elif created_at is None:
                        created_at = datetime.utcnow()
                    
                    updated_at = voice_record.get("updated_at")
                    if isinstance(updated_at, str):
                        try:
                            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            updated_at = datetime.utcnow()
                    elif updated_at is None:
                        updated_at = datetime.utcnow()
                    
                    voice_data = {
                        "id": voice_record.get("id"),
                        "client_id": voice_record.get("client_id"),
                        "name": voice_record.get("name", "Untitled Voice"),
                        "provider": voice_record.get("provider", "elevenlabs"),
                        "type": voice_record.get("type", "custom"),
                        "language": voice_record.get("language", "en-US"),
                        "status": voice_record.get("status", "active"),
                        "provider_voice_id": voice_record.get("provider_voice_id"),
                        "ultravox_voice_id": voice_record.get("ultravox_voice_id"),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    }
                    
                    if voice_record.get("description"):
                        voice_data["description"] = voice_record.get("description")
                    
                    if voice_record.get("training_info"):
                        voice_data["training_info"] = voice_record.get("training_info")
                    
                    voices_data.append(VoiceResponse(**voice_data))
                except Exception as e:
                    logger.warning(f"[VOICES] [LIST] Failed to process custom voice {voice_record.get('id')}: {e}")
                    continue
            
            logger.info(f"[VOICES] [LIST] Returning {len(voices_data)} custom voices from database | request_id={request_id}")
            
            # Log to database
            background_tasks.add_task(
                log_to_database,
                source="backend",
                level="INFO",
                category="voices_list",
                message=f"Listed {len(voices_data)} custom voices from database",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint="/api/v1/voices",
                method="GET",
                context={
                    "voice_count": len(voices_data),
                    "source": "custom",
                },
            )
    
    return {
                "data": voices_data,
        "meta": ResponseMeta(
                    request_id=request_id or str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
        
        # Default: Fetch from Ultravox (for Explore tab)
        logger.info(f"[VOICES] [LIST] Fetching voices from Ultravox | client_id={client_id} | ownership={ownership} | source={source} | request_id={request_id}")
        
        # Check if Ultravox is configured
        if not settings.ULTRAVOX_API_KEY:
            error_msg = "Ultravox API key not configured"
            logger.error(f"[VOICES] [LIST] {error_msg} | client_id={client_id} | request_id={request_id}")
            raise ValidationError(error_msg)
        
        # Fetch voices directly from Ultravox - SIMPLE!
        ultravox_voices = await ultravox_client.list_voices(ownership=ownership)
        
        logger.info(f"[VOICES] [LIST] Fetched {len(ultravox_voices)} voices from Ultravox | request_id={request_id}")
        
        # Convert Ultravox voices to our response format and save to database
        voices_data = []
        db = DatabaseService(current_user["token"])
        db.set_auth(current_user["token"])
        
        for uv_voice in ultravox_voices:
            try:
                # Extract provider_voice_id from definition
                definition = uv_voice.get("definition", {})
                provider_voice_id = None
                provider_name = uv_voice.get("provider", "elevenlabs").lower()
                
                if "elevenLabs" in definition:
                    provider_voice_id = definition["elevenLabs"].get("voiceId")
                elif "cartesia" in definition:
                    provider_voice_id = definition["cartesia"].get("voiceId")
                elif "lmnt" in definition:
                    provider_voice_id = definition["lmnt"].get("voiceId")
                elif "google" in definition:
                    provider_voice_id = definition["google"].get("voiceId")
                
                # Skip if no provider_voice_id (generic voices)
                if not provider_voice_id:
                    continue
                
                ultravox_voice_id = uv_voice.get("voiceId")
                if not ultravox_voice_id:
                    logger.warning(f"[VOICES] [LIST] Voice missing voiceId, skipping | name={uv_voice.get('name')} | request_id={request_id}")
                    continue
                
                # Check if voice already exists in database (by ultravox_voice_id)
                existing_voice = db.select_one(
                    "voices",
                    {
                        "ultravox_voice_id": ultravox_voice_id,
                        "client_id": client_id
                    }
                )
                
                now = datetime.utcnow()
                voice_id = None
                
                if existing_voice:
                    # Voice exists - update it with latest info
                    voice_id = existing_voice.get("id")
                    update_data = {
                        "name": uv_voice.get("name", "Untitled Voice"),
                        "provider": provider_name,
                        "provider_voice_id": provider_voice_id,
                        "language": uv_voice.get("primaryLanguage", "en-US") or "en-US",
                        "status": "active",
                        "updated_at": now.isoformat(),
                    }
                    if uv_voice.get("description"):
                        update_data["description"] = uv_voice.get("description")
                    
                    db.update("voices", {"id": voice_id}, update_data)
                    logger.debug(f"[VOICES] [LIST] Updated existing voice in DB | voice_id={voice_id} | ultravox_id={ultravox_voice_id} | request_id={request_id}")
                else:
                    # Voice doesn't exist - create it in database
                    voice_id = str(uuid.uuid4())
                    voice_record = {
                        "id": voice_id,
                        "client_id": client_id,
                        "name": uv_voice.get("name", "Untitled Voice"),
                        "provider": provider_name,
                        "type": "reference",
                        "language": uv_voice.get("primaryLanguage", "en-US") or "en-US",
                        "status": "active",
                        "provider_voice_id": provider_voice_id,
                        "ultravox_voice_id": ultravox_voice_id,  # IMPORTANT: Save the Ultravox ID!
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                    if uv_voice.get("description"):
                        voice_record["description"] = uv_voice.get("description")
                    
                    db.insert("voices", voice_record)
                    logger.info(f"[VOICES] [LIST] Saved new voice to DB | voice_id={voice_id} | ultravox_id={ultravox_voice_id} | name={voice_record['name']} | request_id={request_id}")
                
                # Map to our VoiceResponse format
                voice_data = {
                    "id": voice_id,  # Use our database ID
                    "client_id": client_id,
                    "name": uv_voice.get("name", "Untitled Voice"),
                    "provider": provider_name,
                    "type": "reference",
                    "language": uv_voice.get("primaryLanguage", "en-US") or "en-US",
                    "status": "active",
                    "provider_voice_id": provider_voice_id,
                    "ultravox_voice_id": ultravox_voice_id,  # Include Ultravox ID in response
                    "created_at": now,
                    "updated_at": now,
                }
                
                if uv_voice.get("description"):
                    voice_data["description"] = uv_voice.get("description")
                
                voices_data.append(VoiceResponse(**voice_data))
            except Exception as e:
                logger.warning(f"[VOICES] [LIST] Failed to process voice {uv_voice.get('voiceId')}: {e}", exc_info=True)
                continue
        
        logger.info(f"[VOICES] [LIST] Returning {len(voices_data)} voices | request_id={request_id}")
        
        # Log to database
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="INFO",
            category="voices_list",
            message=f"Listed {len(voices_data)} voices from Ultravox",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices",
            method="GET",
            context={
                "voice_count": len(voices_data),
                "ownership": ownership,
            },
        )
        
        return {
            "data": voices_data,
            "meta": ResponseMeta(
                request_id=request_id or str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except ProviderError as e:
        # Ultravox API error - return empty array instead of failing
        error_msg = f"Failed to fetch voices from Ultravox: {str(e)}"
        logger.error(f"[VOICES] [LIST] Ultravox API Error | {error_msg} | client_id={client_id} | request_id={request_id}", exc_info=True)
        
        # Log error to database
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="ERROR",
            category="voices_list",
            message=error_msg,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices",
            method="GET",
            status_code=e.details.get("httpStatus", 500) if hasattr(e, "details") else 500,
            error_details={
                "error_type": "ProviderError",
                "provider": "ultravox",
                "error_message": str(e),
                "details": e.details if hasattr(e, "details") else {},
                "traceback": traceback.format_exc(),
            },
        )
        
        # Return empty array instead of raising error - allows frontend to continue working
        return {
            "data": [],
            "meta": ResponseMeta(
                request_id=request_id or str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        error_msg = f"Failed to list voices: {str(e)}"
        logger.error(f"[VOICES] [LIST] Error | {error_msg} | client_id={client_id} | request_id={request_id}", exc_info=True)
        
        # Log error to database
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="ERROR",
            category="voices_list",
            message=error_msg,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices",
            method="GET",
            status_code=500,
            error_details={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc(),
            },
        )
        
        # Return empty array instead of raising error - allows frontend to continue working
        return {
            "data": [],
            "meta": ResponseMeta(
                request_id=request_id or str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }


@router.post("/sync-from-ultravox")
async def sync_voices_from_ultravox(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    ownership: Optional[str] = Query("public", description="Filter by ownership: 'public' or 'private'"),
    provider: Optional[List[str]] = Query(None, description="Filter by provider (e.g., 'eleven_labs', 'cartesia', 'lmnt', 'google')"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Sync pre-loaded voices from Ultravox into the local database.
    This imports public voices (pre-loaded voices) that exist in Ultravox but not in your database.
    """
    request_id = getattr(request.state, "request_id", None)
    client_id = current_user.get("client_id")
    user_id = current_user.get("user_id")
    
    logger.info(f"[VOICES] [SYNC] Starting sync from Ultravox | client_id={client_id} | ownership={ownership} | provider={provider} | request_id={request_id}")
    
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        error_msg = "Insufficient permissions for voice sync"
        logger.warning(f"[VOICES] [SYNC] Permission denied | client_id={client_id} | user_id={user_id} | request_id={request_id}")
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="WARNING",
            category="voices_sync",
            message=error_msg,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices/sync-from-ultravox",
            method="POST",
            status_code=403,
        )
        raise ForbiddenError("Insufficient permissions")
    
    # Check if Ultravox is configured
    if not settings.ULTRAVOX_API_KEY:
        error_msg = "Ultravox API key not configured"
        logger.error(f"[VOICES] [SYNC] Configuration error | {error_msg} | client_id={client_id} | request_id={request_id}")
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="ERROR",
            category="voices_sync",
            message=error_msg,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices/sync-from-ultravox",
            method="POST",
            status_code=500,
        )
        raise ValidationError("Ultravox API key not configured. Please set ULTRAVOX_API_KEY environment variable.")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    try:
        logger.info(f"[VOICES] [SYNC] Fetching voices from Ultravox | ownership={ownership} | provider={provider} | request_id={request_id}")
        
        # #region agent log
        import json
        try:
            with open(r"d:\Users\Admin\Downloads\Truedy Main\.cursor\debug.log", "a", encoding="utf-8") as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"voices.py:475","message":"sync_voices_from_ultravox called","data":{"ownership":ownership,"provider":provider,"client_id":client_id},"timestamp":int(__import__("time").time()*1000)})+"\n")
        except: pass
        # #endregion
        
        # Fetch voices from Ultravox (public voices are pre-loaded)
        ultravox_voices = await ultravox_client.list_voices(
            ownership=ownership,
            provider=provider
        )
        
        # #region agent log
        try:
            with open(r"d:\Users\Admin\Downloads\Truedy Main\.cursor\debug.log", "a", encoding="utf-8") as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"J","location":"voices.py:482","message":"Ultravox voices fetched","data":{"ultravox_voices_count":len(ultravox_voices),"first_voice_sample":ultravox_voices[0] if ultravox_voices else None},"timestamp":int(__import__("time").time()*1000)})+"\n")
        except: pass
        # #endregion
        
        logger.info(f"[VOICES] [SYNC] Fetched {len(ultravox_voices)} voices from Ultravox | request_id={request_id}")
        
        imported_count = 0
        skipped_count = 0
        errors = []
        skipped_reasons = {"no_provider_id": 0, "already_exists": 0}
        
        for uv_voice in ultravox_voices:
            try:
                ultravox_voice_id = uv_voice.get("voiceId")
                provider_voice_id = uv_voice.get("provider_voice_id")
                provider_name = uv_voice.get("provider", "elevenlabs")
                
                # Skip if no provider_voice_id (can't use generic voices as reference)
                if not provider_voice_id:
                    skipped_count += 1
                    skipped_reasons["no_provider_id"] += 1
                    logger.debug(f"[VOICES] [SYNC] Skipping voice (no provider_id) | voice_id={ultravox_voice_id} | name={uv_voice.get('name')} | request_id={request_id}")
                    continue
                
                # Check if voice already exists (by ultravox_voice_id or provider_voice_id)
                existing_by_ultravox = db.select_one(
                    "voices",
                    {
                        "client_id": current_user["client_id"],
                        "ultravox_voice_id": ultravox_voice_id
                    }
                )
                
                existing_by_provider = None
                if provider_voice_id:
                    existing_by_provider = db.select_one(
                        "voices",
                        {
                            "client_id": current_user["client_id"],
                            "provider_voice_id": provider_voice_id,
                            "provider": provider_name
                        }
                    )
                
                if existing_by_ultravox or existing_by_provider:
                    skipped_count += 1
                    skipped_reasons["already_exists"] += 1
                    logger.debug(f"[VOICES] [SYNC] Skipping voice (already exists) | voice_id={ultravox_voice_id} | name={uv_voice.get('name')} | request_id={request_id}")
                    continue
                
                # Import voice into database
                voice_id = str(uuid.uuid4())
                now = datetime.utcnow()
                
                # Map Ultravox voice to our database structure
                voice_record = {
                    "id": voice_id,
                    "client_id": current_user["client_id"],
                    "name": uv_voice.get("name", "Untitled Voice"),
                    "provider": provider_name,
                    "type": "reference",  # Pre-loaded voices are reference type
                    "language": uv_voice.get("primaryLanguage", "en-US") or "en-US",
                    "status": "active",  # Pre-loaded voices are ready to use
                    "provider_voice_id": provider_voice_id,
                    "ultravox_voice_id": ultravox_voice_id,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
                
                # Add description if available
                if uv_voice.get("description"):
                    voice_record["description"] = uv_voice.get("description")
                
                # Store provider-specific settings in a metadata field if needed
                definition = uv_voice.get("definition", {})
                if definition:
                    voice_record["provider_settings"] = definition
                
                db.insert("voices", voice_record)
                imported_count += 1
                logger.info(f"[VOICES] [SYNC] Imported voice | voice_id={voice_id} | ultravox_id={ultravox_voice_id} | name={voice_record['name']} | provider={provider_name} | request_id={request_id}")
                
            except Exception as e:
                error_detail = {
                    "voice_id": uv_voice.get("voiceId"),
                    "name": uv_voice.get("name"),
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
                logger.error(f"[VOICES] [SYNC] Failed to import voice | voice_id={uv_voice.get('voiceId')} | name={uv_voice.get('name')} | error={str(e)} | request_id={request_id}", exc_info=True)
                errors.append(error_detail)
        
        # Log sync completion
        logger.info(f"[VOICES] [SYNC] Sync completed | imported={imported_count} | skipped={skipped_count} | total={len(ultravox_voices)} | errors={len(errors)} | request_id={request_id}")
        
        # Log to database
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="INFO",
            category="voices_sync",
            message=f"Synced voices from Ultravox: {imported_count} imported, {skipped_count} skipped",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices/sync-from-ultravox",
            method="POST",
            context={
                "imported": imported_count,
                "skipped": skipped_count,
                "total_fetched": len(ultravox_voices),
                "skipped_reasons": skipped_reasons,
                "ownership_filter": ownership,
                "provider_filter": provider,
                "error_count": len(errors),
            },
        )
        
        return {
            "data": {
                "imported": imported_count,
                "skipped": skipped_count,
                "total_fetched": len(ultravox_voices),
                "skipped_reasons": skipped_reasons,
                "errors": errors if errors else None,
            },
            "meta": ResponseMeta(
                request_id=request_id or str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
    except Exception as e:
        error_msg = f"Failed to sync voices from Ultravox: {str(e)}"
        logger.error(f"[VOICES] [SYNC] Fatal error | {error_msg} | client_id={client_id} | request_id={request_id}", exc_info=True)
        
        # Log error to database
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="ERROR",
            category="voices_sync",
            message=error_msg,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint="/api/v1/voices/sync-from-ultravox",
            method="POST",
            status_code=500,
            error_details={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc(),
            },
        )
        raise ValidationError(error_msg, {"error": str(e)})


@router.get("/{voice_id}")
async def get_voice(
    voice_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Get single voice - returns what is in the DB immediately.
    Use /voices/{voice_id}/sync for status reconciliation.
    """
    request_id = getattr(request.state, "request_id", None)
    client_id = current_user.get("client_id")
    user_id = current_user.get("user_id")
    
    logger.info(f"[VOICES] [GET] Fetching voice | voice_id={voice_id} | client_id={client_id} | request_id={request_id}")
    
    try:
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
            error_msg = f"Voice not found: {voice_id}"
            logger.warning(f"[VOICES] [GET] {error_msg} | client_id={client_id} | request_id={request_id}")
            background_tasks.add_task(
                log_to_database,
                source="backend",
                level="WARNING",
                category="voices_get",
                message=error_msg,
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=f"/api/v1/voices/{voice_id}",
                method="GET",
                status_code=404,
            )
        raise NotFoundError("voice", voice_id)
    
        logger.info(f"[VOICES] [GET] Voice found | voice_id={voice_id} | name={voice.get('name')} | status={voice.get('status')} | request_id={request_id}")
        
        # Log to database
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="INFO",
            category="voices_get",
            message=f"Retrieved voice: {voice.get('name')}",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=f"/api/v1/voices/{voice_id}",
            method="GET",
            context={
                "voice_id": voice_id,
                "voice_name": voice.get("name"),
                "status": voice.get("status"),
                "provider": voice.get("provider"),
                "type": voice.get("type"),
            },
        )
        
    return {
        "data": VoiceResponse(**voice),
        "meta": ResponseMeta(
                request_id=request_id or str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
    except NotFoundError:
        raise
    except Exception as e:
        error_msg = f"Failed to get voice: {str(e)}"
        logger.error(f"[VOICES] [GET] {error_msg} | voice_id={voice_id} | client_id={client_id} | request_id={request_id}", exc_info=True)
        background_tasks.add_task(
            log_to_database,
            source="backend",
            level="ERROR",
            category="voices_get",
            message=error_msg,
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=f"/api/v1/voices/{voice_id}",
            method="GET",
            status_code=500,
            error_details={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "voice_id": voice_id,
                "traceback": traceback.format_exc(),
            },
        )
        raise


@router.patch("/{voice_id}")
async def update_voice(
    voice_id: str,
    voice_data: VoiceUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update voice (name and description only)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if voice exists
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    # Only allow updating name and description
    # Other fields (provider, type, etc.) cannot be changed after creation
    update_data = voice_data.dict(exclude_unset=True)
    if not update_data:
        # No updates provided
        return {
            "data": VoiceResponse(**voice),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    # Update database
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("voices", {"id": voice_id}, update_data)
    
    # Get updated voice
    updated_voice = db.get_voice(voice_id, current_user["client_id"])
    
    return {
        "data": VoiceResponse(**updated_voice),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/{voice_id}")
async def delete_voice(
    voice_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete voice"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    # Delete from Ultravox if it exists there
    if voice.get("ultravox_voice_id"):
        try:
            from app.core.config import settings
            if settings.ULTRAVOX_API_KEY:
                # Note: Ultravox may not have a delete endpoint, but we'll try if it exists
                # For now, we'll just delete from our database
                logger.info(f"Voice {voice_id} has Ultravox ID {voice.get('ultravox_voice_id')}, but Ultravox deletion not implemented")
        except Exception as e:
            logger.warning(f"Failed to handle Ultravox deletion for voice {voice_id}: {e}")
    
    # Delete from database
    db.delete("voices", {"id": voice_id, "client_id": current_user["client_id"]})
    
    return {
        "data": {"id": voice_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{voice_id}/sync")
async def sync_voice_with_ultravox(
    voice_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Sync voice with Ultravox - reconciles status and creates in Ultravox if needed.
    This is the async reconciliation endpoint that should be called when needed.
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    # Check if Ultravox is configured
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key not configured. Please set ULTRAVOX_API_KEY environment variable.")
    
    # If voice has ultravox_voice_id, reconcile status
    if voice.get("ultravox_voice_id"):
        try:
            ultravox_voice = await ultravox_client.get_voice(voice.get("ultravox_voice_id"))
            
            # Use reconciliation helper
            drift = ultravox_client.reconcile_resource(voice, ultravox_voice, "voice")
            
            if drift["has_drift"]:
                update_data = {}
                
                if drift["recommended_action"] == "update_status":
                    status_mapping = {
                        "training": "training",
                        "active": "active",
                        "ready": "active",
                        "failed": "failed",
                        "error": "failed",
                    }
                    ultravox_status = ultravox_voice.get("status", "").lower()
                    new_status = status_mapping.get(ultravox_status, voice.get("status"))
                    update_data["status"] = new_status
                    
                    # Update training_info if available
                    if ultravox_voice.get("training_info"):
                        update_data["training_info"] = ultravox_voice.get("training_info")
                
                if drift["recommended_action"] == "sync_ultravox_id":
                    update_data["ultravox_voice_id"] = drift["drift_details"]["missing_ultravox_id"]
                
                if update_data:
                    update_data["updated_at"] = datetime.utcnow().isoformat()
                    db.update("voices", {"id": voice_id}, update_data)
                    voice = db.get_voice(voice_id, current_user["client_id"])
            
            return {
                "data": VoiceResponse(**voice),
                "meta": ResponseMeta(
                    request_id=str(uuid.uuid4()),
                    ts=datetime.utcnow(),
                ),
                "message": "Voice synced with Ultravox",
                "drift": drift,
            }
        except Exception as e:
            logger.error(f"Failed to sync voice {voice_id} with Ultravox: {e}", exc_info=True)
            raise ValidationError(f"Failed to sync voice with Ultravox: {str(e)}", {"error": str(e)})
    
    # If voice doesn't have ultravox_voice_id, try to create it in Ultravox
    try:
        if voice.get("type") == "custom":
            # Native voices need training samples - can't sync without them
            raise ValidationError(
                "Native voices cannot be synced without training samples. Please recreate the voice with training samples.",
                {"voice_type": "custom"}
            )
        else:
            # External/reference voices
            ultravox_voice_data = {
                "name": voice.get("name"),
                "provider": voice.get("provider", "elevenlabs"),
                "type": "reference",
            }
            if voice.get("provider_voice_id"):
                ultravox_voice_data["provider_voice_id"] = voice.get("provider_voice_id")
            
            logger.info(f"Attempting to create voice in Ultravox: {ultravox_voice_data}")
            ultravox_response = await ultravox_client.create_voice(ultravox_voice_data)
            
            # Ultravox returns "voiceId" (camelCase), not "id"
            ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
            if ultravox_voice_id:
                # Update voice with Ultravox ID
                db.update(
                    "voices",
                    {"id": voice_id},
                    {"ultravox_voice_id": ultravox_voice_id},
                )
                voice["ultravox_voice_id"] = ultravox_voice_id
                
                return {
                    "data": VoiceResponse(**voice),
                    "meta": ResponseMeta(
                        request_id=str(uuid.uuid4()),
                        ts=datetime.utcnow(),
                    ),
                    "message": "Voice successfully synced with Ultravox",
                }
            else:
                raise ValidationError("Failed to create voice in Ultravox - response missing ID")
    except Exception as e:
        logger.error(f"Failed to sync voice {voice_id} with Ultravox: {e}", exc_info=True)
        error_msg = str(e)
        if "404" in error_msg:
            error_msg = "Ultravox API endpoint not found. Please check ULTRAVOX_BASE_URL and ULTRAVOX_API_KEY configuration."
        elif "401" in error_msg or "403" in error_msg:
            error_msg = "Ultravox API authentication failed. Please check your ULTRAVOX_API_KEY."
        raise ValidationError(f"Failed to sync voice with Ultravox: {error_msg}", {"error": str(e)})


@router.get("/{voice_id}/preview")
async def preview_voice(
    voice_id: str,
    text: Optional[str] = Query(None, description="Text to convert to speech for preview (not used with Ultravox preview)"),
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Preview a voice using Ultravox's preview endpoint.
    Works for both custom voices (from DB) and Ultravox voices (direct from Ultravox).
    Uses Ultravox API key - no user credentials required.
    """
    request_id = getattr(request.state, "request_id", None)
    client_id = current_user.get("client_id")
    user_id = current_user.get("user_id")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Try to get voice from database first (for custom voices)
    # Use try-except to handle cases where voice might not exist
    voice = None
    ultravox_voice_id = None
    
    try:
        voice = db.get_voice(voice_id, client_id)
    except Exception as e:
        # Voice not found in database - this is OK for Ultravox voices
        logger.debug(f"[VOICES] [PREVIEW] Voice not found in database (may be Ultravox voice) | voice_id={voice_id} | error={str(e)} | request_id={request_id}")
    
    if voice:
        # Voice exists in database - use ultravox_voice_id if available
        logger.info(f"[VOICES] [PREVIEW] Voice found in database | voice_id={voice_id} | ultravox_voice_id={voice.get('ultravox_voice_id')} | status={voice.get('status')} | request_id={request_id}")
        
        # Check if voice is active (only for custom voices in our DB)
        # For custom voices, they must be active to preview
        voice_status = voice.get("status")
        if voice_status and voice_status != "active":
            raise ValidationError("Voice must be active to preview", {"status": voice_status})
        
        ultravox_voice_id = voice.get("ultravox_voice_id")
        if not ultravox_voice_id:
            # Custom voice without ultravox_voice_id cannot be previewed
            # This means the voice was never successfully created in Ultravox
            error_msg = "Voice does not have an Ultravox ID. The voice may not be ready for preview."
            logger.error(f"[VOICES] [PREVIEW] {error_msg} | voice_id={voice_id} | request_id={request_id}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "voice_not_ready",
                    "message": error_msg
                }
            )
    else:
        # Voice not in database - assume voice_id is the Ultravox voice ID
        # This is the case for voices from Explore tab (Ultravox pre-loaded voices)
        logger.info(f"[VOICES] [PREVIEW] Voice not in database, using voice_id as Ultravox ID | voice_id={voice_id} | request_id={request_id}")
        ultravox_voice_id = voice_id
    
    # Check if Ultravox is configured
    if not settings.ULTRAVOX_API_KEY:
        error_msg = "Ultravox API key not configured"
        logger.error(f"[VOICES] [PREVIEW] {error_msg} | voice_id={voice_id} | request_id={request_id}")
        raise ValidationError(error_msg)
    
    try:
        # Get preview audio from Ultravox
        logger.info(f"[VOICES] [PREVIEW] Fetching preview from Ultravox | ultravox_voice_id={ultravox_voice_id} | request_id={request_id}")
        audio_bytes = await ultravox_client.get_voice_preview(ultravox_voice_id)
        
        logger.info(f"[VOICES] [PREVIEW] Preview audio received | size={len(audio_bytes)} bytes | request_id={request_id}")
        
        # Return audio stream (Ultravox returns audio/wav)
        return StreamingResponse(
            iter([audio_bytes]),
            media_type="audio/wav",
                headers={
                "Content-Disposition": f'inline; filename="voice-preview.wav"',
            }
        )
    except ProviderError as e:
        error_msg = f"Failed to get voice preview from Ultravox: {str(e)}"
        logger.error(f"[VOICES] [PREVIEW] {error_msg} | ultravox_voice_id={ultravox_voice_id} | request_id={request_id}", exc_info=True)
        # Return proper HTTP error instead of ValidationError for API errors
        http_status = e.http_status if hasattr(e, "http_status") else 500
        raise HTTPException(
            status_code=http_status,
            detail={
                "error": "ultravox_api_error",
                "message": error_msg,
                "details": e.details if hasattr(e, "details") else {}
            }
        )
    except ValidationError as e:
        # Re-raise ValidationError as-is (it's already a proper HTTP exception)
        raise
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    except Exception as e:
        error_msg = f"Failed to generate voice preview: {str(e)}"
        logger.error(f"[VOICES] [PREVIEW] {error_msg} | ultravox_voice_id={ultravox_voice_id} | request_id={request_id}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": error_msg
            }
        )

