"""
Voice Endpoints
"""
from fastapi import APIRouter, Header, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.requests import Request
from typing import Optional
from datetime import datetime
import uuid
import json
import logging
import httpx
import os

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.storage import generate_presigned_url, check_object_exists
from app.core.exceptions import NotFoundError, ValidationError, PaymentRequiredError, ForbiddenError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_voice_training_started, emit_voice_created
from app.core.encryption import decrypt_api_key
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
        s3_key = f"uploads/client_{current_user['client_id']}/voices/{doc_id}/sample_{i}.{file.filename.split('.')[-1]}"
        
        url = generate_presigned_url(
            bucket=settings.S3_BUCKET_UPLOADS,
            key=s3_key,
            operation="put_object",
            expires_in=3600,
            content_type=file.content_type,
        )
        
        uploads.append({
            "doc_id": doc_id,
            "s3_key": s3_key,
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
):
    """Create voice (native clone or external reference)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
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
            logger.info(f"Voice with provider_voice_id {voice_data.source.provider_voice_id} already exists: {existing_voice['id']}")
            return {
                "data": VoiceResponse(**existing_voice),
                "meta": ResponseMeta(
                    request_id=str(uuid.uuid4()),
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
    
    # Step 2: Call Ultravox API
    ultravox_voice_id = None
    provider_error_details = None
    
    try:
        # Generate presigned URLs for Ultravox (for native voices)
        training_samples = []
        if voice_data.strategy == "native" and voice_data.source.samples:
            for sample in voice_data.source.samples:
                # Check storage file exists
                if not check_object_exists(settings.S3_BUCKET_UPLOADS, sample.s3_key):
                    raise NotFoundError("voice sample", sample.s3_key)
                
                # Generate read-only presigned URL
                audio_url = generate_presigned_url(
                    bucket=settings.S3_BUCKET_UPLOADS,
                    key=sample.s3_key,
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
            if ultravox_response and ultravox_response.get("id"):
                ultravox_voice_id = ultravox_response.get("id")
            else:
                raise ValueError("Ultravox response missing voice ID")
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
                if ultravox_response and ultravox_response.get("id"):
                    ultravox_voice_id = ultravox_response.get("id")
        
        # Step 3: Update record to 'active' with ultravox_id (success path)
        update_data = {
            "status": "training" if voice_data.strategy == "native" else "active",
            "updated_at": now.isoformat(),
        }
        if ultravox_voice_id:
            update_data["ultravox_voice_id"] = ultravox_voice_id
        
        db.update("voices", {"id": voice_id}, update_data)
        voice_db_record.update(update_data)
        
    except Exception as e:
        # Step 4: Rollback - delete the temporary record and return error
        logger.error(f"Failed to create voice in Ultravox: {e}", exc_info=True)
        
        # Extract error details if it's a ProviderError
        if isinstance(e, ProviderError):
            provider_error_details = e.details.get("provider_details", {})
            # Delete the temporary record
            db.delete("voices", {"id": voice_id, "client_id": current_user["client_id"]})
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
            # Raise appropriate error
            error_msg = str(e)
            if not settings.ULTRAVOX_API_KEY and voice_data.strategy == "native":
                raise ValidationError("Ultravox API key is not configured. Native voice cloning requires Ultravox.")
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
    
    response_data = {
        "data": VoiceResponse(**voice_record),
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
async def list_voices(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    List voices - returns what is in the DB immediately.
    Use /voices/{voice_id}/sync or /voices/sync-all for status reconciliation.
    """
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Get voices from database - return immediately without polling
    voices = db.select("voices", {"client_id": current_user["client_id"]}, "created_at")
    
    return {
        "data": [VoiceResponse(**voice) for voice in voices],
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/{voice_id}")
async def get_voice(
    voice_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Get single voice - returns what is in the DB immediately.
    Use /voices/{voice_id}/sync for status reconciliation.
    """
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    return {
        "data": VoiceResponse(**voice),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


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
            
            if ultravox_response and ultravox_response.get("id"):
                ultravox_voice_id = ultravox_response.get("id")
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
    text: Optional[str] = Query(None, description="Text to convert to speech for preview"),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Preview a voice by generating audio using the provider's TTS API.
    Currently supports ElevenLabs voices.
    """
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Get voice from database
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    logger.info(f"Voice data retrieved: id={voice_id}, provider={voice.get('provider')}, provider_voice_id={voice.get('provider_voice_id')}, status={voice.get('status')}")
    
    # Check if voice is active
    if voice.get("status") != "active":
        raise ValidationError("Voice must be active to preview", {"status": voice.get("status")})
    
    provider = voice.get("provider", "").lower()
    provider_voice_id = voice.get("provider_voice_id")
    
    logger.info(f"Preview request - Provider: {provider}, Provider Voice ID: {provider_voice_id}")
    
    # Only support ElevenLabs for now
    if provider != "elevenlabs":
        raise ValidationError(f"Voice preview not supported for provider: {provider}")
    
    if not provider_voice_id:
        raise ValidationError("Voice does not have a provider_voice_id. Cannot generate preview.")
    
    # Get ElevenLabs API key from database first
    api_keys = db.select(
        "api_keys",
        {
            "client_id": current_user["client_id"],
            "service": "elevenlabs",
            "is_active": True
        }
    )
    
    elevenlabs_api_key = None
    if api_keys:
        # Get the most recent active key
        latest_key = sorted(api_keys, key=lambda x: x.get("updated_at", ""), reverse=True)[0]
        encrypted_key = latest_key.get("encrypted_key")
        if encrypted_key:
            elevenlabs_api_key = decrypt_api_key(encrypted_key)
            logger.info("Using ElevenLabs API key from database")
    
    # If no API key in database, try environment variable (for development)
    if not elevenlabs_api_key:
        elevenlabs_api_key = settings.ELEVENLABS_API_KEY
        if elevenlabs_api_key:
            logger.info("Using ElevenLabs API key from environment variable")
    
    # Also try direct os.getenv as fallback (in case settings didn't load it)
    if not elevenlabs_api_key:
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        if elevenlabs_api_key:
            logger.info("Using ElevenLabs API key from os.getenv")
    
    if not elevenlabs_api_key:
        logger.error(f"ElevenLabs API key not found. Provider voice ID: {provider_voice_id}, Voice ID: {voice_id}")
        raise ValidationError(
            "ElevenLabs API key not found. Please configure your API key by either: "
            "1) Setting ELEVENLABS_API_KEY in your backend .env file (in z-backend directory), or "
            "2) Using PATCH /api/v1/providers/tts endpoint with provider='elevenlabs'. "
            "Make sure to restart your backend server after adding the environment variable.",
            {"error": "missing_api_key", "help": "See backend documentation for API key configuration"}
        )
    
    # Log API key info (without exposing the actual key)
    api_key_preview = elevenlabs_api_key[:8] + "..." + elevenlabs_api_key[-4:] if elevenlabs_api_key and len(elevenlabs_api_key) > 12 else "N/A"
    logger.info(f"Using ElevenLabs API key (length: {len(elevenlabs_api_key) if elevenlabs_api_key else 0}, preview: {api_key_preview}) for voice preview")
    
    # Use default text if not provided
    preview_text = text or "Hello, this is a preview of this voice."
    
    # Call ElevenLabs TTS API
    # Don't specify model_id - let ElevenLabs use the default model for the account
    # This avoids issues with deprecated models on free tier
    try:
        logger.info(f"Calling ElevenLabs API for voice ID: {provider_voice_id}, text: {preview_text[:50]}...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{provider_voice_id}",
                headers={
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json",
                    "xi-api-key": elevenlabs_api_key,
                },
                json={
                    "text": preview_text,
                    # Don't specify model_id - uses account default (works with all tiers)
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    }
                },
            )
            logger.info(f"ElevenLabs API response status: {response.status_code}")
            response.raise_for_status()
            
            # Return audio stream
            return StreamingResponse(
                iter([response.content]),
                media_type="audio/mpeg",
                headers={
                    "Content-Disposition": f'inline; filename="voice-preview.mp3"',
                }
            )
    except httpx.HTTPStatusError as e:
        logger.error(f"ElevenLabs API error: {e.response.status_code} - {e.response.text}")
        
        # Try to parse error response for better error messages
        error_detail = None
        try:
            error_json = e.response.json()
            if "detail" in error_json:
                error_detail = error_json["detail"]
                if isinstance(error_detail, dict):
                    error_message = error_detail.get("message", str(error_detail))
                else:
                    error_message = str(error_detail)
            else:
                error_message = e.response.text
        except:
            error_message = e.response.text
        
        if e.response.status_code == 401:
            if "model_deprecated" in error_message.lower() or "free tier" in error_message.lower():
                raise ValidationError(
                    "The ElevenLabs model is not available on your plan. Please upgrade your ElevenLabs subscription or contact support.",
                    {"error": "model_deprecated", "message": error_message}
                )
            raise ValidationError("Invalid ElevenLabs API key", {"error": "invalid_api_key"})
        elif e.response.status_code == 404:
            raise ValidationError("Voice not found in ElevenLabs", {"error": "voice_not_found"})
        else:
            raise ValidationError(f"Failed to generate voice preview: {error_message}", {"error": "api_error"})
    except Exception as e:
        logger.error(f"Error generating voice preview: {e}", exc_info=True)
        raise ValidationError(f"Failed to generate voice preview: {str(e)}", {"error": "internal_error"})

