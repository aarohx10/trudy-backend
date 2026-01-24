"""
Ultravox API Client + ElevenLabs Voice Cloning
"""
import httpx
import logging
from typing import Dict, Any, Optional, List
from app.core.config import settings
from app.core.retry import retry_with_backoff
from app.core.exceptions import ProviderError

logger = logging.getLogger(__name__)


class ElevenLabsClient:
    """Client for ElevenLabs API - Voice Cloning"""
    
    def __init__(self):
        self.base_url = "https://api.elevenlabs.io/v1"
        self.api_key = settings.ELEVENLABS_API_KEY
        if not self.api_key:
            logger.warning("⚠️  ELEVENLABS_API_KEY is not set. Voice cloning will be disabled.")
        else:
            logger.info("✅ ElevenLabs client initialized")
    
    async def clone_voice(
        self,
        name: str,
        audio_files: List[bytes],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Clone a voice using ElevenLabs API.
        
        Args:
            name: Name for the cloned voice
            audio_files: List of audio file bytes (WAV, MP3, etc.)
            description: Optional description for the voice
        
        Returns:
            Dict with voice_id and other details from ElevenLabs
        """
        if not self.api_key:
            raise ProviderError(
                provider="elevenlabs",
                message="ElevenLabs API key is not configured",
                http_status=500,
            )
        
        url = f"{self.base_url}/voices/add"
        logger.info(f"[ELEVENLABS] Creating voice clone | name={name} | files_count={len(audio_files)}")
        
        async def _make_request():
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Build multipart form data
                files = []
                for i, audio_bytes in enumerate(audio_files):
                    files.append(("files", (f"sample_{i}.mp3", audio_bytes, "audio/mpeg")))
                
                data = {"name": name}
                if description:
                    data["description"] = description
                
                response = await client.post(
                    url,
                    headers={"xi-api-key": self.api_key},
                    data=data,
                    files=files,
                )
                
                logger.debug(f"[ELEVENLABS] Response | status_code={response.status_code}")
                if response.status_code >= 400:
                    error_text = response.text[:500] if response.text else "No response body"
                    logger.error(f"[ELEVENLABS] Error | status={response.status_code} | response={error_text}")
                response.raise_for_status()
                return response.json()
        
        try:
            result = await retry_with_backoff(_make_request)
            voice_id = result.get("voice_id")
            logger.info(f"[ELEVENLABS] Voice clone created | voice_id={voice_id} | name={name}")
            return result
        except httpx.HTTPStatusError as e:
            error_detail = "Unknown error"
            try:
                error_body = e.response.json()
                error_detail = error_body.get("detail", {}).get("message", str(e)) if isinstance(error_body.get("detail"), dict) else str(error_body.get("detail", e))
            except:
                error_detail = e.response.text[:200] if e.response.text else str(e)
            
            logger.error(f"[ELEVENLABS] Clone failed | status={e.response.status_code} | error={error_detail}")
            raise ProviderError(
                provider="elevenlabs",
                message=f"ElevenLabs voice cloning failed: {error_detail}",
                http_status=e.response.status_code,
            )
        except httpx.RequestError as e:
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "provider": "elevenlabs",
                "operation": "voice_clone",
                "name": name,
            }
            logger.error(f"[ELEVENLABS] Request error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            raise ProviderError(
                provider="elevenlabs",
                message=f"ElevenLabs API request failed: {e}",
                http_status=502,
            )
    
    async def get_voice(self, voice_id: str) -> Dict[str, Any]:
        """Get voice details from ElevenLabs"""
        if not self.api_key:
            raise ProviderError(
                provider="elevenlabs",
                message="ElevenLabs API key is not configured",
                http_status=500,
            )
        
        url = f"{self.base_url}/voices/{voice_id}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers={"xi-api-key": self.api_key},
            )
            response.raise_for_status()
            return response.json()
    
    async def delete_voice(self, voice_id: str) -> bool:
        """Delete a voice from ElevenLabs"""
        if not self.api_key:
            raise ProviderError(
                provider="elevenlabs",
                message="ElevenLabs API key is not configured",
                http_status=500,
            )
        
        url = f"{self.base_url}/voices/{voice_id}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                url,
                headers={"xi-api-key": self.api_key},
            )
            if response.status_code == 200:
                logger.info(f"[ELEVENLABS] Voice deleted | voice_id={voice_id}")
                return True
            logger.warning(f"[ELEVENLABS] Delete failed | voice_id={voice_id} | status={response.status_code}")
            return False


# Global ElevenLabs client instance
elevenlabs_client = ElevenLabsClient()


class UltravoxClient:
    """Client for Ultravox API"""
    
    def __init__(self):
        # Normalize base URL: remove trailing /v1 if present (endpoints include /api prefix)
        base_url = settings.ULTRAVOX_BASE_URL.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]  # Remove /v1 suffix
            logger.warning(f"⚠️  ULTRAVOX_BASE_URL contained /v1 suffix, normalized to: {base_url}")
        self.base_url = base_url
        self.api_key = settings.ULTRAVOX_API_KEY
        if not self.api_key:
            logger.warning("⚠️  ULTRAVOX_API_KEY is not set. Ultravox features will be disabled.")
            logger.warning("⚠️  Please set ULTRAVOX_API_KEY in your .env file to enable voice and agent syncing.")
        else:
            logger.info(f"✅ Ultravox client initialized with base URL: {self.base_url}")
        self.headers = {
            "X-API-Key": self.api_key if self.api_key else "",
            "Content-Type": "application/json",
        }
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request with retry logic"""
        if not self.api_key:
            raise ProviderError(
                provider="ultravox",
                message="Ultravox API key is not configured",
                http_status=500,
            )
        
        url = f"{self.base_url}{endpoint}"
        logger.info(f"[ULTRAVOX] Making request | method={method} | url={url} | params={params}")
        if data:
            logger.debug(f"[ULTRAVOX] Request Data: {data}")
        
        async def _make_request():
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.request(
                    method,
                    url,
                    json=data,
                    params=params,
                    headers=self.headers,
                )
                logger.debug(f"[ULTRAVOX] Response received | status_code={response.status_code} | url={url}")
                if response.status_code >= 400:
                    # Log full error details for debugging
                    error_text = response.text[:500] if response.text else "No response body"
                    logger.error(f"[ULTRAVOX] Error Response | status={response.status_code} | url={url} | response_preview={error_text}")
                response.raise_for_status()
                return response.json()
        
        try:
            return await retry_with_backoff(_make_request)
        except httpx.HTTPStatusError as e:
            import traceback
            import json
            # Get error details from response if available
            error_detail = "Unknown error"
            error_details = {}
            try:
                # Try to parse as JSON first
                error_body = e.response.json()
                if isinstance(error_body, dict):
                    error_detail = error_body.get("error", {}).get("message", str(e)) if isinstance(error_body.get("error"), dict) else str(e)
                    # Include full error response in details
                    error_details = error_body
                else:
                    error_detail = str(e)
                    error_details = {"raw_response": str(error_body)}
            except Exception as parse_error:
                # Not JSON, likely HTML error page (like 404 Not Found page)
                error_text = e.response.text[:1000] if e.response.text else "No response body"
                error_detail = f"HTTP {e.response.status_code}: {error_text[:200]}"
                error_details = {
                    "status_code": e.response.status_code,
                    "response_body": error_text,
                    "request_url": str(e.request.url),
                    "method": e.request.method,
                    "parse_error": str(parse_error),
                }
            
            # Log RAW error with full details
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
                "http_status_code": e.response.status_code,
                "http_status_text": e.response.reason_phrase if hasattr(e.response, 'reason_phrase') else None,
                "response_headers": dict(e.response.headers) if hasattr(e.response, 'headers') else None,
                "response_text": e.response.text[:2000] if e.response.text else None,
                "response_text_full": e.response.text if e.response.text else None,
                "request_url": str(e.request.url) if hasattr(e, 'request') else url,
                "request_method": e.request.method if hasattr(e, 'request') else method,
                "request_headers": dict(e.request.headers) if hasattr(e, 'request') and hasattr(e.request, 'headers') else None,
                "error_detail": error_detail,
                "error_details": error_details,
                "full_traceback": traceback.format_exc(),
                "url": url,
                "method": method,
            }
            logger.error(f"[ULTRAVOX] HTTP Status Error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            
            raise ProviderError(
                provider="ultravox",
                message=f"Ultravox API error: {e.response.status_code} - {error_detail[:200]}",
                http_status=e.response.status_code,
                retry_after=int(e.response.headers.get("Retry-After", 0)) if e.response.status_code == 429 else None,
                details=error_details,
            )
        except httpx.RequestError as e:
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
                "request_url": url,
                "request_method": method,
            }
            logger.error(f"[ULTRAVOX] Request Error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            
            raise ProviderError(
                provider="ultravox",
                message=f"Ultravox API request failed: {e}",
                http_status=502,
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "request_url": url,
                    "method": method,
                    "raw_error": error_details_raw,
                },
            )
    
    # Voices
    async def list_voices(self, ownership: Optional[str] = None, provider: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """List all voices from Ultravox with provider-specific IDs"""
        params = {}
        if ownership:
            params["ownership"] = ownership
        if provider:
            params["provider"] = provider
        
        # Use /api/voices endpoint per Ultravox API documentation
        # Full URL should be: https://api.ultravox.ai/api/voices
        response = await self._request("GET", "/api/voices", params=params)
        
        voices = response.get("results", [])  # Fixed: use "results" not "data" per Ultravox API docs
        
        # Extract provider_voice_id from definition object (per Ultravox API structure)
        processed_count = 0
        for voice in voices:
            definition = voice.get("definition", {})
            provider_name = voice.get("provider", "").lower()
            
            # Extract provider_voice_id based on provider type from definition
            if "elevenLabs" in definition:
                voice["provider_voice_id"] = definition["elevenLabs"].get("voiceId")
                voice["provider"] = "elevenlabs"
            elif "cartesia" in definition:
                voice["provider_voice_id"] = definition["cartesia"].get("voiceId")
                voice["provider"] = "cartesia"
            elif "lmnt" in definition:
                voice["provider_voice_id"] = definition["lmnt"].get("voiceId")
                voice["provider"] = "lmnt"
            elif "google" in definition:
                voice["provider_voice_id"] = definition["google"].get("voiceId")
                voice["provider"] = "google"
            elif "generic" in definition:
                # Generic voices don't have a provider_voice_id
                voice["provider"] = "generic"
            else:
                # Fallback to provider field if no definition match
                if not voice.get("provider"):
                    voice["provider"] = provider_name or "unknown"
            processed_count += 1
        
        return voices
    
    def reconcile_resource(
        self,
        local_record: Dict[str, Any],
        ultravox_object: Dict[str, Any],
        resource_type: str = "voice",
    ) -> Dict[str, Any]:
        """
        Reconcile a local DB record with a live Ultravox object to detect drift.
        
        Returns a dict with:
        - has_drift: bool indicating if there are differences
        - drift_details: dict describing what differs
        - recommended_action: str suggesting what to do
        """
        drift = {
            "has_drift": False,
            "drift_details": {},
            "recommended_action": "no_action",
        }
        
        if resource_type == "voice":
            # Check status drift
            local_status = local_record.get("status", "").lower()
            ultravox_status = ultravox_object.get("status", "").lower()
            
            status_mapping = {
                "training": "training",
                "active": "active",
                "ready": "active",
                "completed": "active",
                "failed": "failed",
                "error": "failed",
            }
            mapped_ultravox_status = status_mapping.get(ultravox_status, local_status)
            
            if mapped_ultravox_status != local_status:
                drift["has_drift"] = True
                drift["drift_details"]["status"] = {
                    "local": local_status,
                    "ultravox": ultravox_status,
                    "mapped": mapped_ultravox_status,
                }
                drift["recommended_action"] = "update_status"
            
            # Check if Ultravox ID is missing locally
            if not local_record.get("ultravox_voice_id") and ultravox_object.get("id"):
                drift["has_drift"] = True
                drift["drift_details"]["missing_ultravox_id"] = ultravox_object.get("id")
                drift["recommended_action"] = "sync_ultravox_id"
        
        elif resource_type == "agent":
            # Check status drift
            local_status = local_record.get("status", "").lower()
            ultravox_status = ultravox_object.get("status", "").lower()
            
            if ultravox_status in ["active", "ready"] and local_status != "active":
                drift["has_drift"] = True
                drift["drift_details"]["status"] = {
                    "local": local_status,
                    "ultravox": ultravox_status,
                }
                drift["recommended_action"] = "update_status"
            
            # Check if Ultravox ID is missing locally
            if not local_record.get("ultravox_agent_id") and ultravox_object.get("id"):
                drift["has_drift"] = True
                drift["drift_details"]["missing_ultravox_id"] = ultravox_object.get("id")
                drift["recommended_action"] = "sync_ultravox_id"
        
        return drift
    
    async def create_voice(self, voice_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create voice in Ultravox (legacy - use import_voice_from_provider instead)"""
        response = await self._request("POST", "/api/voices", data=voice_data)
        return response
    
    async def import_voice_from_provider(
        self,
        name: str,
        provider: str,
        provider_voice_id: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Import a voice from an external provider into Ultravox.
        
        This uses the correct Ultravox API format with provider-specific definitions.
        Voice names are lowercased with underscores (no spaces).
        
        Args:
            name: Display name for the voice
            provider: Provider name (elevenlabs, cartesia, lmnt, google)
            provider_voice_id: The voice ID from the provider
            description: Optional description
        
        Returns:
            Ultravox voice response with voiceId
        """
        # Normalize name: lowercase, replace spaces with underscores
        normalized_name = name.lower().replace(" ", "_").replace("-", "_")
        # Remove any non-alphanumeric characters except underscores
        normalized_name = "".join(c if c.isalnum() or c == "_" else "" for c in normalized_name)
        
        # Append provider voice ID to ensure unique names (prevents conflicts)
        # Format: name_provider_voice_id
        normalized_name = f"{normalized_name}_{provider_voice_id}"
        
        logger.info(f"[ULTRAVOX] Importing voice from provider | name={normalized_name} | provider={provider} | provider_voice_id={provider_voice_id}")
        
        # Build provider-specific definition
        voice_data: Dict[str, Any] = {
            "name": normalized_name,
        }
        
        if description:
            voice_data["description"] = description
        
        # Provider-specific definitions per Ultravox API
        if provider == "elevenlabs":
            voice_data["definition"] = {
                "elevenLabs": {
                    "voiceId": provider_voice_id,
                    "model": "eleven_multilingual_v2",
                    "stability": 0.5,
                    "similarityBoost": 0.75,
                    "style": 0.0,
                    "useSpeakerBoost": True,
                    "speed": 1.0,
                }
            }
        elif provider == "cartesia":
            voice_data["definition"] = {
                "cartesia": {
                    "voiceId": provider_voice_id,
                    "model": "sonic-english",
                    "generationConfig": {
                        "speed": 1.0,
                        "emotion": "positivity:high",
                    }
                }
            }
        elif provider == "lmnt":
            voice_data["definition"] = {
                "lmnt": {
                    "voiceId": provider_voice_id,
                    "model": "lily",
                    "speed": 1.0,
                    "conversational": True,
                }
            }
        elif provider == "google":
            voice_data["definition"] = {
                "google": {
                    "voiceId": provider_voice_id,
                    "speakingRate": 1.0,
                }
            }
        else:
            raise ProviderError(
                provider="ultravox",
                message=f"Unsupported provider: {provider}. Supported: elevenlabs, cartesia, lmnt, google",
                http_status=400,
            )
        
        logger.debug(f"[ULTRAVOX] Voice import payload: {voice_data}")
        
        response = await self._request("POST", "/api/voices", data=voice_data)
        
        ultravox_voice_id = response.get("voiceId") or response.get("id")
        logger.info(f"[ULTRAVOX] Voice imported successfully | ultravox_voice_id={ultravox_voice_id} | provider={provider}")
        
        return response
    
    async def get_voice(self, voice_id: str) -> Dict[str, Any]:
        """Get voice from Ultravox"""
        response = await self._request("GET", f"/api/voices/{voice_id}")
        return response
    
    async def get_voice_preview(self, voice_id: str) -> bytes:
        """Get voice preview audio from Ultravox - returns raw audio bytes (audio/wav)"""
        if not self.api_key:
            raise ProviderError(
                provider="ultravox",
                message="Ultravox API key is not configured",
                http_status=500,
            )
        
        url = f"{self.base_url}/api/voices/{voice_id}/preview"
        logger.info(f"[ULTRAVOX] Getting voice preview | voice_id={voice_id} | url={url}")
        
        async def _make_request():
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={
                        "X-API-Key": self.api_key,
                    },
                )
                logger.debug(f"[ULTRAVOX] Preview response received | status_code={response.status_code} | url={url}")
                if response.status_code >= 400:
                    error_text = response.text[:500] if response.text else "No response body"
                    logger.error(f"[ULTRAVOX] Preview Error Response | status={response.status_code} | url={url} | response_preview={error_text}")
                response.raise_for_status()
                return response.content  # Return raw bytes, not JSON
        
        try:
            return await retry_with_backoff(_make_request)
        except httpx.HTTPStatusError as e:
            error_detail = "Unknown error"
            error_details = {}
            try:
                error_body = e.response.json()
                if isinstance(error_body, dict):
                    error_detail = error_body.get("error", {}).get("message", str(e)) if isinstance(error_body.get("error"), dict) else str(e)
                    error_details = error_body
                else:
                    error_detail = str(e)
                    error_details = {"raw_response": str(error_body)}
            except:
                error_text = e.response.text[:1000] if e.response.text else "No response body"
                error_detail = f"HTTP {e.response.status_code}: {error_text[:200]}"
                error_details = {
                    "status_code": e.response.status_code,
                    "response_body": error_text,
                    "request_url": str(e.request.url),
                    "method": e.request.method,
                }
            
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
                "http_status_code": e.response.status_code,
                "http_status_text": e.response.reason_phrase if hasattr(e.response, 'reason_phrase') else None,
                "response_headers": dict(e.response.headers) if hasattr(e.response, 'headers') else None,
                "response_text": e.response.text[:2000] if e.response.text else None,
                "request_url": str(e.request.url) if hasattr(e, 'request') else url,
                "request_method": e.request.method if hasattr(e, 'request') else "GET",
                "error_detail": error_detail,
                "error_details": error_details,
                "full_traceback": traceback.format_exc(),
                "url": url,
                "operation": "preview_voice",
            }
            logger.error(f"[ULTRAVOX] Preview HTTP Status Error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            raise ProviderError(
                provider="ultravox",
                message=f"Ultravox API error: {e.response.status_code} - {error_detail[:200]}",
                http_status=e.response.status_code,
                retry_after=int(e.response.headers.get("Retry-After", 0)) if e.response.status_code == 429 else None,
                details=error_details,
            )
        except httpx.RequestError as e:
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
                "full_traceback": traceback.format_exc(),
                "request_url": url,
                "request_method": "GET",
                "operation": "preview_voice",
            }
            logger.error(f"[ULTRAVOX] Preview Request Error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            raise ProviderError(
                provider="ultravox",
                message=f"Ultravox API request failed: {e}",
                http_status=502,
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "request_url": url,
                    "method": "GET",
                },
            )
    
    # Agents
    async def create_agent(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create agent in Ultravox"""
        response = await self._request("POST", "/api/agents", data=agent_data)
        return response
    
    async def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """Get agent from Ultravox"""
        response = await self._request("GET", f"/api/agents/{agent_id}")
        return response
    
    async def update_agent(self, agent_id: str, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update agent in Ultravox"""
        response = await self._request("PATCH", f"/api/agents/{agent_id}", data=agent_data)
        return response
    
    async def delete_agent(self, agent_id: str, force: bool = False) -> Dict[str, Any]:
        """Delete agent from Ultravox"""
        params = {"force": "true"} if force else {}
        response = await self._request("DELETE", f"/api/agents/{agent_id}", params=params)
        return response
    
    # Knowledge Bases (Corpora)
    async def create_corpus(self, corpus_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create corpus in Ultravox"""
        response = await self._request("POST", "/api/corpora", data=corpus_data)
        return response
    
    async def add_corpus_source(self, corpus_id: str, source_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add source to corpus"""
        response = await self._request("POST", f"/api/corpora/{corpus_id}/sources", data=source_data)
        return response
    
    # Calls
    async def create_call(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create call in Ultravox.
        Automatically includes webhook callback if WEBHOOK_BASE_URL is configured.
        """
        # Add webhook callback if configured
        if settings.WEBHOOK_BASE_URL and settings.ULTRAVOX_WEBHOOK_SECRET:
            webhook_url = f"{settings.WEBHOOK_BASE_URL}/api/v1/webhooks/ultravox"
            if "callbacks" not in call_data:
                call_data["callbacks"] = {}
            if "ended" not in call_data["callbacks"]:
                call_data["callbacks"]["ended"] = {
                    "url": webhook_url,
                    "secrets": [settings.ULTRAVOX_WEBHOOK_SECRET],
                }
        
        response = await self._request("POST", "/api/calls", data=call_data)
        return response
    
    async def create_test_call(
        self,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a WebRTC test call for agent testing.
        Returns joinUrl for browser WebRTC session initialization.
        """
        call_data = {
            "agentId": agent_id,
            "medium": {"webRtc": {}},
            "context": {
                **(context or {}),
                "is_test": True,  # Flag to bypass billing logic
            },
        }
        response = await self._request("POST", "/api/calls", data=call_data)
        return response
    
    async def get_call(self, call_id: str) -> Dict[str, Any]:
        """Get call from Ultravox"""
        response = await self._request("GET", f"/api/calls/{call_id}")
        return response
    
    async def get_call_transcript(self, call_id: str) -> List[Dict[str, Any]]:
        """Get call messages/transcript"""
        response = await self._request("GET", f"/api/calls/{call_id}/messages")
        return response.get("results", [])
    
    async def get_call_recording(self, call_id: str) -> str:
        """Get call recording URL"""
        response = await self._request("GET", f"/api/calls/{call_id}/recording")
        return response.get("recordingUrl", "")
    
    # Campaigns (Batches)
    async def create_scheduled_batch(
        self,
        agent_id: str,
        batch_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Create scheduled batch for campaign.
        Note: Batch status updates come via webhooks, not callbacks.
        Ensure webhook is registered via ensure_webhook_registration().
        """
        response = await self._request(
            "POST",
            f"/api/agents/{agent_id}/scheduled-batches",
            data=batch_data,
        )
        return response
    
    async def get_batch(self, agent_id: str, batch_id: str) -> Dict[str, Any]:
        """
        Get batch status from Ultravox.
        Requires agent_id as batches are scoped to agents.
        """
        # Ultravox API structure: /api/agents/{agent_id}/scheduled-batches/{batch_id}
        response = await self._request("GET", f"/api/agents/{agent_id}/scheduled-batches/{batch_id}")
        return response
    
    # Webhooks
    async def list_webhooks(self) -> List[Dict[str, Any]]:
        """List all webhooks configured in Ultravox"""
        response = await self._request("GET", "/api/webhooks")
        return response.get("results", [])
    
    async def create_webhook(
        self,
        url: str,
        events: List[str],
        secrets: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a webhook in Ultravox"""
        webhook_data = {
            "url": url,
            "events": events,
        }
        if secrets:
            webhook_data["secrets"] = secrets
        if agent_id:
            webhook_data["agentId"] = agent_id
        
        response = await self._request("POST", "/api/webhooks", data=webhook_data)
        return response
    
    async def update_webhook(
        self,
        webhook_id: str,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        secrets: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update a webhook in Ultravox"""
        webhook_data = {}
        if url is not None:
            webhook_data["url"] = url
        if events is not None:
            webhook_data["events"] = events
        if secrets is not None:
            webhook_data["secrets"] = secrets
        
        response = await self._request("PATCH", f"/api/webhooks/{webhook_id}", data=webhook_data)
        return response
    
    async def delete_webhook(self, webhook_id: str) -> None:
        """Delete a webhook from Ultravox"""
        await self._request("DELETE", f"/api/webhooks/{webhook_id}")
    
    async def ensure_webhook_registration(self) -> Optional[str]:
        """
        Ensure webhook is registered with Ultravox.
        Checks if webhook URL is already registered, creates if not.
        Returns webhook_id if successful, None otherwise.
        """
        if not settings.WEBHOOK_BASE_URL:
            logger.warning("WEBHOOK_BASE_URL not configured, skipping webhook registration")
            return None
        
        if not settings.ULTRAVOX_WEBHOOK_SECRET:
            logger.warning("ULTRAVOX_WEBHOOK_SECRET not configured, skipping webhook registration")
            return None
        
        webhook_url = f"{settings.WEBHOOK_BASE_URL}/api/v1/webhooks/ultravox"
        # Valid Ultravox webhook events per API documentation
        required_events = [
            "call.started",   # Fired when a call starts
            "call.joined",    # Fired when a call is joined
            "call.ended",     # Fired when a call ends
            "call.billed",    # Fired when a call is billed
        ]
        
        try:
            # List existing webhooks
            existing_webhooks = await self.list_webhooks()
            
            # Check if our webhook URL is already registered
            for webhook in existing_webhooks:
                if webhook.get("url") == webhook_url:
                    webhook_id = webhook.get("webhookId") or webhook.get("id")
                    existing_events = webhook.get("events", [])
                    
                    # Check if all required events are subscribed
                    missing_events = [e for e in required_events if e not in existing_events]
                    if missing_events:
                        # Update webhook to include missing events
                        logger.info(f"Updating webhook {webhook_id} to include missing events: {missing_events}")
                        await self.update_webhook(
                            webhook_id,
                            events=list(set(existing_events + required_events)),
                            secrets=[settings.ULTRAVOX_WEBHOOK_SECRET],
                        )
                    else:
                        logger.info(f"Webhook {webhook_id} already registered with all required events")
                    
                    return webhook_id
            
            # Webhook not found, create it
            logger.info(f"Registering webhook with Ultravox: {webhook_url}")
            webhook_response = await self.create_webhook(
                url=webhook_url,
                events=required_events,
                secrets=[settings.ULTRAVOX_WEBHOOK_SECRET],
            )
            
            webhook_id = webhook_response.get("webhookId") or webhook_response.get("id")
            if webhook_id:
                logger.info(f"✅ Webhook registered successfully: {webhook_id}")
            else:
                logger.warning("Webhook created but no ID returned")
            
            return webhook_id
            
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
                "webhook_url": webhook_url,
                "operation": "register_webhook",
            }
            logger.error(f"[ULTRAVOX] Failed to register webhook (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            return None
    
    # Tools
    async def list_tools(self, ownership: str = "private") -> Dict[str, Any]:
        """List tools from Ultravox with ownership filter"""
        params = {"ownership": ownership} if ownership else {}
        response = await self._request("GET", "/api/tools", params=params)
        return response
    
    async def get_tool(self, tool_id: str) -> Dict[str, Any]:
        """Get tool from Ultravox"""
        response = await self._request("GET", f"/api/tools/{tool_id}")
        return response
    
    async def create_tool(self, tool_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create tool in Ultravox"""
        response = await self._request("POST", "/api/tools", data=tool_data)
        return response
    
    async def create_durable_tool(
        self,
        name: str,
        description: str,
        endpoint: str,
        http_method: str = "POST",
        dynamic_parameters: Optional[List[Dict[str, Any]]] = None,
        static_parameters: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Create a durable tool in Ultravox with static parameters support.
        Used for Knowledge Base proxy tools where kb_id is hidden from the LLM.
        Includes httpSecurityOptions to require X-Tool-Secret header authentication.
        """
        tool_data = {
            "name": name,
            "definition": {
                "modelToolName": name,
                "description": description,
                "dynamicParameters": dynamic_parameters or [],
                "staticParameters": static_parameters or [],
                "http": {
                    "baseUrlPattern": endpoint,
                    "httpMethod": http_method,
                },
                "requirements": {
                    "httpSecurityOptions": {
                        "options": [
                            {
                                "requirements": {
                                    "toolSecret": {
                                        "headerApiKey": {"name": "X-Tool-Secret"}
                                    }
                                }
                            }
                        ]
                    }
                },
                "precomputable": True,  # Knowledge base queries are non-mutating
            },
        }
        
        response = await self._request("POST", "/api/tools", data=tool_data)
        return response
    
    async def update_tool(self, tool_id: str, tool_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update tool in Ultravox (full definition replacement)"""
        response = await self._request("PUT", f"/api/tools/{tool_id}", data=tool_data)
        return response
    
    async def test_tool(self, tool_id: str, test_data: Dict[str, Any]) -> Dict[str, Any]:
        """Test tool in Ultravox"""
        response = await self._request("POST", f"/api/tools/{tool_id}/test", data=test_data)
        return response
    
    async def delete_tool(self, tool_id: str) -> None:
        """Delete tool from Ultravox"""
        await self._request("DELETE", f"/api/tools/{tool_id}")
    
    # TTS API Keys
    async def update_tts_api_key(self, provider: str, api_key_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update TTS API key for provider"""
        response = await self._request(
            "PATCH",
            "/api/accounts/me/tts_api_keys",
            data={"provider": provider, **api_key_data},
        )
        return response
    
    # SIP/Telephony
    async def get_sip_config(self) -> Dict[str, Any]:
        """Get SIP configuration from Ultravox"""
        response = await self._request("GET", "/api/sip")
        return response


# Global client instances
ultravox_client = UltravoxClient()

