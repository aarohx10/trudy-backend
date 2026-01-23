"""
Voice Endpoints - SIMPLIFIED
Just HTTP requests. That's it.
"""
from fastapi import APIRouter, Header, Depends, Query, Request, HTTPException, UploadFile
from fastapi.responses import Response
from typing import Optional, List
from datetime import datetime
import uuid
import logging
import httpx

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, PaymentRequiredError, ForbiddenError, ProviderError
from app.models.schemas import VoiceResponse, ResponseMeta
from app.core.config import settings
from app.services.ultravox import ultravox_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("")
async def create_voice(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Create voice - SIMPLE: Just HTTP requests
    
    Supports both JSON and multipart/form-data:
    
    JSON (for imports):
    {
        "name": "Voice name",
        "strategy": "external",
        "source": {"provider_voice_id": "..."},
        "provider_overrides": {"provider": "elevenlabs"}
    }
    
    FormData (multipart/form-data for clones):
    - name: Voice name (required)
    - strategy: "native" (clone) or "external" (import)
    - files: Audio files (for native) - required for native
    - provider_voice_id: Provider voice ID (for external) - required for external
    - provider: "elevenlabs" (default)
    """
    try:
        client_id = current_user.get("client_id")
        user_id = current_user.get("user_id")
        
        if current_user["role"] not in ["client_admin", "agency_admin"]:
            raise ForbiddenError("Insufficient permissions")
        
        db = DatabaseService(current_user["token"])
        db.set_auth(current_user["token"])
        
        # Determine if JSON or multipart
        content_type = request.headers.get("content-type", "")
        is_json = "application/json" in content_type
        
        logger.info(f"[VOICES] Creating voice | content_type={content_type} | is_json={is_json}")
        
        if is_json:
            # JSON request (for imports)
            body = await request.json()
            logger.info(f"[VOICES] JSON body received | body_keys={list(body.keys()) if isinstance(body, dict) else 'not_dict'}")
            name = body.get("name")
            strategy = body.get("strategy")
            source = body.get("source", {})
            provider_overrides = body.get("provider_overrides", {})
            provider = provider_overrides.get("provider", "elevenlabs")
            provider_voice_id = source.get("provider_voice_id")
            files = []
        else:
            # Multipart form data (for clones)
            form = await request.form()
            name = form.get("name")
            strategy = form.get("strategy")
            provider = form.get("provider", "elevenlabs")
            provider_voice_id = form.get("provider_voice_id")
            files = form.getlist("files")
        
        logger.info(f"[VOICES] Parsed request | name={name} | strategy={strategy} | provider={provider} | provider_voice_id={provider_voice_id}")
        
        if not name:
            raise ValidationError("Voice name is required")
        if not strategy:
            raise ValidationError("Strategy is required (native or external)")
        
        voice_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        # NATIVE: Clone voice
        if strategy == "native":
            if not files or len(files) == 0:
                raise ValidationError("At least one audio file is required for voice cloning")
            
            if not settings.ELEVENLABS_API_KEY:
                raise ValidationError("ElevenLabs API key is not configured")
            if not settings.ULTRAVOX_API_KEY:
                raise ValidationError("Ultravox API key is not configured")
            
            # Credit check
            client = db.get_client(client_id)
            if not client or client.get("credits_balance", 0) < 50:
                raise PaymentRequiredError(
                    "Insufficient credits for voice cloning. Required: 50",
                    {"required": 50, "available": client.get("credits_balance", 0) if client else 0},
                )
            
            # Step 1: Clone in ElevenLabs
            logger.info(f"[VOICES] Cloning voice in ElevenLabs | name={name}")
            files_data = []
            for file_item in files:
                if isinstance(file_item, UploadFile):
                    content = await file_item.read()
                    filename = file_item.filename or "audio.mp3"
                    content_type = file_item.content_type or "audio/mpeg"
                    files_data.append(("files", (filename, content, content_type)))
            
            async with httpx.AsyncClient(timeout=120.0) as http_client:
                elevenlabs_response = await http_client.post(
                    "https://api.elevenlabs.io/v1/voices/add",
                    headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
                    data={"name": name},
                    files=files_data,
                )
                
                if elevenlabs_response.status_code >= 400:
                    error_text = elevenlabs_response.text[:500] if elevenlabs_response.text else "No response body"
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
            
            logger.info(f"[VOICES] ElevenLabs clone successful | voice_id={elevenlabs_voice_id}")
            
            # Step 2: Import to Ultravox
            logger.info(f"[VOICES] Importing to Ultravox | elevenlabs_voice_id={elevenlabs_voice_id}")
            ultravox_response = await ultravox_client.import_voice_from_provider(
                name=name,
                provider="elevenlabs",
                provider_voice_id=elevenlabs_voice_id,
                description=f"Cloned voice: {name}",
            )
            ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
            
            if not ultravox_voice_id:
                raise ProviderError(
                    provider="ultravox",
                    message="Ultravox response missing voiceId",
                    http_status=500,
                )
            
            logger.info(f"[VOICES] Ultravox import successful | ultravox_voice_id={ultravox_voice_id}")
            
            # Step 3: Save to DB
            voice_record = {
                "id": voice_id,
                "client_id": client_id,
                "user_id": user_id,
                "name": name,
                "provider": "elevenlabs",
                "type": "custom",
                "language": "en-US",
                "status": "active",
                "provider_voice_id": elevenlabs_voice_id,
                "ultravox_voice_id": ultravox_voice_id,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            db.insert("voices", voice_record)
            
            # Deduct credits
            db.update("clients", {"id": client_id}, {
                "credits_balance": client.get("credits_balance", 0) - 50,
                "updated_at": now.isoformat(),
            })
            
            logger.info(f"[VOICES] Voice cloned successfully | voice_id={voice_id}")
            
            return {
                "data": VoiceResponse(**voice_record),
                "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
            }
        
        # EXTERNAL: Import voice
        else:
            if not provider_voice_id:
                raise ValidationError("Provider voice ID is required for external import")
            
            if not settings.ULTRAVOX_API_KEY:
                raise ValidationError("Ultravox API key is not configured")
            
            try:
                # Step 1: Import to Ultravox
                logger.info(f"[VOICES] Importing voice from provider | provider={provider} | provider_voice_id={provider_voice_id} | name={name}")
                ultravox_response = await ultravox_client.import_voice_from_provider(
                    name=name,
                    provider=provider,
                    provider_voice_id=provider_voice_id,
                    description=f"Imported voice: {name}",
                )
                
                logger.info(f"[VOICES] Ultravox response received | response_keys={list(ultravox_response.keys()) if isinstance(ultravox_response, dict) else 'not_dict'}")
                
                ultravox_voice_id = ultravox_response.get("voiceId") or ultravox_response.get("id")
                
                if not ultravox_voice_id:
                    logger.error(f"[VOICES] Ultravox response missing voiceId | response={ultravox_response}")
                    raise ProviderError(
                        provider="ultravox",
                        message="Ultravox response missing voiceId",
                        http_status=500,
                    )
                
                logger.info(f"[VOICES] Ultravox import successful | ultravox_voice_id={ultravox_voice_id}")
                
                # Step 2: Save to DB
                voice_record = {
                    "id": voice_id,
                    "client_id": client_id,
                    "user_id": user_id,
                    "name": name,
                    "provider": provider,
                    "type": "reference",
                    "language": "en-US",
                    "status": "active",
                    "provider_voice_id": provider_voice_id,
                    "ultravox_voice_id": ultravox_voice_id,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
                
                logger.info(f"[VOICES] Saving voice to DB | voice_id={voice_id}")
                db.insert("voices", voice_record)
                
                logger.info(f"[VOICES] Voice imported successfully | voice_id={voice_id}")
                
                return {
                    "data": VoiceResponse(**voice_record),
                    "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
                }
                
            except ProviderError as pe:
                # Re-raise ProviderError as-is
                provider = pe.details.get("provider", "unknown") if pe.details else "unknown"
                http_status = pe.details.get("httpStatus", 500) if pe.details else 500
                logger.error(f"[VOICES] ProviderError during import | provider={provider} | message={pe.message} | http_status={http_status}")
                raise
            except Exception as e:
                # Log the actual error
                import traceback
                logger.error(f"[VOICES] Error importing voice | error={str(e)} | type={type(e).__name__} | traceback={traceback.format_exc()}")
                raise ProviderError(
                    provider="ultravox",
                    message=f"Failed to import voice: {str(e)}",
                    http_status=500,
                )
    
    except (ValidationError, PaymentRequiredError, ForbiddenError, NotFoundError, ProviderError):
        # Re-raise known errors as-is
        raise
    except Exception as e:
        # Catch any unexpected errors and log them
        import traceback
        logger.error(f"[VOICES] Unexpected error in create_voice | error={str(e)} | type={type(e).__name__} | traceback={traceback.format_exc()}")
        raise ProviderError(
            provider="unknown",
            message=f"An unexpected error occurred: {str(e)}",
            http_status=500,
        )


@router.get("")
async def list_voices(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    source: Optional[str] = Query(None, description="Filter by source: 'ultravox' or 'custom'"),
):
    """List voices - simple: from DB or Ultravox"""
    client_id = current_user.get("client_id")
    now = datetime.utcnow()
    
    # Custom voices: from database (includes both cloned "custom" and imported "reference" voices)
    if source == "custom":
        db = DatabaseService(current_user["token"])
        db.set_auth(current_user["token"])
        
        # Get both cloned voices (type: "custom") and imported voices (type: "reference")
        # These are all "my voices" - voices owned by this client
        cloned_voices = db.select("voices", {"client_id": client_id, "type": "custom"}, order_by="created_at DESC")
        imported_voices = db.select("voices", {"client_id": client_id, "type": "reference"}, order_by="created_at DESC")
        
        # Combine both lists (both are already sorted DESC, so we maintain order)
        all_voices = cloned_voices + imported_voices
        
        # Sort by created_at descending (newest first) - handle both ISO strings and datetime objects
        def get_sort_key(voice):
            created_at = voice.get("created_at", "")
            if isinstance(created_at, str):
                return created_at
            elif hasattr(created_at, "isoformat"):
                return created_at.isoformat()
            return ""
        
        all_voices.sort(key=get_sort_key, reverse=True)
        
        voices_data = []
        for voice_record in all_voices:
            try:
                voices_data.append(VoiceResponse(**voice_record))
            except Exception as e:
                logger.warning(f"[VOICES] Failed to process voice: {str(e)}")
                continue
        
        return {
            "data": voices_data,
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
        }
    
    # Ultravox voices: from Ultravox API
    else:
        if not settings.ULTRAVOX_API_KEY:
            raise ValidationError("Ultravox API key not configured")
        
        ultravox_voices = await ultravox_client.list_voices()
        
        voices_data = []
        for uv_voice in ultravox_voices:
            try:
                definition = uv_voice.get("definition", {})
                provider_voice_id = None
                
                if "elevenLabs" in definition:
                    provider_voice_id = definition["elevenLabs"].get("voiceId")
                elif "cartesia" in definition:
                    provider_voice_id = definition["cartesia"].get("voiceId")
                elif "lmnt" in definition:
                    provider_voice_id = definition["lmnt"].get("voiceId")
                elif "google" in definition:
                    provider_voice_id = definition["google"].get("voiceId")
                
                if not provider_voice_id:
                    continue
                
                ultravox_voice_id = uv_voice.get("voiceId")
                if not ultravox_voice_id:
                    continue
                
                voice_data = {
                    "id": ultravox_voice_id,
                    "client_id": client_id,
                    "name": uv_voice.get("name", "Untitled Voice"),
                    "provider": uv_voice.get("provider", "elevenlabs"),
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
                logger.warning(f"[VOICES] Failed to process voice: {str(e)}")
                continue
        
        return {
            "data": voices_data,
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
        }


@router.get("/{voice_id}")
async def get_voice(
    voice_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single voice - from DB"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    return {
        "data": VoiceResponse(**voice),
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
    }


@router.patch("/{voice_id}")
async def update_voice(
    voice_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update voice (name and description only)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, current_user["client_id"])
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    body = await request.json()
    update_data = {k: v for k, v in body.items() if k in ["name", "description"]}
    
    if not update_data:
        return {
            "data": VoiceResponse(**voice),
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
        }
    
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("voices", {"id": voice_id}, update_data)
    
    updated_voice = db.get_voice(voice_id, current_user["client_id"])
    
    return {
        "data": VoiceResponse(**updated_voice),
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
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
    
    db.delete("voices", {"id": voice_id, "client_id": current_user["client_id"]})
    
    return {
        "data": {"id": voice_id, "deleted": True},
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
    }


@router.get("/{voice_id}/preview")
async def preview_voice(
    voice_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Preview voice - from Ultravox"""
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key not configured")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Try to get voice from DB
    voice = None
    try:
        voice = db.get_voice(voice_id, current_user["client_id"])
    except:
        pass
    
    if voice:
        ultravox_voice_id = voice.get("ultravox_voice_id")
        if not ultravox_voice_id:
            raise ValidationError("Voice does not have an Ultravox ID")
    else:
        ultravox_voice_id = voice_id
    
    audio_bytes = await ultravox_client.get_voice_preview(ultravox_voice_id)
    
    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": 'inline; filename="voice-preview.wav"'},
    )
