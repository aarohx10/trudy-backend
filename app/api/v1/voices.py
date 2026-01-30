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

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError, ProviderError
from app.models.schemas import VoiceResponse, ResponseMeta
from app.core.config import settings
from app.services.ultravox import ultravox_client
from app.core.db_logging import log_to_database

logger = logging.getLogger(__name__)
router = APIRouter()

# Helper function to log to BOTH console AND database
async def log_both(source: str, level: str, category: str, message: str, **kwargs):
    """Log to both console (stdout/stderr) AND database - ensures visibility even if DB fails"""
    # Always log to console first (this goes to server logs)
    log_func = getattr(logger, level.lower(), logger.info)
    context_str = f" | context={kwargs.get('context', {})}" if kwargs.get('context') else ""
    log_func(f"[{category.upper()}] {message}{context_str}")
    
    # Also log to database (for admin panel)
    try:
        await log_to_database(source=source, level=level, category=category, message=message, **kwargs)
    except Exception as db_log_error:
        # If DB logging fails, at least we have console logs
        logger.warning(f"Database logging failed (but console log succeeded): {db_log_error}")


@router.post("")
async def create_voice(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    # Voice import only - voice cloning has been removed
    name: Optional[str] = Form(None),
    strategy: Optional[str] = Form(None),
    provider: Optional[str] = Form(None),
    provider_voice_id: Optional[str] = Form(None),
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
    
    FormData (multipart/form-data for imports - voice cloning removed):
    - name: Voice name (required)
    - strategy: "external" (import only - voice cloning removed)
    - provider_voice_id: Provider voice ID (required for external)
    - provider: "elevenlabs" (default)
    """
    # CRITICAL: Log immediately when function is called (before any processing)
    logger.info("=" * 80)
    logger.info(f"[VOICES] 🔥 FUNCTION CALLED - create_voice endpoint hit!")
    logger.info(f"[VOICES] Timestamp: {datetime.utcnow().isoformat()}")
    logger.info(f"[VOICES] Request method: {request.method}")
    logger.info(f"[VOICES] Request URL: {request.url}")
    logger.info(f"[VOICES] Content-Type: {request.headers.get('content-type', 'MISSING')}")
    logger.info(f"[VOICES] Content-Length: {request.headers.get('content-length', 'MISSING')}")
    logger.info(f"[VOICES] FastAPI File/Form params received:")
    logger.info(f"[VOICES]   - name: {name}")
    logger.info(f"[VOICES]   - strategy: {strategy}")
    # Files parameter removed - voice cloning has been removed
    logger.info("=" * 80)
    
    try:
        client_id = current_user.get("client_id")
        user_id = current_user.get("user_id")
        request_id = getattr(request.state, "request_id", None)
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        if current_user["role"] not in ["client_admin", "agency_admin"]:
            raise ForbiddenError("Insufficient permissions")
        
        # Determine if JSON or multipart - PRODUCTION APPROACH: Use FastAPI File/Form params
        content_type = request.headers.get("content-type", "")
        is_json = "application/json" in content_type
        is_multipart = "multipart/form-data" in content_type
        
        logger.info(f"[VOICES] Content-Type analysis: is_json={is_json}, is_multipart={is_multipart}")
        logger.info(f"[VOICES] FastAPI params populated: name={name is not None} (files parameter removed - voice cloning removed)")
        
        # PRODUCTION FIX: Use FastAPI's File/Form parameters if multipart, otherwise parse JSON
        # This avoids the request.form() hang issue completely!
        import time
        parse_start = time.time()
        
        if is_multipart and name is not None:
            # FastAPI has already parsed the multipart form via Form parameters
            # Voice cloning removed - no files parameter needed
            logger.info("=" * 80)
            logger.info(f"[VOICES] ===== VOICE CREATION REQUEST START (MULTIPART) =====")
            logger.info(f"[VOICES] ✅ Using FastAPI Form parameters (voice cloning removed - import only)")
            logger.info(f"[VOICES] Name from Form param: {name}")
            logger.info(f"[VOICES] Strategy from Form param: {strategy}")
            logger.info("=" * 80)
            
            await log_both(
                source="backend",
                level="INFO",
                category="voice_import",
                message="✅ Voice creation request received (multipart via FastAPI Form - voice cloning removed)",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                context={
                    "content_type": content_type,
                    "content_length": request.headers.get("content-length", "unknown"),
                    "name": name,
                    "strategy": strategy,
                    "parsing_method": "FastAPI Form params (voice cloning removed)",
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            
            # Use the FastAPI-parsed values directly - NO request.form() needed!
            provider = provider or "elevenlabs"
            # Voice cloning removed - files parameter no longer exists
            # Only import (external strategy) is supported, which uses JSON
            
            await log_both(
                source="backend",
                level="INFO",
                category="voice_import",
                message="✅ Request parsed successfully (via FastAPI Form - INSTANT!)",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                context={
                    "voice_name": name,
                    "strategy": strategy,
                    "provider": provider,
                    "parsing_method": "FastAPI Form (no request.form() delay)",
                },
            )
            logger.info(f"[VOICES] ✅ Request parsed INSTANTLY via FastAPI Form | name={name} | strategy={strategy}")
            # Skip all form parsing below - go straight to validation
        elif is_json:
            # JSON request (for imports)
            await log_both(
                source="backend",
                level="INFO",
                category="voice_cloning",
                message="Parsing JSON request body",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
            )
            logger.info(f"[VOICES] Parsing JSON body...")
            body = await request.json()
            parse_time = time.time() - parse_start
            await log_both(
                source="backend",
                level="INFO",
                category="voice_cloning",
                message="JSON body parsed successfully",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                context={
                    "body_keys": list(body.keys()) if isinstance(body, dict) else "not_dict",
                    "parse_time_seconds": round(parse_time, 2),
                },
            )
            logger.info(f"[VOICES] JSON body received | body_keys={list(body.keys()) if isinstance(body, dict) else 'not_dict'} | parse_time={parse_time:.2f}s")
            name = body.get("name")
            strategy = body.get("strategy")
            source = body.get("source", {})
            provider_overrides = body.get("provider_overrides", {})
            provider = provider_overrides.get("provider", "elevenlabs")
            provider_voice_id = source.get("provider_voice_id")
            # Voice cloning removed - files no longer needed
            
            await log_both(
                source="backend",
                level="INFO",
                category="voice_cloning",
                message="Voice creation request received (JSON)",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                context={
                    "content_type": content_type,
                    "name": name,
                    "strategy": strategy,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        else:
            # Fallback: Try to parse as multipart using request.form() if File/Form params weren't populated
            # This handles edge cases where FastAPI didn't parse the form
            logger.warning(f"[VOICES] WARNING: Content-Type is {content_type} but File/Form params are None")
            logger.warning(f"[VOICES] Falling back to request.form() parsing...")
            
            await log_both(
                source="backend",
                level="WARNING",
                category="voice_cloning",
                message="Falling back to request.form() parsing (File/Form params not populated)",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                context={"content_type": content_type},
            )
            
            import time
            parse_start = time.time()
            # Multipart form data fallback (voice cloning removed - only import supported via JSON)
            await log_both(
                source="backend",
                level="INFO",
                category="voice_cloning",
                message="Parsing multipart form data",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
            )
            logger.info(f"[VOICES] Parsing multipart form data...")
            logger.info(f"[VOICES] Content-Length header: {request.headers.get('content-length', 'unknown')}")
            logger.info(f"[VOICES] Content-Type header: {content_type}")
            logger.info(f"[VOICES] Request client: {request.client}")
            logger.info(f"[VOICES] Starting form() call - this may take time for large files...")
            logger.info(f"[VOICES] PRODUCTION NOTE: request.form() will read entire body into memory before parsing")
            logger.info(f"[VOICES] This is the standard FastAPI approach for multipart/form-data")
            
            try:
                # Add timeout protection - form parsing should not take more than 30 seconds
                # If it does, there's likely a network/streaming issue
                import asyncio
                
                # Add heartbeat logging during form parsing to show we're alive
                async def heartbeat_logger():
                    """Log heartbeat every 5 seconds while waiting for form"""
                    heartbeat_count = 0
                    while True:
                        await asyncio.sleep(5)
                        heartbeat_count += 1
                        elapsed = heartbeat_count * 5
                        logger.info(f"[VOICES] ⏳ HEARTBEAT: Still waiting for form data... ({elapsed}s elapsed)")
                        await log_both(
                            source="backend",
                            level="INFO",
                            category="voice_cloning",
                            message=f"⏳ HEARTBEAT: Still waiting for form data... ({elapsed}s elapsed)",
                            request_id=request_id,
                            client_id=client_id,
                            user_id=user_id,
                            endpoint=str(request.url.path),
                            method=request.method,
                        )
                
                try:
                    # Start heartbeat logger
                    heartbeat_task = asyncio.create_task(heartbeat_logger())
                    try:
                        # Actually parse the form - this reads the entire body
                        # PRODUCTION NOTE: request.form() reads entire body into memory before parsing
                        # For very large files, consider streaming approach, but 1.8MB should be fine
                        form = await asyncio.wait_for(request.form(), timeout=30.0)
                    finally:
                        # Stop heartbeat
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass
                except asyncio.TimeoutError:
                    await log_both(
                        source="backend",
                        level="ERROR",
                        category="voice_cloning",
                        message="Form parsing timed out after 30 seconds",
                        request_id=request_id,
                        client_id=client_id,
                        user_id=user_id,
                        endpoint=str(request.url.path),
                        method=request.method,
                        context={
                            "timeout_seconds": 30,
                            "content_length": request.headers.get('content-length', 'unknown'),
                        },
                    )
                    logger.error(f"[VOICES] Form parsing TIMEOUT after 30s | content_length={request.headers.get('content-length', 'unknown')}")
                    raise ValidationError("Form data upload timed out after 30 seconds. The file may be too large or the connection is too slow.")
                
                parse_time = time.time() - parse_start
                await log_both(
                    source="backend",
                    level="INFO",
                    category="voice_cloning",
                    message="Form data parsed successfully",
                    request_id=request_id,
                    client_id=client_id,
                    user_id=user_id,
                    endpoint=str(request.url.path),
                    method=request.method,
                    context={
                        "parse_time_seconds": round(parse_time, 2),
                        "content_length": request.headers.get('content-length', 'unknown'),
                    },
                )
                logger.info(f"[VOICES] Form data parsed | parse_time={parse_time:.2f}s | content_length={request.headers.get('content-length', 'unknown')}")
                logger.info(f"[VOICES] ===== FORM PARSING COMPLETE - PROCEEDING TO FIELD EXTRACTION =====")
            except Exception as form_error:
                import traceback
                error_traceback = traceback.format_exc()
                await log_both(
                    source="backend",
                    level="ERROR",
                    category="voice_cloning",
                    message=f"Failed to parse form data: {str(form_error)}",
                    request_id=request_id,
                    client_id=client_id,
                    user_id=user_id,
                    endpoint=str(request.url.path),
                    method=request.method,
                    error_details={
                        "error_type": type(form_error).__name__,
                        "error_message": str(form_error),
                        "traceback": error_traceback,
                    },
                )
                logger.error(f"[VOICES] Failed to parse form data | error={str(form_error)} | type={type(form_error).__name__}")
                logger.error(f"[VOICES] Form parse traceback: {error_traceback}")
                raise ValidationError(f"Failed to parse form data: {str(form_error)}")
            
            name = form.get("name")
            strategy = form.get("strategy")
            provider = form.get("provider", "elevenlabs")
            provider_voice_id = form.get("provider_voice_id")
            
            # Voice cloning removed - file extraction no longer needed
            # Only import (external strategy) is supported, which uses JSON, not multipart
            files = []
            
            await log_both(
                source="backend",
                level="INFO",
                category="voice_import",
                message="Form fields extracted successfully (voice cloning removed - no file processing needed)",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                context={
                    "voice_name": name,
                    "strategy": strategy,
                    "provider": provider,
                },
            )
            logger.info(f"[VOICES] Form fields extracted | name={name} | strategy={strategy}")
        
        # This section only runs if we used request.form() fallback (not FastAPI Form)
        # If we used FastAPI Form, we already logged above and skipped here
        # Voice cloning removed - no file processing needed
        if not (is_multipart and name is not None):
            await log_both(
                source="backend",
                level="INFO",
                category="voice_cloning",
                message="Request parsed successfully",
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                context={
                    "voice_name": name,
                    "strategy": strategy,
                    "provider": provider,
                    "has_provider_voice_id": bool(provider_voice_id),
                },
            )
            logger.info(f"[VOICES] Parsed request | name={name} | strategy={strategy} | provider={provider} | provider_voice_id={provider_voice_id}")
        
        if not name:
            raise ValidationError("Voice name is required")
        if not strategy:
            raise ValidationError("Strategy is required (external only - voice cloning has been removed)")
        
        # REJECT native strategy - voice cloning has been completely removed
        if strategy == "native":
            raise ValidationError("Voice cloning (native strategy) has been removed. Please use voice import (external strategy) instead.")
        
        # Only external (import) strategy is supported now
        if strategy != "external":
            raise ValidationError("Only 'external' strategy is supported. Voice cloning has been removed.")
        
        voice_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        # Import voice from provider (ONLY supported operation now)
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
            # CRITICAL: Use clerk_org_id for organization-first approach
            clerk_org_id = current_user.get("clerk_org_id")
            if not clerk_org_id:
                raise ValidationError("Missing organization ID in token")
            
            # Initialize database service with org_id context
            db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
            db.set_auth(current_user["token"])
            
            voice_record = {
                "id": voice_id,
                "client_id": client_id,  # Legacy field
                "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
                "user_id": user_id,  # Track which user created the voice
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
    
    except (ValidationError, ForbiddenError, NotFoundError, ProviderError) as e:
        # Re-raise known errors as-is, but log them first
        import traceback
        error_traceback = traceback.format_exc()
        
        # Get request context if available
        request_id = getattr(request.state, "request_id", None) if 'request' in locals() else None
        client_id = current_user.get("client_id") if 'current_user' in locals() else None
        user_id = current_user.get("user_id") if 'current_user' in locals() else None
        endpoint = str(request.url.path) if 'request' in locals() else None
        method = request.method if 'request' in locals() else None
        
        await log_to_database(
            source="backend",
            level="ERROR",
            category="voice_cloning",
            message=f"Error in voice creation: {type(e).__name__} - {str(e)}",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=endpoint,
            method=method,
            error_details={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_details": e.details if hasattr(e, 'details') else None,
                "traceback": error_traceback,
            },
        )
        
        logger.error("=" * 80)
        logger.error(f"[VOICES] ===== ERROR IN VOICE CREATION =====")
        logger.error(f"[VOICES] Error type: {type(e).__name__}")
        logger.error(f"[VOICES] Error message: {str(e)}")
        if hasattr(e, 'details'):
            logger.error(f"[VOICES] Error details: {e.details}")
        logger.error(f"[VOICES] ===== END ERROR =====")
        logger.error("=" * 80)
        raise
    except Exception as e:
        # Catch any unexpected errors and log them
        import traceback
        error_traceback = traceback.format_exc()
        
        # Get request context if available
        request_id = getattr(request.state, "request_id", None) if 'request' in locals() else None
        client_id = current_user.get("client_id") if 'current_user' in locals() else None
        user_id = current_user.get("user_id") if 'current_user' in locals() else None
        endpoint = str(request.url.path) if 'request' in locals() else None
        method = request.method if 'request' in locals() else None
        
        await log_to_database(
            source="backend",
            level="ERROR",
            category="voice_cloning",
            message=f"Unexpected error in voice creation: {type(e).__name__} - {str(e)}",
            request_id=request_id,
            client_id=client_id,
            user_id=user_id,
            endpoint=endpoint,
            method=method,
            error_details={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": error_traceback,
            },
        )
        
        logger.error("=" * 80)
        logger.error(f"[VOICES] ===== UNEXPECTED ERROR IN VOICE CREATION =====")
        logger.error(f"[VOICES] Error type: {type(e).__name__}")
        logger.error(f"[VOICES] Error message: {str(e)}")
        logger.error(f"[VOICES] Full traceback:")
        logger.error(error_traceback)
        logger.error(f"[VOICES] ===== END UNEXPECTED ERROR =====")
        logger.error("=" * 80)
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
        # Initialize database service with org_id context
        db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
        db.set_auth(current_user["token"])
        
        # Get all custom voices (type: "reference" for imported, type: "custom" for cloned)
        # CRITICAL: Filter by org_id instead of client_id - shows all organization voices
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
    """Get single voice - from DB (filtered by org_id)"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, org_id=clerk_org_id)
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
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    voice = db.get_voice(voice_id, org_id=clerk_org_id)
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
    db.update("voices", {"id": voice_id, "clerk_org_id": clerk_org_id}, update_data)
    
    updated_voice = db.get_voice(voice_id, org_id=clerk_org_id)
    
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
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
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
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Preview voice - from Ultravox. ALWAYS uses ultravox_voice_id, never provider_voice_id or local voice_id."""
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key not configured")
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    # Get voice from DB - this is required for custom voices (cloned/imported)
    voice = None
    try:
        # Filter by org_id via context
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
