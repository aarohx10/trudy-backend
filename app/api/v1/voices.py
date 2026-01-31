"""
Voice Endpoints - SIMPLIFIED
Just HTTP requests. That's it.
"""
from fastapi import APIRouter, Header, Depends, Query, Request, HTTPException, Form
from fastapi.responses import Response
from typing import Optional, List, Annotated, Union
from datetime import datetime
import uuid
import logging
import httpx

from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError, ProviderError
from app.models.schemas import VoiceResponse, ResponseMeta
from app.core.config import settings
from app.services.ultravox import ultravox_client

logger = logging.getLogger(__name__)
router = APIRouter()


def _db_voice_to_response(record: dict) -> VoiceResponse:
    """Build VoiceResponse from DB voice record (has clerk_org_id). API still returns client_id for compat."""
    out = dict(record)
    out["client_id"] = record.get("clerk_org_id") or ""
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    for key in ("name", "provider", "type", "language", "status"):
        if out.get(key) is None:
            out[key] = ""
    return VoiceResponse(**out)


@router.post("")
async def create_voice(
    request: Request,
    current_user: dict = Depends(require_admin_role),
    name: Optional[str] = Form(None),
    strategy: Optional[str] = Form(None),
    provider: Optional[str] = Form(None),
    provider_voice_id: Optional[str] = Form(None),
):
    """
    Create voice (import only). Supports JSON or multipart/form-data.
    Flow: validate org → parse body → Ultravox import → save to DB by clerk_org_id → return.
    """
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id or not str(clerk_org_id).strip():
        raise ValidationError("Missing organization ID in token")
    clerk_org_id = str(clerk_org_id).strip()
    user_id = current_user.get("user_id")

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        name = body.get("name")
        strategy = body.get("strategy")
        source = body.get("source") or {}
        provider = (body.get("provider_overrides") or {}).get("provider", "elevenlabs")
        provider_voice_id = source.get("provider_voice_id")
    elif "multipart/form-data" in content_type:
        provider = provider or "elevenlabs"
    else:
        raise ValidationError("Content-Type must be application/json or multipart/form-data")

    if not name:
        raise ValidationError("Voice name is required")
    if not strategy:
        raise ValidationError("Strategy is required")
    if strategy == "native":
        raise ValidationError("Voice cloning (native) has been removed. Use voice import (external) instead.")
    if strategy != "external":
        raise ValidationError("Only 'external' strategy is supported")
    if not provider_voice_id:
        raise ValidationError("Provider voice ID is required for external import")
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key is not configured")

    ultravox_response = await ultravox_client.import_voice_from_provider(
        name=name,
        provider=provider,
        provider_voice_id=provider_voice_id,
        description=f"Imported voice: {name}",
    )
    ultravox_voice_id = (
        ultravox_response.get("voiceId")
        or ultravox_response.get("id")
        or ultravox_response.get("voice_id")
        or (ultravox_response.get("data") or {}).get("voiceId")
        or (ultravox_response.get("data") or {}).get("id")
    )
    if not ultravox_voice_id:
        raise ProviderError(
            provider="ultravox",
            message="Ultravox response missing voiceId",
            http_status=500,
            details={"response": ultravox_response},
        )

    voice_id = str(uuid.uuid4())
    now = datetime.utcnow()
    db = DatabaseService(org_id=clerk_org_id)
    voice_record = {
        "id": voice_id,
        "clerk_org_id": clerk_org_id,
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
    created_voice = db.insert("voices", voice_record)
    return {
        "data": _db_voice_to_response(created_voice or voice_record),
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
    }


@router.get("")
async def list_voices(
    request: Request,
        current_user: dict = Depends(require_admin_role),
    source: Optional[str] = Query(None, description="Filter by source: 'ultravox' or 'custom'"),
):
    """
    List voices - simple: from DB or Ultravox.
    
    CRITICAL: Filters by clerk_org_id to show organization voices.
    Shows: system_voices + organization_voices (all voices available to the team).
    """
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    now = datetime.utcnow()
    
    # Custom voices: from database (includes imported "reference" voices - voice cloning has been removed)
    if source == "custom":
        db = DatabaseService(org_id=clerk_org_id)
        
        # Get all custom voices (type: "reference" for imported, type: "custom" for cloned)
        # CRITICAL: Filter by clerk_org_id - shows all organization voices
        imported_voices = db.select("voices", {"clerk_org_id": clerk_org_id, "type": "reference"}, order_by="created_at DESC")
        cloned_voices = db.select("voices", {"clerk_org_id": clerk_org_id, "type": "custom"}, order_by="created_at DESC")
        
        # Combine imported and cloned voices
        all_voices = list(imported_voices) + list(cloned_voices)
        
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
                voices_data.append(_db_voice_to_response(voice_record))
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
                    "id": str(ultravox_voice_id),
                    "client_id": clerk_org_id,
                    "name": uv_voice.get("name", "Untitled Voice") or "",
                    "provider": uv_voice.get("provider", "elevenlabs") or "elevenlabs",
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
        current_user: dict = Depends(require_admin_role),
):
    """Get single voice - from DB (filtered by org_id)"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    db = DatabaseService(org_id=clerk_org_id)
    voice = db.get_voice(voice_id, org_id=clerk_org_id)
    if not voice:
        raise NotFoundError("voice", voice_id)
    return {
        "data": _db_voice_to_response(voice),
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
    }


@router.patch("/{voice_id}")
async def update_voice(
    voice_id: str,
    request: Request,
        current_user: dict = Depends(require_admin_role),
):
    """Update voice (name and description only)"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    db = DatabaseService(org_id=clerk_org_id)
    voice = db.get_voice(voice_id, org_id=clerk_org_id)
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    body = await request.json()
    update_data = {k: v for k, v in body.items() if k in ["name", "description"]}
    if not update_data:
        return {
            "data": _db_voice_to_response(voice),
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
        }
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("voices", {"id": voice_id, "clerk_org_id": clerk_org_id}, update_data)
    updated_voice = db.get_voice(voice_id, org_id=clerk_org_id)
    return {
        "data": _db_voice_to_response(updated_voice),
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
    }


@router.delete("/{voice_id}")
async def delete_voice(
    voice_id: str,
        current_user: dict = Depends(require_admin_role),
):
    """Delete voice"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    db = DatabaseService(org_id=clerk_org_id)
    voice = db.get_voice(voice_id, org_id=clerk_org_id)
    if not voice:
        raise NotFoundError("voice", voice_id)
    
    db.delete("voices", {"id": voice_id, "clerk_org_id": clerk_org_id})
    
    return {
        "data": {"id": voice_id, "deleted": True},
        "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=datetime.utcnow()),
    }


@router.get("/{voice_id}/preview")
async def preview_voice(
    voice_id: str,
    request: Request,
        current_user: dict = Depends(require_admin_role),
):
    """Preview voice - from Ultravox. ALWAYS uses ultravox_voice_id, never provider_voice_id or local voice_id."""
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key not configured")
    
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    db = DatabaseService(org_id=clerk_org_id)
    voice = None
    try:
        voice = db.get_voice(voice_id, org_id=clerk_org_id)
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
        # Custom voice (imported) - MUST use ultravox_voice_id from DB (voice cloning has been removed)
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
