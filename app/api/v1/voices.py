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
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError, ProviderError
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
            
            # Step 1: Clone in ElevenLabs (NO DB, NO CREDITS - matches test script)
            import time
            start_time = time.time()
            logger.info(f"[VOICES] Cloning voice in ElevenLabs | name={name}")
            files_data = []
            for file_item in files:
                if isinstance(file_item, UploadFile):
                    file_read_start = time.time()
                    content = await file_item.read()
                    file_read_time = time.time() - file_read_start
                    logger.info(f"[VOICES] File read completed | filename={file_item.filename} | size={len(content)} bytes | time={file_read_time:.2f}s")
                    filename = file_item.filename or "audio.mp3"
                    content_type = file_item.content_type or "audio/mpeg"
                    files_data.append(("files", (filename, content, content_type)))
            
            try:
                elevenlabs_start = time.time()
                logger.info(f"[VOICES] Starting ElevenLabs API call | timeout=120s")
                async with httpx.AsyncClient(timeout=120.0) as client:
                    elevenlabs_response = await client.post(
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
                elevenlabs_time = time.time() - elevenlabs_start
                logger.info(f"[VOICES] ElevenLabs API call completed | time={elevenlabs_time:.2f}s")
            except httpx.TimeoutException as e:
                logger.error(f"[VOICES] ElevenLabs timeout after 120s | error={str(e)}")
                raise ProviderError(
                    provider="elevenlabs",
                    message="ElevenLabs API request timed out after 120 seconds. The voice cloning may still be processing. Please try again later.",
                    http_status=504,
                )
            except httpx.RequestError as e:
                logger.error(f"[VOICES] ElevenLabs request error | error={str(e)} | type={type(e).__name__}")
                raise ProviderError(
                    provider="elevenlabs",
                    message=f"Failed to connect to ElevenLabs API: {str(e)}",
                    http_status=502,
                )
            
            total_elevenlabs_time = time.time() - start_time
            logger.info(f"[VOICES] ElevenLabs clone successful | voice_id={elevenlabs_voice_id} | total_time={total_elevenlabs_time:.2f}s")
            
            # Step 2: Import to Ultravox - EXACT COPY OF TEST SCRIPT
            ultravox_start = time.time()
            logger.info(f"[VOICES] Importing to Ultravox | elevenlabs_voice_id={elevenlabs_voice_id}")
            
            # Normalize name exactly like test script (line 87-92)
            normalized_name = name.lower().replace(" ", "_").replace("-", "_")
            normalized_name = "".join(c if c.isalnum() or c == "_" else "" for c in normalized_name)
            normalized_name = f"{normalized_name}_{elevenlabs_voice_id}"
            
            # Build payload exactly like test script (line 95-117)
            url = f"{settings.ULTRAVOX_BASE_URL}/api/voices"
            payload = {
                "name": normalized_name,
            }
            description = f"Cloned voice: {name}"
            if description:
                payload["description"] = description
            payload["definition"] = {
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
            
            headers = {
                "X-API-Key": settings.ULTRAVOX_API_KEY,
                "Content-Type": "application/json",
            }
            
            # Direct HTTP call exactly like test script (line 129-135)
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    ultravox_response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                    )
                    
                    if ultravox_response.status_code >= 400:
                        error_text = ultravox_response.text[:500] if ultravox_response.text else "No response body"
                        raise ProviderError(
                            provider="ultravox",
                            message=f"Ultravox import failed: {error_text}",
                            http_status=ultravox_response.status_code,
                        )
                    
                    ultravox_data = ultravox_response.json()
                ultravox_time = time.time() - ultravox_start
                logger.info(f"[VOICES] Ultravox API call completed | time={ultravox_time:.2f}s")
            except httpx.TimeoutException as e:
                logger.error(f"[VOICES] Ultravox timeout after 120s | error={str(e)}")
                raise ProviderError(
                    provider="ultravox",
                    message="Ultravox API request timed out after 120 seconds. The voice import may still be processing. Please try again later.",
                    http_status=504,
                )
            except httpx.RequestError as e:
                logger.error(f"[VOICES] Ultravox request error | error={str(e)} | type={type(e).__name__}")
                raise ProviderError(
                    provider="ultravox",
                    message=f"Failed to connect to Ultravox API: {str(e)}",
                    http_status=502,
                )
            
            # Extract voice ID exactly like test script (line 151-157)
            ultravox_voice_id = (
                ultravox_data.get("voiceId") or
                ultravox_data.get("id") or
                ultravox_data.get("voice_id") or
                (ultravox_data.get("data", {}) if isinstance(ultravox_data.get("data"), dict) else {}).get("voiceId") or
                (ultravox_data.get("data", {}) if isinstance(ultravox_data.get("data"), dict) else {}).get("id")
            )
            
            if not ultravox_voice_id:
                raise ProviderError(
                    provider="ultravox",
                    message="Ultravox response missing voiceId",
                    http_status=500,
                    details={"response": ultravox_data},
                )
            
            total_ultravox_time = time.time() - ultravox_start
            logger.info(f"[VOICES] Ultravox import successful | ultravox_voice_id={ultravox_voice_id} | total_time={total_ultravox_time:.2f}s")
            
            # Step 3: Save to DB (AFTER both API calls succeed - no credit checks, no credit updates)
            db_start = time.time()
            db = DatabaseService(current_user["token"])
            db.set_auth(current_user["token"])
            
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
            db_time = time.time() - db_start
            total_time = time.time() - start_time
            logger.info(f"[VOICES] Voice cloned successfully | voice_id={voice_id} | db_time={db_time:.2f}s | total_time={total_time:.2f}s")
            
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
                
                logger.info(f"[VOICES] Ultravox response received | response_keys={list(ultravox_response.keys()) if isinstance(ultravox_response, dict) else 'not_dict'} | response_type={type(ultravox_response).__name__}")
                
                # Log full response for debugging (truncated to avoid huge logs)
                import json
                response_str = json.dumps(ultravox_response, default=str)[:1000]
                logger.debug(f"[VOICES] Ultravox response (first 1000 chars): {response_str}")
                
                # Extract voice ID - try multiple possible field names
                ultravox_voice_id = (
                    ultravox_response.get("voiceId") or 
                    ultravox_response.get("id") or
                    ultravox_response.get("voice_id") or
                    (ultravox_response.get("data", {}) if isinstance(ultravox_response.get("data"), dict) else {}).get("voiceId") or
                    (ultravox_response.get("data", {}) if isinstance(ultravox_response.get("data"), dict) else {}).get("id")
                )
                
                if not ultravox_voice_id:
                    logger.error(f"[VOICES] Ultravox response missing voiceId | response={ultravox_response} | response_keys={list(ultravox_response.keys()) if isinstance(ultravox_response, dict) else 'N/A'}")
                    raise ProviderError(
                        provider="ultravox",
                        message=f"Ultravox response missing voiceId. Response structure: {list(ultravox_response.keys()) if isinstance(ultravox_response, dict) else 'not a dict'}",
                        http_status=500,
                        details={"response": ultravox_response},
                    )
                
                logger.info(f"[VOICES] Extracted ultravox_voice_id | ultravox_voice_id={ultravox_voice_id} | from_field={'voiceId' if ultravox_response.get('voiceId') else 'id' if ultravox_response.get('id') else 'other'}")
                
                logger.info(f"[VOICES] Ultravox import successful | ultravox_voice_id={ultravox_voice_id}")
                
                # Step 2: Save to DB (AFTER Ultravox import succeeds - no credit checks)
                db = DatabaseService(current_user["token"])
                db.set_auth(current_user["token"])
                
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
    
    except (ValidationError, ForbiddenError, NotFoundError, ProviderError):
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
    """Preview voice - from Ultravox. ALWAYS uses ultravox_voice_id, never provider_voice_id or local voice_id."""
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key not configured")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Get voice from DB - this is required for custom voices (cloned/imported)
    voice = None
    try:
        voice = db.get_voice(voice_id, current_user["client_id"])
        if voice:
            logger.info(f"[VOICES] Preview: Found voice in DB | voice_id={voice_id} | ultravox_voice_id={voice.get('ultravox_voice_id')} | provider_voice_id={voice.get('provider_voice_id')}")
        else:
            logger.info(f"[VOICES] Preview: Voice not found in DB | voice_id={voice_id}")
    except Exception as e:
        logger.warning(f"[VOICES] Preview: Exception getting voice from DB | voice_id={voice_id} | error={str(e)} | type={type(e).__name__}")
        voice = None
    
    # Determine ultravox_voice_id - CRITICAL: Always use ultravox_voice_id, NEVER provider_voice_id
    ultravox_voice_id = None
    
    if voice:
        # Custom voice (cloned or imported) - MUST use ultravox_voice_id from DB
        ultravox_voice_id = voice.get("ultravox_voice_id")
        
        if not ultravox_voice_id:
            # This is a critical error - custom voices MUST have ultravox_voice_id
            logger.error(f"[VOICES] Preview: CRITICAL - Voice in DB but missing ultravox_voice_id | voice_id={voice_id} | voice_keys={list(voice.keys())} | voice={voice}")
            raise ValidationError(
                f"Voice does not have an Ultravox ID. This voice cannot be previewed. Voice ID: {voice_id}, Name: {voice.get('name', 'Unknown')}"
            )
        
        # Double-check we're not accidentally using provider_voice_id
        provider_voice_id = voice.get("provider_voice_id")
        if ultravox_voice_id == provider_voice_id:
            logger.warning(f"[VOICES] Preview: WARNING - ultravox_voice_id equals provider_voice_id | voice_id={voice_id} | id={ultravox_voice_id}")
        
        logger.info(f"[VOICES] Preview: Using ultravox_voice_id from DB | voice_id={voice_id} | ultravox_voice_id={ultravox_voice_id}")
        
        # Verify voice exists in Ultravox before trying to preview
        # This helps catch cases where the voice was deleted or never properly imported
        try:
            ultravox_voice_info = await ultravox_client.get_voice(ultravox_voice_id)
            logger.info(f"[VOICES] Preview: Verified voice exists in Ultravox | ultravox_voice_id={ultravox_voice_id} | name={ultravox_voice_info.get('name', 'Unknown')}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error(f"[VOICES] Preview: Voice not found in Ultravox | ultravox_voice_id={ultravox_voice_id} | voice_id={voice_id}")
                raise NotFoundError(
                    "voice",
                    ultravox_voice_id,
                    message=f"Voice not found in Ultravox. The voice may have been deleted or the import may have failed. Ultravox Voice ID: {ultravox_voice_id}",
                )
            else:
                logger.warning(f"[VOICES] Preview: Error verifying voice in Ultravox (non-404) | ultravox_voice_id={ultravox_voice_id} | status={e.response.status_code} | continuing anyway...")
        except Exception as e:
            logger.warning(f"[VOICES] Preview: Exception verifying voice in Ultravox | ultravox_voice_id={ultravox_voice_id} | error={str(e)} | continuing anyway...")
    else:
        # Voice not in DB - might be a default Ultravox voice (from explore section)
        # In this case, voice_id should already be the ultravox_voice_id
        logger.info(f"[VOICES] Preview: Voice not in DB, using voice_id as ultravox_voice_id (default Ultravox voice) | voice_id={voice_id}")
        ultravox_voice_id = voice_id
    
    # Validate ultravox_voice_id is not empty
    if not ultravox_voice_id:
        logger.error(f"[VOICES] Preview: CRITICAL - ultravox_voice_id is empty | voice_id={voice_id}")
        raise ValidationError("Ultravox voice ID is required for preview")
    
    # Always use ultravox_voice_id for preview - NEVER use provider_voice_id
    logger.info(f"[VOICES] Preview: Calling Ultravox preview API | ultravox_voice_id={ultravox_voice_id} | voice_id={voice_id}")
    
    try:
        audio_bytes = await ultravox_client.get_voice_preview(ultravox_voice_id)
        logger.info(f"[VOICES] Preview: Success | ultravox_voice_id={ultravox_voice_id} | audio_size={len(audio_bytes)} bytes")
    except httpx.HTTPStatusError as e:
        # HTTP error from Ultravox API
        error_msg = f"Ultravox API error: {e.response.status_code}"
        error_details = {}
        
        if e.response.text:
            try:
                error_data = e.response.json()
                error_msg += f" - {error_data.get('message', error_data.get('error', str(error_data)))}"
                error_details = error_data
            except:
                error_text = e.response.text[:500]
                error_msg += f" - {error_text}"
                error_details = {"raw_response": error_text}
        
        logger.error(f"[VOICES] Preview: Ultravox API HTTP error | ultravox_voice_id={ultravox_voice_id} | status={e.response.status_code} | error={error_msg} | details={error_details}")
        
        # Provide more specific error messages based on status code
        if e.response.status_code == 400:
            raise ProviderError(
                provider="ultravox",
                message=f"Invalid request to Ultravox API. The voice may not be ready for preview yet, or the voice ID may be incorrect. {error_msg}",
                http_status=502,
                details={
                    "ultravox_voice_id": ultravox_voice_id,
                    "status_code": e.response.status_code,
                    "ultravox_error": error_details,
                },
            )
        elif e.response.status_code == 404:
            raise NotFoundError(
                "voice",
                ultravox_voice_id,
                message=f"Voice not found in Ultravox. {error_msg}",
            )
        else:
            raise ProviderError(
                provider="ultravox",
                message=error_msg,
                http_status=502,
                details={
                    "ultravox_voice_id": ultravox_voice_id,
                    "status_code": e.response.status_code,
                    "ultravox_error": error_details,
                },
            )
    except Exception as e:
        logger.error(f"[VOICES] Preview: Unexpected error calling Ultravox | ultravox_voice_id={ultravox_voice_id} | error={str(e)} | type={type(e).__name__}")
        import traceback
        logger.error(f"[VOICES] Preview: Traceback | {traceback.format_exc()}")
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to get voice preview: {str(e)}",
            http_status=502,
            details={"ultravox_voice_id": ultravox_voice_id},
        )
    
    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": 'inline; filename="voice-preview.wav"'},
    )
