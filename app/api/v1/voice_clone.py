"""
Voice Cloning Endpoint - SIMPLE & SEPARATE
Based on test_voice_clone.py - keeps it lightweight and basic
"""
from fastapi import APIRouter, Depends, File, Form, UploadFile, Header
from fastapi.responses import Response
from typing import Optional, List
from datetime import datetime
import uuid
import logging
import httpx

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError, ForbiddenError, ProviderError
from app.models.schemas import VoiceResponse, ResponseMeta
from app.core.config import settings
from app.services.ultravox import ultravox_client
from app.core.db_logging import log_to_database

logger = logging.getLogger(__name__)
router = APIRouter()


async def clone_to_elevenlabs(audio_files: List[bytes], voice_name: str) -> dict:
    """
    Clone voice to ElevenLabs - EXACTLY like test_voice_clone.py
    Simple, direct, no over-engineering
    """
    if not settings.ELEVENLABS_API_KEY:
        raise ProviderError(
            provider="elevenlabs",
            message="ElevenLabs API key is not configured",
            http_status=500,
        )
    
    url = "https://api.elevenlabs.io/v1/voices/add"
    logger.info(f"[VOICE_CLONE] Step 1: Cloning to ElevenLabs | name={voice_name} | files_count={len(audio_files)}")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Build files exactly like test script
        files = []
        for i, audio_bytes in enumerate(audio_files):
            files.append(("files", (f"sample_{i}.mp3", audio_bytes, "audio/mpeg")))
        
        data = {"name": voice_name}
        
        logger.info(f"[VOICE_CLONE] Sending request to ElevenLabs...")
        response = await client.post(
            url,
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
            data=data,
            files=files,
        )
        
        if response.status_code >= 400:
            error_text = response.text[:500] if response.text else "No response body"
            logger.error(f"[VOICE_CLONE] ElevenLabs error: {response.status_code} | {error_text}")
            raise ProviderError(
                provider="elevenlabs",
                message=f"ElevenLabs clone failed: {error_text}",
                http_status=response.status_code,
            )
        
        result = response.json()
        voice_id = result.get("voice_id")
        
        if not voice_id:
            logger.error(f"[VOICE_CLONE] ElevenLabs response missing voice_id | response={result}")
            raise ProviderError(
                provider="elevenlabs",
                message="ElevenLabs response missing voice_id",
                http_status=500,
            )
        
        logger.info(f"[VOICE_CLONE] ✅ ElevenLabs clone successful! | voice_id={voice_id}")
        return {"voice_id": voice_id, "full_response": result}


@router.post("")
async def create_voice_clone(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    name: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """
    Create voice clone - SIMPLE: Just like test_voice_clone.py
    
    Flow:
    1. Clone voice to ElevenLabs (using uploaded audio files)
    2. Import cloned voice to Ultravox
    3. Save to database
    
    FormData (multipart/form-data):
    - name: Voice name (required)
    - files: Audio files (required, one or more)
    """
    logger.info("=" * 80)
    logger.info(f"[VOICE_CLONE] 🔥 CREATE VOICE CLONE ENDPOINT HIT!")
    logger.info(f"[VOICE_CLONE] Name: {name} | Files: {len(files)}")
    logger.info("=" * 80)
    
    try:
        # Auth check
        client_id = current_user.get("client_id")
        user_id = current_user.get("user_id")
        
        if current_user["role"] not in ["client_admin", "agency_admin"]:
            raise ForbiddenError("Insufficient permissions")
        
        # Validation
        if not name or not name.strip():
            raise ValidationError("Voice name is required")
        
        if not files or len(files) == 0:
            raise ValidationError("At least one audio file is required")
        
        # Read audio files into bytes (exactly like test script)
        logger.info(f"[VOICE_CLONE] Reading {len(files)} audio file(s)...")
        audio_files_bytes = []
        for i, file in enumerate(files):
            audio_bytes = await file.read()
            audio_files_bytes.append(audio_bytes)
            logger.info(f"[VOICE_CLONE] File {i+1}: {file.filename} | size={len(audio_bytes)} bytes")
        
        # Step 1: Clone to ElevenLabs (exactly like test script)
        logger.info(f"[VOICE_CLONE] Step 1: Cloning to ElevenLabs...")
        elevenlabs_result = await clone_to_elevenlabs(audio_files_bytes, name)
        elevenlabs_voice_id = elevenlabs_result["voice_id"]
        
        # Step 2: Import to Ultravox (exactly like test script)
        logger.info(f"[VOICE_CLONE] Step 2: Importing to Ultravox...")
        ultravox_result = await ultravox_client.import_voice_from_provider(
            name=name,
            provider="elevenlabs",
            provider_voice_id=elevenlabs_voice_id,
            description=f"Cloned voice: {name}",
        )
        
        # Extract Ultravox voice ID
        ultravox_voice_id = (
            ultravox_result.get("voiceId") or
            ultravox_result.get("id") or
            ultravox_result.get("voice_id") or
            (ultravox_result.get("data", {}) if isinstance(ultravox_result.get("data"), dict) else {}).get("voiceId") or
            (ultravox_result.get("data", {}) if isinstance(ultravox_result.get("data"), dict) else {}).get("id")
        )
        
        if not ultravox_voice_id:
            logger.error(f"[VOICE_CLONE] Ultravox response missing voiceId | response={ultravox_result}")
            raise ProviderError(
                provider="ultravox",
                message="Ultravox response missing voiceId",
                http_status=500,
            )
        
        logger.info(f"[VOICE_CLONE] ✅ Ultravox import successful! | ultravox_voice_id={ultravox_voice_id}")
        
        # Step 3: Save to database
        voice_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        db = DatabaseService(current_user["token"])
        db.set_auth(current_user["token"])
        
        voice_record = {
            "id": voice_id,
            "client_id": client_id,
            "user_id": user_id,
            "name": name,
            "provider": "elevenlabs",
            "type": "custom",  # Cloned voices are "custom"
            "language": "en-US",
            "status": "active",
            "provider_voice_id": elevenlabs_voice_id,
            "ultravox_voice_id": ultravox_voice_id,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        logger.info(f"[VOICE_CLONE] Saving to database...")
        db.insert("voices", voice_record)
        
        logger.info(f"[VOICE_CLONE] ✅ Voice clone created successfully!")
        logger.info(f"[VOICE_CLONE] Summary: ElevenLabs ID={elevenlabs_voice_id} | Ultravox ID={ultravox_voice_id}")
        
        # Log to database
        try:
            await log_to_database(
                source="backend",
                level="INFO",
                category="voice_clone",
                message=f"Voice clone created successfully | name={name} | elevenlabs_id={elevenlabs_voice_id} | ultravox_id={ultravox_voice_id}",
                client_id=client_id,
                user_id=user_id,
            )
        except Exception as log_error:
            logger.warning(f"Database logging failed: {log_error}")
        
        return {
            "data": VoiceResponse(**voice_record),
            "meta": ResponseMeta(request_id=str(uuid.uuid4()), ts=now),
        }
        
    except (ValidationError, ForbiddenError, ProviderError) as e:
        logger.error(f"[VOICE_CLONE] Error: {type(e).__name__} - {str(e)}")
        raise
    except Exception as e:
        logger.error(f"[VOICE_CLONE] Unexpected error: {str(e)}", exc_info=True)
        raise ProviderError(
            provider="system",
            message=f"Voice cloning failed: {str(e)}",
            http_status=500,
        )
