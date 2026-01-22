"""
Voice Endpoints
"""
from fastapi import APIRouter, Header, Depends, Query, Request, HTTPException, UploadFile, BackgroundTasks
from fastapi.responses import Response
from typing import Optional, List
from datetime import datetime
import uuid
import json
import logging
import httpx
import asyncio

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.storage import generate_presigned_url, check_object_exists
from app.core.exceptions import NotFoundError, ValidationError, PaymentRequiredError, ForbiddenError, ProviderError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.services.ultravox import ultravox_client, elevenlabs_client
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


# Presign endpoint removed - using direct multipart upload instead


async def _process_voice_cloning(
    voice_id: str,
    name: str,
    audio_files: List[bytes],
    user_id: str,
    client_id: str,
    client: dict,
    db: DatabaseService,
):
    """Background task to process voice cloning with ElevenLabs and Ultravox"""
    try:
        logger.info(f"[VOICES] [BACKGROUND] Starting voice clone processing | voice_id={voice_id} | name={name}")
        
        # Send directly to ElevenLabs
        logger.info(f"[VOICES] [BACKGROUND] Starting ElevenLabs voice clone | voice_id={voice_id} | files_count={len(audio_files)}")
        elevenlabs_response = await elevenlabs_client.clone_voice(
            name=name,
            audio_files=audio_files,
            description=f"Voice clone for user {user_id}",
        )
        elevenlabs_voice_id = elevenlabs_response.get("voice_id")
        logger.info(f"[VOICES] [BACKGROUND] ElevenLabs clone completed | voice_id={voice_id} | elevenlabs_voice_id={elevenlabs_voice_id}")
        
        if not elevenlabs_voice_id:
            raise ProviderError(
                provider="elevenlabs",
                message="ElevenLabs response missing voice_id",
                http_status=500,
            )
        
        # Import to Ultravox
        logger.info(f"[VOICES] [BACKGROUND] Starting Ultravox import | voice_id={voice_id} | elevenlabs_voice_id={elevenlabs_voice_id}")
        ultravox_response = await ultravox_client.import_voice_from_provider(
            name=name,
            provider="elevenlabs",
            provider_voice_id=elevenlabs_voice_id,
            description=f"Cloned voice: {name}",
        )
        ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
        logger.info(f"[VOICES] [BACKGROUND] Ultravox import completed | voice_id={voice_id} | ultravox_voice_id={ultravox_voice_id}")
        
        if not ultravox_voice_id:
            raise ProviderError(
                provider="ultravox",
                message="Ultravox response missing voiceId",
                http_status=500,
            )
        
        # Update database record
        update_data = {
            "status": "active",
            "provider_voice_id": elevenlabs_voice_id,
            "ultravox_voice_id": ultravox_voice_id,
            "updated_at": datetime.utcnow().isoformat(),
        }
        db.update("voices", {"id": voice_id}, update_data)
        logger.info(f"[VOICES] [BACKGROUND] Database updated | voice_id={voice_id} | status=active")
        
        # Deduct credits
        db.update("clients", {"id": client_id}, {
            "credits_balance": client.get("credits_balance", 0) - 50,
            "updated_at": datetime.utcnow().isoformat(),
        })
        logger.info(f"[VOICES] [BACKGROUND] Credits deducted | voice_id={voice_id} | client_id={client_id} | amount=50")
        logger.info(f"[VOICES] [BACKGROUND] Voice cloning completed successfully | voice_id={voice_id} | name={name}")
        
    except ProviderError as e:
        logger.error(f"[VOICES] [BACKGROUND] Provider error | voice_id={voice_id} | error={str(e)}", exc_info=True)
        db.update("voices", {"id": voice_id}, {
            "status": "failed",
            "updated_at": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logger.error(f"[VOICES] [BACKGROUND] Unexpected error | voice_id={voice_id} | error={str(e)}", exc_info=True)
        db.update("voices", {"id": voice_id}, {
            "status": "failed",
            "updated_at": datetime.utcnow().isoformat(),
        })


@router.post("")
async def create_voice(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """
    Create voice - SIMPLIFIED: Direct multipart upload
    
    FormData (multipart/form-data):
    - name: Voice name (required)
    - strategy: "native" (voice clone) or "external" (import)
    - provider: "elevenlabs" (default)
    - files: Audio files (WAV, MP3, WebM, OGG) - required for native
    - provider_voice_id: Provider voice ID - required for external
    
    Legacy JSON support maintained for backward compatibility.
    """
    client_id = current_user.get("client_id")
    user_id = current_user.get("user_id")
    
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Determine if this is multipart (new) or JSON (legacy)
    content_type = request.headers.get("content-type", "")
    is_multipart = "multipart/form-data" in content_type
    
    if is_multipart:
        # Parse multipart form data
        form = await request.form()
        name = form.get("name")
        strategy = form.get("strategy")
        provider = form.get("provider", "elevenlabs")
        provider_voice_id = form.get("provider_voice_id")
        files = form.getlist("files")
        # NEW: Multipart form-data approach
        if not name:
            raise ValidationError("Voice name is required")
        if not strategy:
            raise ValidationError("Strategy is required (native or external)")
        
        # NATIVE (Voice Clone): Process files directly
        if strategy == "native":
            if not files or len(files) == 0:
                raise ValidationError("At least one audio file is required for voice cloning")
            
            if len(files) > 10:
                raise ValidationError("Maximum 10 files allowed")
            
            # Validate requirements
            if not settings.ELEVENLABS_API_KEY:
                raise ValidationError("ElevenLabs API key is not configured. Voice cloning requires ElevenLabs.")
            if not settings.ULTRAVOX_API_KEY:
                raise ValidationError("Ultravox API key is not configured. Voice cloning requires Ultravox.")
            
            # Credit check
            client = db.get_client(client_id)
            if not client or client.get("credits_balance", 0) < 50:
                raise PaymentRequiredError(
                    "Insufficient credits for voice cloning. Required: 50",
                    {"required": 50, "available": client.get("credits_balance", 0) if client else 0},
                )
            
            # Process files - read quickly and validate
            audio_files = []
            total_size = 0
            for file_item in files:
                # file_item is a UploadFile from form
                if not isinstance(file_item, UploadFile):
                    continue
                
                # Quick validation first (before reading)
                filename = file_item.filename or ""
                content_type = file_item.content_type or ""
                valid_extensions = ['.wav', '.mp3', '.mpeg', '.webm', '.ogg', '.m4a', '.aac', '.flac']
                
                if not content_type.startswith('audio/') and not any(filename.lower().endswith(ext) for ext in valid_extensions):
                    raise ValidationError(f"Invalid file type: {filename}. Only audio files are allowed.")
                
                # Read file content (this is necessary before passing to background task)
                # FastAPI streams this efficiently, but for large files it can take a moment
                content = await file_item.read()
                
                # Validate size (10MB max per file)
                file_size = len(content)
                if file_size > 10 * 1024 * 1024:
                    raise ValidationError(f"File {filename} exceeds 10MB limit")
                
                total_size += file_size
                audio_files.append(content)
            
            # Log file processing
            logger.info(f"[VOICES] [CREATE] Files processed | count={len(audio_files)} | total_size={total_size} bytes")
            
            # Create voice record
            voice_id = str(uuid.uuid4())
            now = datetime.utcnow()
            
            voice_db_record = {
                "id": voice_id,
                "client_id": client_id,
                "user_id": user_id,
                "name": name,
                "provider": provider or "elevenlabs",
                "type": "custom",
                "language": "en-US",
                "status": "creating",
                "training_info": {
                    "progress": 0,
                    "started_at": now.isoformat(),
                },
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            
            db.insert("voices", voice_db_record)
            logger.info(f"[VOICES] [CREATE] Voice record created | voice_id={voice_id} | name={name} | strategy=native")
            
            # Add background task to process cloning (returns immediately)
            background_tasks.add_task(
                _process_voice_cloning,
                voice_id=voice_id,
                name=name,
                audio_files=audio_files,
                user_id=user_id,
                client_id=client_id,
                client=client,
                db=db,
            )
            
            # Return immediately with "creating" status
            response_data = {
                "data": VoiceResponse(**voice_db_record),
                "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
            }
            
            logger.info(f"[VOICES] [CREATE] Voice creation initiated (processing in background) | voice_id={voice_id} | name={name}")
            return response_data
        
        # EXTERNAL (Import existing voice)
        else:
            if not provider_voice_id:
                raise ValidationError("Provider voice ID is required for external import")
            
            if not settings.ULTRAVOX_API_KEY:
                raise ValidationError("Ultravox API key is not configured.")
            
            # Create voice record
            voice_id = str(uuid.uuid4())
            now = datetime.utcnow()
            
            voice_db_record = {
                "id": voice_id,
                "client_id": client_id,
                "user_id": user_id,
                "name": name,
                "provider": provider or "elevenlabs",
                "type": "reference",
                "language": "en-US",
                "status": "creating",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            
            db.insert("voices", voice_db_record)
            
            try:
                ultravox_response = await ultravox_client.import_voice_from_provider(
                    name=name,
                    provider=provider or "elevenlabs",
                    provider_voice_id=provider_voice_id,
                    description=f"Imported voice: {name}",
                )
                ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
                
                if not ultravox_voice_id:
                    raise ProviderError(
                        provider="ultravox",
                        message="Ultravox response missing voiceId",
                        http_status=500,
                    )
                
                update_data = {
                    "status": "active",
                    "provider_voice_id": provider_voice_id,
                    "ultravox_voice_id": ultravox_voice_id,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                db.update("voices", {"id": voice_id}, update_data)
                voice_db_record.update(update_data)
                
                response_data = {
                    "data": VoiceResponse(**voice_db_record),
                    "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
                }
                
                return response_data
                
            except Exception as e:
                db.delete("voices", {"id": voice_id})
                raise
    
    else:
        # LEGACY: JSON approach (backward compatibility)
        try:
            body = await request.json()
            voice_data = VoiceCreate(**body)
        except Exception as e:
            raise ValidationError(f"Invalid request body: {str(e)}")
        
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
        
        # Determine provider
        provider = voice_data.provider_overrides.get("provider", "elevenlabs") if voice_data.provider_overrides else "elevenlabs"
        
        # Check for duplicate voice
        if voice_data.strategy != "native" and voice_data.source.provider_voice_id:
            existing_voices = db.select(
                "voices",
                {
                    "client_id": client_id,
                    "user_id": user_id,
                    "provider_voice_id": voice_data.source.provider_voice_id,
                    "provider": provider,
                }
            )
            if existing_voices and len(existing_voices) > 0:
                existing_voice = existing_voices[0]
                return {
                    "data": VoiceResponse(**existing_voice),
                    "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
                }
        
        # Credit check for native training
        client = None
        if voice_data.strategy == "native":
            client = db.get_client(client_id)
            if not client or client.get("credits_balance", 0) < 50:
                raise PaymentRequiredError(
                    "Insufficient credits for voice cloning. Required: 50",
                    {"required": 50, "available": client.get("credits_balance", 0) if client else 0},
                )
        
        # ATOMIC RESOURCE CREATION
        voice_id = str(uuid.uuid4())
        now = datetime.utcnow()
        voice_type = "custom" if voice_data.strategy == "native" else "reference"
        
        # Initialize tracking variables
        elevenlabs_voice_id = None
        ultravox_voice_id = None
        
        # Prepare voice record for database
        voice_db_record = {
            "id": voice_id,
            "client_id": client_id,
            "user_id": user_id,
            "name": voice_data.name,
            "provider": provider,
            "type": voice_type,
            "language": "en-US",
            "status": "creating",
            "training_info": {
                "progress": 0,
                "started_at": now.isoformat(),
            } if voice_data.strategy == "native" else {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # Insert temporary record
        db.insert("voices", voice_db_record)
        
        try:
            # NATIVE (Voice Clone): Clone in ElevenLabs → Import to Ultravox
            if voice_data.strategy == "native":
                
                # Validate requirements
                if not settings.ELEVENLABS_API_KEY:
                    raise ValidationError("ElevenLabs API key is not configured. Voice cloning requires ElevenLabs.")
                if not settings.ULTRAVOX_API_KEY:
                    raise ValidationError("Ultravox API key is not configured. Voice cloning requires Ultravox.")
                if not voice_data.source.samples or len(voice_data.source.samples) == 0:
                    raise ValidationError("At least one audio sample is required for voice cloning.")
                
                # Step 1: Download audio files from storage (legacy path)
                audio_files = []
                for sample in voice_data.source.samples:
                    if not check_object_exists(settings.STORAGE_BUCKET_UPLOADS, sample.storage_key):
                        raise NotFoundError("voice sample", sample.storage_key)
                    
                    # Generate presigned URL and download the file
                    audio_url = generate_presigned_url(
                        bucket=settings.STORAGE_BUCKET_UPLOADS,
                        key=sample.storage_key,
                        operation="get_object",
                        expires_in=3600,
                    )
                    
                    # Download the audio file
                    async with httpx.AsyncClient(timeout=60.0) as http_client:
                        response = await http_client.get(audio_url)
                        response.raise_for_status()
                        audio_files.append(response.content)
                
                elevenlabs_response = await elevenlabs_client.clone_voice(
                    name=voice_data.name,
                    audio_files=audio_files,
                    description=f"Voice clone for user {user_id}",
                )
                elevenlabs_voice_id = elevenlabs_response.get("voice_id")
                
                if not elevenlabs_voice_id:
                    raise ProviderError(
                        provider="elevenlabs",
                        message="ElevenLabs response missing voice_id",
                        http_status=500,
                    )
                
                ultravox_response = await ultravox_client.import_voice_from_provider(
                    name=voice_data.name,
                    provider="elevenlabs",
                    provider_voice_id=elevenlabs_voice_id,
                    description=f"Cloned voice: {voice_data.name}",
                )
                ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
                
                if not ultravox_voice_id:
                    raise ProviderError(
                        provider="ultravox",
                        message="Ultravox response missing voiceId",
                        http_status=500,
                    )
                
                # Update database record
                update_data = {
                    "status": "active",  # Voice cloning is complete
                    "provider_voice_id": elevenlabs_voice_id,
                    "ultravox_voice_id": ultravox_voice_id,
                    "updated_at": now.isoformat(),
                }
                db.update("voices", {"id": voice_id}, update_data)
                voice_db_record.update(update_data)
            
            # EXTERNAL (Import existing voice): Import to Ultravox
            else:
                if not settings.ULTRAVOX_API_KEY:
                    raise ValidationError("Ultravox API key is not configured.")
                
                provider_voice_id = voice_data.source.provider_voice_id
                if not provider_voice_id:
                    raise ValidationError(f"Provider voice ID is required for {provider} import.")
                
                # Import to Ultravox with correct provider-specific definition
                ultravox_response = await ultravox_client.import_voice_from_provider(
                    name=voice_data.name,
                    provider=provider,
                    provider_voice_id=provider_voice_id,
                    description=f"Imported {provider} voice: {voice_data.name}",
                )
                ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
                
                if not ultravox_voice_id:
                    raise ProviderError(
                        provider="ultravox",
                        message="Ultravox response missing voiceId",
                        http_status=500,
                    )
                
                # Update database record
                update_data = {
                    "status": "active",
                    "provider_voice_id": provider_voice_id,
                    "ultravox_voice_id": ultravox_voice_id,
                    "updated_at": now.isoformat(),
                }
                db.update("voices", {"id": voice_id}, update_data)
                voice_db_record.update(update_data)
        
        except Exception as e:
            # Rollback: Delete temporary record
            db.delete("voices", {"id": voice_id, "client_id": client_id})
            
            # Clean up ElevenLabs voice if created
            if elevenlabs_voice_id:
                try:
                    await elevenlabs_client.delete_voice(elevenlabs_voice_id)
                except Exception:
                    pass  # Ignore cleanup errors
            
            # Re-raise appropriate error
            if isinstance(e, (ValidationError, NotFoundError, PaymentRequiredError, ProviderError)):
                raise
            logger.error(f"[VOICES] [CREATE] Failed to create voice: {e}", exc_info=True)
            raise ProviderError(provider=provider, message=str(e), http_status=500)
        
        # Prepare response record
        voice_record = voice_db_record.copy()
        voice_record["created_at"] = now
        voice_record["updated_at"] = now
        
        # Debit credits for native voice cloning
        if voice_data.strategy == "native" and client:
            db.insert(
                "credit_transactions",
                {
                    "client_id": client_id,
                    "type": "spent",
                    "amount": 50,
                    "reference_type": "voice_cloning",
                    "reference_id": voice_id,
                    "description": f"Voice cloning: {voice_data.name}",
                },
            )
            db.update(
                "clients",
                {"id": client_id},
                {"credits_balance": client["credits_balance"] - 50},
            )
        
        response_data = {
            "data": VoiceResponse(**voice_record),
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
        }
        
        # Store idempotency response
        if idempotency_key:
            body_dict = voice_data.dict() if hasattr(voice_data, 'dict') else json.loads(json.dumps(voice_data, default=str))
            await store_idempotency_response(
                client_id,
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
    ownership: Optional[str] = Query("public", description="Filter by ownership: 'public' or 'private'"),
    source: Optional[str] = Query(None, description="Filter by source: 'ultravox' (pre-loaded voices) or 'custom' (user-created voices)"),
):
    """
    List voices - simplified: returns directly from source without DB syncing
    - source='custom': Returns custom voices from database
    - source='ultravox' or not provided: Returns voices directly from Ultravox API
    """
    client_id = current_user.get("client_id")
    now = datetime.utcnow()
    
    try:
        # Custom voices: return directly from database
        if source == "custom":
            db = DatabaseService(current_user["token"])
            db.set_auth(current_user["token"])
            
            custom_voices = db.select(
                "voices",
                {"client_id": client_id, "type": "custom"},
                order_by="created_at"
            )
            
            voices_data = []
            for voice_record in custom_voices:
                try:
                    # Parse datetime fields
                    created_at = voice_record.get("created_at")
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            created_at = now
                    elif created_at is None:
                        created_at = now
                    
                    updated_at = voice_record.get("updated_at")
                    if isinstance(updated_at, str):
                        try:
                            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            updated_at = now
                    elif updated_at is None:
                        updated_at = now
                    
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
            
            return {
                "data": voices_data,
                "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
            }
        
        # Ultravox voices: return directly from Ultravox API (no DB operations)
        else:
            if not settings.ULTRAVOX_API_KEY:
                raise ValidationError("Ultravox API key not configured")
            
            ultravox_voices = await ultravox_client.list_voices(ownership=ownership)
            
            # Convert Ultravox format to our VoiceResponse format
            voices_data = []
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
                    
                    # Skip if no provider_voice_id
                    if not provider_voice_id:
                        continue
                    
                    ultravox_voice_id = uv_voice.get("voiceId")
                    if not ultravox_voice_id:
                        continue
                    
                    # Map to VoiceResponse format (use ultravox_voice_id as id for simplicity)
                    voice_data = {
                        "id": ultravox_voice_id,  # Use Ultravox ID directly
                        "client_id": client_id,
                        "name": uv_voice.get("name", "Untitled Voice"),
                        "provider": provider_name,
                        "type": "reference",
                        "language": uv_voice.get("primaryLanguage", "en-US") or "en-US",
                        "status": "active",
                        "provider_voice_id": provider_voice_id,
                        "ultravox_voice_id": ultravox_voice_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                    
                    if uv_voice.get("description"):
                        voice_data["description"] = uv_voice.get("description")
                    
                    voices_data.append(VoiceResponse(**voice_data))
                except Exception as e:
                    logger.warning(f"[VOICES] [LIST] Failed to process voice {uv_voice.get('voiceId')}: {e}")
                    continue
            
            return {
                "data": voices_data,
                "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
            }
    
    except ProviderError as e:
        logger.error(f"[VOICES] [LIST] Ultravox API Error: {e}")
        return {"data": [], "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now)}
    except Exception as e:
        logger.error(f"[VOICES] [LIST] Error: {e}", exc_info=True)
        return {"data": [], "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now)}


@router.post("/sync-from-ultravox")
async def sync_voices_from_ultravox(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    ownership: Optional[str] = Query("public", description="Filter by ownership: 'public' or 'private'"),
    provider: Optional[List[str]] = Query(None, description="Filter by provider (e.g., 'eleven_labs', 'cartesia', 'lmnt', 'google')"),
):
    """
    Sync pre-loaded voices from Ultravox into the local database.
    This imports public voices (pre-loaded voices) that exist in Ultravox but not in your database.
    """
    client_id = current_user.get("client_id")
    
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key not configured. Please set ULTRAVOX_API_KEY environment variable.")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    try:
        # Fetch voices from Ultravox
        ultravox_voices = await ultravox_client.list_voices(ownership=ownership, provider=provider)
        
        imported_count = 0
        skipped_count = 0
        errors = []
        skipped_reasons = {"no_provider_id": 0, "already_exists": 0}
        
        for uv_voice in ultravox_voices:
            try:
                ultravox_voice_id = uv_voice.get("voiceId")
                provider_voice_id = uv_voice.get("provider_voice_id")
                provider_name = uv_voice.get("provider", "elevenlabs")
                
                # Skip if no provider_voice_id
                if not provider_voice_id:
                    skipped_count += 1
                    skipped_reasons["no_provider_id"] += 1
                    continue
                
                # Check if voice already exists
                existing_by_ultravox = db.select_one(
                    "voices",
                    {"client_id": current_user["client_id"], "ultravox_voice_id": ultravox_voice_id}
                )
                
                existing_by_provider = None
                if provider_voice_id:
                    existing_by_provider = db.select_one(
                        "voices",
                        {"client_id": current_user["client_id"], "provider_voice_id": provider_voice_id, "provider": provider_name}
                    )
                
                if existing_by_ultravox or existing_by_provider:
                    skipped_count += 1
                    skipped_reasons["already_exists"] += 1
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
            except Exception as e:
                logger.warning(f"[VOICES] [SYNC] Failed to import voice {uv_voice.get('voiceId')}: {e}")
                errors.append({
                    "voice_id": uv_voice.get("voiceId"),
                    "name": uv_voice.get("name"),
                    "error": str(e),
                })
        
        return {
            "data": {
                "imported": imported_count,
                "skipped": skipped_count,
                "total_fetched": len(ultravox_voices),
                "skipped_reasons": skipped_reasons,
                "errors": errors if errors else None,
            },
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
        }
    except Exception as e:
        logger.error(f"[VOICES] [SYNC] Failed to sync voices: {e}", exc_info=True)
        raise ValidationError(f"Failed to sync voices from Ultravox: {str(e)}")


@router.get("/{voice_id}")
async def get_voice(
    voice_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single voice - returns what is in the DB immediately."""
    try:
        db = DatabaseService(current_user["token"])
        db.set_auth(current_user["token"])
        
        voice = db.get_voice(voice_id, current_user["client_id"])
        if not voice:
            raise NotFoundError("voice", voice_id)
        
        return {
            "data": VoiceResponse(**voice),
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
        }
    except NotFoundError:
        raise
    except Exception as e:
        logger.error(f"[VOICES] [GET] Failed to get voice {voice_id}: {e}", exc_info=True)
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
    request: Request,
    text: Optional[str] = Query(None, description="Text to convert to speech for preview (not used with Ultravox preview)"),
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
        
        # Return audio response (Ultravox returns audio/wav)
        return Response(
            content=audio_bytes,
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

