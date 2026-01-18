"""
Ultravox API Client
"""
import httpx
import logging
from typing import Dict, Any, Optional, List
from app.core.config import settings
from app.core.retry import retry_with_backoff
from app.core.exceptions import ProviderError

logger = logging.getLogger(__name__)


class UltravoxClient:
    """Client for Ultravox API"""
    
    def __init__(self):
        self.base_url = settings.ULTRAVOX_BASE_URL
        self.api_key = settings.ULTRAVOX_API_KEY
        if not self.api_key:
            logger.warning("⚠️  ULTRAVOX_API_KEY is not set. Ultravox features will be disabled.")
            logger.warning("⚠️  Please set ULTRAVOX_API_KEY in your .env file to enable voice and agent syncing.")
        else:
            logger.info(f"✅ Ultravox client initialized with base URL: {self.base_url}")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
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
        logger.info(f"Ultravox API Request: {method} {url}")
        if data:
            logger.debug(f"Ultravox Request Data: {data}")
        
        async def _make_request():
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method,
                    url,
                    json=data,
                    params=params,
                    headers=self.headers,
                )
                logger.debug(f"Ultravox API Response: {response.status_code} for {url}")
                if response.status_code >= 400:
                    logger.error(f"Ultravox API Error Response: Status {response.status_code}, URL: {url}, Headers: {dict(response.headers)}")
                response.raise_for_status()
                return response.json()
        
        try:
            return await retry_with_backoff(_make_request)
        except httpx.HTTPStatusError as e:
            # Get error details from response if available
            error_detail = "Unknown error"
            error_details = {}
            try:
                error_body = e.response.json()
                if isinstance(error_body, dict):
                    error_detail = error_body.get("error", {}).get("message", str(e)) if isinstance(error_body.get("error"), dict) else str(e)
                    # Include full error response in details
                    error_details = error_body
                else:
                    error_detail = str(e)
                    error_details = {"raw_response": str(error_body)}
            except:
                error_detail = e.response.text or str(e)
                error_details = {"raw_response": error_detail}
            
            logger.error(f"Ultravox API error {e.response.status_code} for {method} {endpoint}: {error_detail}")
            raise ProviderError(
                provider="ultravox",
                message=f"Ultravox API error: {e.response.status_code} - {error_detail}",
                http_status=e.response.status_code,
                retry_after=int(e.response.headers.get("Retry-After", 0)) if e.response.status_code == 429 else None,
                details=error_details,
            )
        except httpx.RequestError as e:
            raise ProviderError(
                provider="ultravox",
                message=f"Failed to connect to Ultravox: {str(e)}",
                http_status=502,
            )
    
    # Voices
    async def list_voices(self) -> List[Dict[str, Any]]:
        """List all voices from Ultravox with provider-specific IDs"""
        response = await self._request("GET", "/voices")
        voices = response.get("data", [])
        # Ensure each voice includes provider-specific identifiers
        for voice in voices:
            # Extract provider_voice_id from externalVoice if present
            if "externalVoice" in voice:
                external_voice = voice["externalVoice"]
                for provider in ["elevenLabs", "openai", "google", "cartesia", "lmnt", "generic"]:
                    if provider in external_voice:
                        provider_data = external_voice[provider]
                        if "voiceId" in provider_data:
                            voice["provider_voice_id"] = provider_data["voiceId"]
                            voice["provider"] = provider
                            break
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
        """Create voice in Ultravox"""
        response = await self._request("POST", "/voices", data=voice_data)
        return response.get("data", {})
    
    async def get_voice(self, voice_id: str) -> Dict[str, Any]:
        """Get voice from Ultravox"""
        response = await self._request("GET", f"/voices/{voice_id}")
        return response.get("data", {})
    
    # Agents
    async def create_agent(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create agent in Ultravox"""
        response = await self._request("POST", "/agents", data=agent_data)
        return response.get("data", {})
    
    async def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """Get agent from Ultravox"""
        response = await self._request("GET", f"/agents/{agent_id}")
        return response.get("data", {})
    
    async def update_agent(self, agent_id: str, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update agent in Ultravox"""
        response = await self._request("PATCH", f"/agents/{agent_id}", data=agent_data)
        return response.get("data", {})
    
    async def delete_agent(self, agent_id: str, force: bool = False) -> Dict[str, Any]:
        """Delete agent from Ultravox"""
        params = {"force": "true"} if force else {}
        response = await self._request("DELETE", f"/agents/{agent_id}", params=params)
        return response.get("data", {})
    
    # Knowledge Bases (Corpora)
    async def create_corpus(self, corpus_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create corpus in Ultravox"""
        response = await self._request("POST", "/corpora", data=corpus_data)
        return response.get("data", {})
    
    async def add_corpus_source(self, corpus_id: str, source_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add source to corpus"""
        response = await self._request("POST", f"/corpora/{corpus_id}/sources", data=source_data)
        return response.get("data", {})
    
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
        
        response = await self._request("POST", "/calls", data=call_data)
        return response.get("data", {})
    
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
        response = await self._request("POST", "/calls", data=call_data)
        return response.get("data", {})
    
    async def get_call(self, call_id: str) -> Dict[str, Any]:
        """Get call from Ultravox"""
        response = await self._request("GET", f"/calls/{call_id}")
        return response.get("data", {})
    
    async def get_call_transcript(self, call_id: str) -> Dict[str, Any]:
        """Get call transcript"""
        response = await self._request("GET", f"/calls/{call_id}/transcript")
        return response.get("data", {})
    
    async def get_call_recording(self, call_id: str) -> str:
        """Get call recording URL"""
        response = await self._request("GET", f"/calls/{call_id}/recording")
        return response.get("data", {}).get("url", "")
    
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
            f"/agents/{agent_id}/scheduled-batches",
            data=batch_data,
        )
        return response.get("data", {})
    
    async def get_batch(self, agent_id: str, batch_id: str) -> Dict[str, Any]:
        """
        Get batch status from Ultravox.
        Requires agent_id as batches are scoped to agents.
        """
        # Ultravox API structure: /agents/{agent_id}/scheduled-batches/{batch_id}
        response = await self._request("GET", f"/agents/{agent_id}/scheduled-batches/{batch_id}")
        return response.get("data", {})
    
    # Webhooks
    async def list_webhooks(self) -> List[Dict[str, Any]]:
        """List all webhooks configured in Ultravox"""
        response = await self._request("GET", "/webhooks")
        return response.get("data", [])
    
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
        
        response = await self._request("POST", "/webhooks", data=webhook_data)
        return response.get("data", {})
    
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
        
        response = await self._request("PATCH", f"/webhooks/{webhook_id}", data=webhook_data)
        return response.get("data", {})
    
    async def delete_webhook(self, webhook_id: str) -> None:
        """Delete a webhook from Ultravox"""
        await self._request("DELETE", f"/webhooks/{webhook_id}")
    
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
        required_events = [
            "call.started",
            "call.ended",
            "call.completed",
            "call.failed",
            "batch.status.changed",
            "batch.completed",
            "voice.training.completed",
            "voice.training.failed",
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
            logger.error(f"Failed to register webhook with Ultravox: {e}", exc_info=True)
            return None
    
    # Tools
    async def create_tool(self, tool_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create tool in Ultravox"""
        response = await self._request("POST", "/tools", data=tool_data)
        return response.get("data", {})
    
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
        
        response = await self._request("POST", "/tools", data=tool_data)
        return response.get("data", {})
    
    async def update_tool(self, tool_id: str, tool_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update tool in Ultravox"""
        response = await self._request("PATCH", f"/tools/{tool_id}", data=tool_data)
        return response.get("data", {})
    
    async def delete_tool(self, tool_id: str) -> None:
        """Delete tool from Ultravox"""
        await self._request("DELETE", f"/tools/{tool_id}")
    
    # TTS API Keys
    async def update_tts_api_key(self, provider: str, api_key_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update TTS API key for provider"""
        response = await self._request(
            "PATCH",
            "/accounts/me/tts-api-keys",
            data={"provider": provider, **api_key_data},
        )
        return response.get("data", {})
    
    # SIP/Telephony
    async def get_sip_config(self) -> Dict[str, Any]:
        """Get SIP configuration from Ultravox"""
        response = await self._request("GET", "/sip/config")
        return response.get("data", {})


# Global client instance
ultravox_client = UltravoxClient()

