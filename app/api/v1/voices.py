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

from app.core.auth import get_current_user
from app.core.database import DatabaseService, DatabaseAdminService
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


@router.post("/clone")
async def clone_voice(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    DEPRECATED: Use POST /voices with strategy="native" instead.
    This endpoint is kept for backward compatibility only.
    
    Muscular voice cloning endpoint: File Upload → ElevenLabs Clone → Ultravox Sync → Supabase Record
    
    FormData (multipart/form-data):
    - name: Voice name (required)
    - file: Audio file (MP3, WAV, etc.) - required
    
    Returns immediately with voice record. Processing happens in background.
    """
    logger.warning(f"[VOICES] [CLONE] Deprecated endpoint used | client_id={current_user.get('client_id')} | user_id={current_user.get('user_id')}. Please use POST /voices with strategy='native' instead.")
    client_id = current_user.get("client_id")
    user_id = current_user.get("user_id")
    
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Validate API keys
    if not settings.ELEVENLABS_API_KEY:
        raise ValidationError("ElevenLabs API key is not configured")
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key is not configured")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Parse multipart form data
    form = await request.form()
    name = form.get("name")
    file_item = form.get("file")
    
    if not name:
        raise ValidationError("Voice name is required")
    if not file_item or not isinstance(file_item, UploadFile):
        raise ValidationError("Audio file is required")
    
    # Validate file
    filename = file_item.filename or ""
    content_type = file_item.content_type or ""
    valid_extensions = ['.wav', '.mp3', '.mpeg', '.webm', '.ogg', '.m4a', '.aac', '.flac']
    
    if not content_type.startswith('audio/') and not any(filename.lower().endswith(ext) for ext in valid_extensions):
        raise ValidationError(f"Invalid file type: {filename}. Only audio files are allowed.")
    
    # Read file content
    audio_content = await file_item.read()
    file_size = len(audio_content)
    
    if file_size > 10 * 1024 * 1024:
        raise ValidationError(f"File {filename} exceeds 10MB limit")
    
    if file_size == 0:
        raise ValidationError("File is empty")
    
    # Credit check
    client = db.get_client(client_id)
    if not client or client.get("credits_balance", 0) < 50:
        raise PaymentRequiredError(
            "Insufficient credits for voice cloning. Required: 50",
            {"required": 50, "available": client.get("credits_balance", 0) if client else 0},
        )
    
    # Create voice record first
    voice_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    voice_db_record = {
        "id": voice_id,
        "client_id": client_id,
        "user_id": user_id,
        "name": name,
        "provider": "elevenlabs",
        "type": "custom",
        "language": "en-US",
        "status": "training",
        "training_info": {
            "progress": 0,
            "message": "Voice cloning queued. Processing will begin shortly...",
            "started_at": now.isoformat(),
        },
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    
    db.insert("voices", voice_db_record)
    logger.info(f"[VOICES] [CLONE] Voice record created | voice_id={voice_id} | name={name}")
    
    # Process cloning in background using unified function - return immediately
    background_tasks.add_task(
        _process_voice_cloning_unified,
        voice_id=voice_id,
        name=name,
        audio_files=[audio_content],
        filenames=[filename],
        content_types=[content_type],
        user_id=user_id,
        client_id=client_id,
    )
    
    logger.info(f"[VOICES] [CLONE] Voice cloning queued for background processing | voice_id={voice_id} | name={name}")
    
    # Return immediately with "training" status
    return {
        "data": VoiceResponse(**voice_db_record),
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
    }


async def _process_voice_cloning_unified(
    voice_id: str,
    name: str,
    audio_files: List[bytes],
    filenames: List[str],
    content_types: List[str],
    user_id: str,
    client_id: str,
):
    """
    Unified background task to process voice cloning with ElevenLabs and Ultravox.
    Handles both single file (from /voices/clone) and multiple files (from /voices).
    Uses DatabaseAdminService to bypass RLS since we don't have user token in background.
    """
    # Hardened implementation with broad try/except to prevent stuck loading state
    logger.info(f"[VOICES] [BACKGROUND] Task started | voice_id={voice_id} | name={name}")
    
    db = None
    
    try:
        from app.core.database import DatabaseAdminService
        
        db = DatabaseAdminService()
        elevenlabs_voice_id = None
    
        logger.info(f"[VOICES] [BACKGROUND] Starting unified voice clone processing | voice_id={voice_id} | name={name} | files_count={len(audio_files)}")
        
        # Update status to training
        db.update("voices", {"id": voice_id}, {
            "status": "training",
            "training_info": {
                "progress": 10,
                "message": "Starting ElevenLabs voice cloning...",
                "updated_at": datetime.utcnow().isoformat(),
            },
            "updated_at": datetime.utcnow().isoformat(),
        })
        
        # Step 1: Clone voice in ElevenLabs
        logger.info(f"[VOICES] [BACKGROUND] Starting ElevenLabs clone | voice_id={voice_id} | files_count={len(audio_files)}")
        elevenlabs_url = "https://api.elevenlabs.io/v1/voices/add"
        
        async with httpx.AsyncClient(timeout=120.0) as http_client:
            # Build multipart form data for ElevenLabs
            files = []
            for i, (audio_content, filename, content_type) in enumerate(zip(audio_files, filenames, content_types)):
                files.append(("files", (filename or f"sample_{i}.mp3", audio_content, content_type or "audio/mpeg")))
            
            data = {"name": name}
            
            elevenlabs_response = await http_client.post(
                elevenlabs_url,
                headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
                data=data,
                files=files,
            )
            
            if elevenlabs_response.status_code >= 400:
                error_text = elevenlabs_response.text[:500] if elevenlabs_response.text else "No response body"
                import json
                error_details_raw = {
                    "status_code": elevenlabs_response.status_code,
                    "status_text": elevenlabs_response.reason_phrase if hasattr(elevenlabs_response, 'reason_phrase') else None,
                    "error_text": error_text,
                    "full_response_text": elevenlabs_response.text if elevenlabs_response.text else None,
                    "response_headers": dict(elevenlabs_response.headers) if hasattr(elevenlabs_response, 'headers') else None,
                    "request_url": elevenlabs_url,
                    "request_method": "POST",
                    "voice_id": voice_id,
                    "name": name,
                    "files_count": len(audio_files),
                }
                logger.error(f"[VOICES] [BACKGROUND] ElevenLabs error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                raise ProviderError(
                    provider="elevenlabs",
                    message=f"ElevenLabs voice cloning failed: {error_text}",
                    http_status=elevenlabs_response.status_code,
                )
            
            elevenlabs_data = elevenlabs_response.json()
            elevenlabs_voice_id = elevenlabs_data.get("voice_id")
            
            if not elevenlabs_voice_id:
                raise ProviderError(
                    provider="elevenlabs",
                    message="ElevenLabs response missing voice_id",
                    http_status=500,
                )
        
        logger.info(f"[VOICES] [BACKGROUND] ElevenLabs clone completed | voice_id={voice_id} | elevenlabs_voice_id={elevenlabs_voice_id}")
        
        # Update progress
        db.update("voices", {"id": voice_id}, {
            "training_info": {
                "progress": 50,
                "message": "ElevenLabs cloning completed. Syncing with Ultravox...",
                "updated_at": datetime.utcnow().isoformat(),
            },
            "updated_at": datetime.utcnow().isoformat(),
        })
        
        # Step 2: Import to Ultravox
        logger.info(f"[VOICES] [BACKGROUND] Starting Ultravox sync | voice_id={voice_id} | elevenlabs_voice_id={elevenlabs_voice_id}")
        
        # Normalize name for Ultravox
        normalized_name = name.lower().replace(" ", "_").replace("-", "_")
        normalized_name = "".join(c if c.isalnum() or c == "_" else "" for c in normalized_name)
        
        ultravox_url = f"{settings.ULTRAVOX_BASE_URL.rstrip('/')}/api/voices"
        ultravox_payload = {
            "name": normalized_name,
            "description": f"Cloned voice: {name}",
            "definition": {
                "elevenLabs": {
                    "voiceId": elevenlabs_voice_id,
                    "model": "eleven_multilingual_v2",
                    "stability": 0.5,
                    "similarityBoost": 0.75,
                    "style": 0.0,
                    "useSpeakerBoost": True,
                    "speed": 1.0,
                }
            }
        }
        
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http_client:
            ultravox_response = await http_client.post(
                ultravox_url,
                headers={
                    "X-API-Key": settings.ULTRAVOX_API_KEY,
                    "Content-Type": "application/json",
                },
                json=ultravox_payload,
            )
            
            if ultravox_response.status_code >= 400:
                error_text = ultravox_response.text[:500] if ultravox_response.text else "No response body"
                import json
                error_details_raw = {
                    "status_code": ultravox_response.status_code,
                    "status_text": ultravox_response.reason_phrase if hasattr(ultravox_response, 'reason_phrase') else None,
                    "error_text": error_text,
                    "full_response_text": ultravox_response.text if ultravox_response.text else None,
                    "response_headers": dict(ultravox_response.headers) if hasattr(ultravox_response, 'headers') else None,
                    "request_url": ultravox_url,
                    "request_method": "POST",
                    "request_payload": ultravox_payload,
                    "voice_id": voice_id,
                    "name": name,
                    "elevenlabs_voice_id": elevenlabs_voice_id,
                }
                logger.error(f"[VOICES] [BACKGROUND] Ultravox error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                
                # Clean up ElevenLabs voice on Ultravox failure
                if elevenlabs_voice_id:
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as cleanup_client:
                            await cleanup_client.delete(
                                f"https://api.elevenlabs.io/v1/voices/{elevenlabs_voice_id}",
                                headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
                            )
                            logger.info(f"[VOICES] [BACKGROUND] Cleaned up ElevenLabs voice | elevenlabs_voice_id={elevenlabs_voice_id}")
                    except Exception as cleanup_error:
                        import traceback
                        import json
                        cleanup_error_details = {
                            "error_type": type(cleanup_error).__name__,
                            "error_message": str(cleanup_error),
                            "full_traceback": traceback.format_exc(),
                            "elevenlabs_voice_id": elevenlabs_voice_id,
                        }
                        logger.error(f"[VOICES] [BACKGROUND] Cleanup error (RAW ERROR): {json.dumps(cleanup_error_details, indent=2, default=str)}", exc_info=True)
                
                raise ProviderError(
                    provider="ultravox",
                    message=f"Ultravox sync failed: {error_text}",
                    http_status=ultravox_response.status_code,
                )
            
            ultravox_data = ultravox_response.json()
            ultravox_voice_id = ultravox_data.get("voiceId") or ultravox_data.get("id")
            
            if not ultravox_voice_id:
                raise ProviderError(
                    provider="ultravox",
                    message="Ultravox response missing voiceId",
                    http_status=500,
                )
        
        logger.info(f"[VOICES] [BACKGROUND] Ultravox sync completed | voice_id={voice_id} | ultravox_voice_id={ultravox_voice_id}")
        
        # Step 3: Update database record
        update_data = {
            "status": "active",
            "provider_voice_id": elevenlabs_voice_id,
            "ultravox_voice_id": ultravox_voice_id,
            "training_info": {
                "progress": 100,
                "message": "Voice cloning completed successfully",
                "completed_at": datetime.utcnow().isoformat(),
            },
            "updated_at": datetime.utcnow().isoformat(),
        }
        db.update("voices", {"id": voice_id}, update_data)
        logger.info(f"[VOICES] [BACKGROUND] Database updated | voice_id={voice_id} | status=active")
        
        # Step 4: Deduct credits (get fresh client data)
        current_client = db.select_one("clients", {"id": client_id})
        if current_client:
            # Log credit transaction
            try:
                db.insert(
                    "credit_transactions",
                    {
                        "client_id": client_id,
                        "type": "spent",
                        "amount": 50,
                        "reference_type": "voice_cloning",
                        "reference_id": voice_id,
                        "description": f"Voice cloning: {name}",
                    },
                )
            except Exception as tx_error:
                logger.warning(f"[VOICES] [BACKGROUND] Failed to log credit transaction (non-critical): {str(tx_error)}")
            
            # Deduct credits
            db.update("clients", {"id": client_id}, {
                "credits_balance": current_client.get("credits_balance", 0) - 50,
                "updated_at": datetime.utcnow().isoformat(),
            })
            logger.info(f"[VOICES] [BACKGROUND] Credits deducted | voice_id={voice_id} | client_id={client_id} | amount=50")
        else:
            logger.warning(f"[VOICES] [BACKGROUND] Client not found for credit deduction | client_id={client_id}")
        
        logger.info(f"[VOICES] [BACKGROUND] Voice cloning completed successfully | voice_id={voice_id} | name={name}")
        
    except Exception as e:
        # Catch-all for any error in the background task to ensure status is updated
        import traceback
        import json
        
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
            "voice_id": voice_id,
        }
        logger.error(f"[VOICES] [BACKGROUND] Critical failure in background task (RAW ERROR): {json.dumps(error_details, indent=2, default=str)}", exc_info=True)
        
        # Try to update status to failed if DB connection is available
        if db:
            try:
                db.update("voices", {"id": voice_id}, {
                    "status": "failed",
                    "training_info": {
                        "progress": 0,
                        "message": f"Critical error: {str(e)}",
                        "error_at": datetime.utcnow().isoformat(),
                    },
                    "updated_at": datetime.utcnow().isoformat(),
                })
                logger.info(f"[VOICES] [BACKGROUND] Updated voice status to failed | voice_id={voice_id}")
            except Exception as db_error:
                logger.error(f"[VOICES] [BACKGROUND] Failed to update voice status to failed: {str(db_error)}", exc_info=True)


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
                "status": "training",
                "training_info": {
                    "progress": 0,
                    "message": "Voice cloning queued. Processing will begin shortly...",
                    "started_at": now.isoformat(),
                },
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            
            db.insert("voices", voice_db_record)
            logger.info(f"[VOICES] [CREATE] Voice record created | voice_id={voice_id} | name={name} | strategy=native")
            
            # Add background task to process cloning (returns immediately)
            # Build filenames and content_types lists for unified function
            filenames = []
            content_types_list = []
            for file_item in files:
                if isinstance(file_item, UploadFile):
                    filenames.append(file_item.filename or "audio.mp3")
                    content_types_list.append(file_item.content_type or "audio/mpeg")
            
            background_tasks.add_task(
                _process_voice_cloning_unified,
                voice_id=voice_id,
                name=name,
                audio_files=audio_files,
                filenames=filenames,
                content_types=content_types_list,
                user_id=user_id,
                client_id=client_id,
            )
            
            # Return immediately with "training" status
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
                "status": "active",
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
                import traceback
                import json
                error_details_raw = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "error_args": e.args if hasattr(e, 'args') else None,
                    "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                    "full_traceback": traceback.format_exc(),
                    "voice_id": voice_id,
                    "name": name,
                    "strategy": strategy,
                }
                logger.error(f"[VOICES] [CREATE] Error in native voice creation (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                db.delete("voices", {"id": voice_id})
                raise
    
    else:
        # LEGACY: JSON approach (backward compatibility)
        try:
            body = await request.json()
            logger.info(f"[VOICES] [CREATE] Received JSON body | body_keys={list(body.keys()) if isinstance(body, dict) else 'not_dict'}")
            voice_data = VoiceCreate(**body)
            logger.info(f"[VOICES] [CREATE] VoiceCreate schema validated | strategy={voice_data.strategy} | name={voice_data.name}")
        except Exception as e:
            import traceback
            import json
            # Try to get the raw body for debugging
            try:
                raw_body = await request.body()
                body_text = raw_body.decode('utf-8') if raw_body else "empty"
            except:
                body_text = "could_not_read_body"
            
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "content_type": content_type,
                "raw_body": body_text[:1000] if len(body_text) > 1000 else body_text,
            }
            logger.error(f"[VOICES] [CREATE] Invalid request body (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
            "status": "training" if voice_data.strategy == "native" else "active",
            "training_info": {
                "progress": 0,
                "message": "Voice cloning queued. Processing will begin shortly...",
                "started_at": now.isoformat(),
            } if voice_data.strategy == "native" else {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        # Insert temporary record
        db.insert("voices", voice_db_record)
        
        try:
            # NATIVE (Voice Clone): Process in background (legacy JSON path - now uses background processing)
            if voice_data.strategy == "native":
                
                # Validate requirements
                if not settings.ELEVENLABS_API_KEY:
                    raise ValidationError("ElevenLabs API key is not configured. Voice cloning requires ElevenLabs.")
                if not settings.ULTRAVOX_API_KEY:
                    raise ValidationError("Ultravox API key is not configured. Voice cloning requires Ultravox.")
                if not voice_data.source.samples or len(voice_data.source.samples) == 0:
                    raise ValidationError("At least one audio sample is required for voice cloning.")
                
                # Download audio files from storage for background processing
                audio_files = []
                filenames = []
                content_types = []
                
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
                        # Extract filename from storage key or use default
                        filename = sample.storage_key.split('/')[-1] if '/' in sample.storage_key else sample.storage_key
                        filenames.append(filename)
                        # Try to infer content type from filename
                        content_type = "audio/mpeg"
                        if filename.lower().endswith('.wav'):
                            content_type = "audio/wav"
                        elif filename.lower().endswith('.webm'):
                            content_type = "audio/webm"
                        elif filename.lower().endswith('.ogg'):
                            content_type = "audio/ogg"
                        content_types.append(content_type)
                
                # Process cloning in background - return immediately with "training" status
                background_tasks.add_task(
                    _process_voice_cloning_unified,
                    voice_id=voice_id,
                    name=voice_data.name,
                    audio_files=audio_files,
                    filenames=filenames,
                    content_types=content_types,
                    user_id=user_id,
                    client_id=client_id,
                )
                
                logger.info(f"[VOICES] [CREATE] Voice creation initiated (processing in background) | voice_id={voice_id} | name={voice_data.name} | strategy=native (JSON path)")
            
            # EXTERNAL (Import existing voice): Import to Ultravox
            else:
                if not settings.ULTRAVOX_API_KEY:
                    raise ValidationError("Ultravox API key is not configured.")
                
                provider_voice_id = voice_data.source.provider_voice_id
                if not provider_voice_id:
                    raise ValidationError(f"Provider voice ID is required for {provider} import.")
                
                logger.info(f"[VOICES] [CREATE] Importing voice from provider | provider={provider} | provider_voice_id={provider_voice_id} | name={name}")
                
                # Import to Ultravox with correct provider-specific definition
                try:
                    ultravox_response = await ultravox_client.import_voice_from_provider(
                        name=voice_data.name,
                        provider=provider,
                        provider_voice_id=provider_voice_id,
                        description=f"Imported {provider} voice: {voice_data.name}",
                    )
                except ProviderError as pe:
                    logger.error(f"[VOICES] [CREATE] ProviderError during import: {str(pe)}", exc_info=True)
                    # Re-raise with original status code if available
                    raise pe
                except Exception as import_error:
                    logger.error(f"[VOICES] [CREATE] Unexpected error during import: {str(import_error)}", exc_info=True)
                    raise ProviderError(
                        provider="ultravox",
                        message=f"Failed to import voice from Ultravox: {str(import_error)}",
                        http_status=502, # Bad Gateway / Upstream error
                        details={"original_error": str(import_error)}
                    )

                ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
                
                if not ultravox_voice_id:
                    logger.error(f"[VOICES] [CREATE] Import response missing voiceId: {ultravox_response}")
                    raise ProviderError(
                        provider="ultravox",
                        message="Ultravox response missing voiceId",
                        http_status=502,
                        details={"response": ultravox_response}
                    )
                
                logger.info(f"[VOICES] [CREATE] Import successful | ultravox_voice_id={ultravox_voice_id}")

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
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "voice_id": voice_id,
                "client_id": client_id,
                "elevenlabs_voice_id": elevenlabs_voice_id if 'elevenlabs_voice_id' in locals() else None,
                "ultravox_voice_id": ultravox_voice_id if 'ultravox_voice_id' in locals() else None,
                "strategy": voice_data.strategy if 'voice_data' in locals() else None,
                "provider": provider,
                "provider_voice_id": provider_voice_id if 'provider_voice_id' in locals() else None,
            }
            logger.error(f"[VOICES] [CREATE] Error in voice creation (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            
            # Rollback: Delete temporary record
            try:
                db.delete("voices", {"id": voice_id, "client_id": client_id})
            except Exception as delete_error:
                logger.error(f"[VOICES] [CREATE] Failed to delete voice record during rollback (RAW ERROR): {str(delete_error)}", exc_info=True)
            
            # Clean up ElevenLabs voice if created
            if 'elevenlabs_voice_id' in locals() and elevenlabs_voice_id:
                try:
                    await elevenlabs_client.delete_voice(elevenlabs_voice_id)
                except Exception as cleanup_error:
                    cleanup_error_details = {
                        "error_type": type(cleanup_error).__name__,
                        "error_message": str(cleanup_error),
                        "full_traceback": traceback.format_exc(),
                        "elevenlabs_voice_id": elevenlabs_voice_id,
                    }
                    logger.error(f"[VOICES] [CREATE] Cleanup error (RAW ERROR): {json.dumps(cleanup_error_details, indent=2, default=str)}", exc_info=True)
            
            # Re-raise appropriate error - preserve original error message and details
            if isinstance(e, (ValidationError, NotFoundError, PaymentRequiredError, ProviderError)):
                raise
            
            # For unknown exceptions, wrap in ProviderError with full details
            error_message = str(e)
            if hasattr(e, 'message'):
                error_message = e.message
            elif hasattr(e, 'detail'):
                error_message = str(e.detail)
            
            # Include provider details if available
            error_details = {
                "original_error_type": type(e).__name__,
                "original_error_message": error_message,
                "voice_id": voice_id,
                "strategy": voice_data.strategy if 'voice_data' in locals() else None,
            }
            
            logger.error(f"[VOICES] [CREATE] Wrapping unknown exception as ProviderError (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            raise ProviderError(
                provider=provider,
                message=f"Voice import failed: {error_message}",
                http_status=500,
                details=error_details
            )
        
        # Prepare response record
        voice_record = voice_db_record.copy()
        voice_record["created_at"] = now
        voice_record["updated_at"] = now
        
        # Note: Credits are deducted in background task for native strategy
        # External imports don't require credits, so no deduction needed here
        
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
                    import traceback
                    import json
                    error_details_raw = {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "error_args": e.args if hasattr(e, 'args') else None,
                        "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                        "full_traceback": traceback.format_exc(),
                        "voice_id": voice_record.get('id'),
                        "voice_record": voice_record,
                    }
                    logger.warning(f"[VOICES] [LIST] Failed to process custom voice (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
                    import traceback
                    import json
                    error_details_raw = {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "error_args": e.args if hasattr(e, 'args') else None,
                        "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                        "full_traceback": traceback.format_exc(),
                        "ultravox_voice_id": uv_voice.get('voiceId'),
                        "ultravox_voice": uv_voice,
                    }
                    logger.warning(f"[VOICES] [LIST] Failed to process voice (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                    continue
            
            return {
                "data": voices_data,
                "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
            }
    
    except ProviderError as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "client_id": client_id,
            "source": source,
        }
        logger.error(f"[VOICES] [LIST] Ultravox API Error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        return {"data": [], "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now)}
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "error_module": getattr(e, '__module__', None),
            "error_class": type(e).__name__,
            "full_traceback": traceback.format_exc(),
            "client_id": client_id,
            "source": source,
        }
        logger.error(f"[VOICES] [LIST] Error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
                import traceback
                import json
                error_details_raw = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "error_args": e.args if hasattr(e, 'args') else None,
                    "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                    "full_traceback": traceback.format_exc(),
                    "ultravox_voice_id": uv_voice.get('voiceId'),
                    "ultravox_voice": uv_voice,
                }
                logger.warning(f"[VOICES] [SYNC] Failed to import voice (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "error_module": getattr(e, '__module__', None),
            "error_class": type(e).__name__,
            "full_traceback": traceback.format_exc(),
            "client_id": client_id,
            "ownership": ownership,
            "provider": provider,
        }
        logger.error(f"[VOICES] [SYNC] Failed to sync voices (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "voice_id": voice_id,
            "client_id": current_user.get("client_id"),
        }
        logger.error(f"[VOICES] [GET] Failed to get voice (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "voice_id": voice_id,
                "ultravox_voice_id": voice.get("ultravox_voice_id"),
            }
            logger.warning(f"[VOICES] [DELETE] Failed to handle Ultravox deletion (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
    
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
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
                "full_traceback": traceback.format_exc(),
                "voice_id": voice_id,
                "ultravox_voice_id": voice.get("ultravox_voice_id"),
            }
            logger.error(f"[VOICES] [SYNC] Failed to sync voice with Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "error_module": getattr(e, '__module__', None),
            "error_class": type(e).__name__,
            "full_traceback": traceback.format_exc(),
            "voice_id": voice_id,
            "voice": voice,
        }
        logger.error(f"[VOICES] [SYNC] Failed to sync voice with Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        
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
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "voice_id": voice_id,
                "request_id": request_id,
            }
            logger.debug(f"[VOICES] [PREVIEW] Voice not found in database (may be Ultravox voice) (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}")
    
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "http_status": e.http_status if hasattr(e, 'http_status') else None,
            "provider": e.provider if hasattr(e, 'provider') else None,
            "full_traceback": traceback.format_exc(),
            "ultravox_voice_id": ultravox_voice_id,
            "request_id": request_id,
            "voice_id": voice_id,
        }
        error_msg = f"Failed to get voice preview from Ultravox: {str(e)}"
        logger.error(f"[VOICES] [PREVIEW] Provider error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "ultravox_voice_id": ultravox_voice_id,
            "request_id": request_id,
            "voice_id": voice_id,
        }
        logger.error(f"[VOICES] [PREVIEW] Validation error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        # Re-raise ValidationError as-is (it's already a proper HTTP exception)
        raise
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "error_module": getattr(e, '__module__', None),
            "error_class": type(e).__name__,
            "full_traceback": traceback.format_exc(),
            "ultravox_voice_id": ultravox_voice_id,
            "request_id": request_id,
            "voice_id": voice_id,
        }
        error_msg = f"Failed to generate voice preview: {str(e)}"
        logger.error(f"[VOICES] [PREVIEW] Unexpected error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": error_msg
            }
        )

