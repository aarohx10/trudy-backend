"""
Telephony Service
Handles phone number management, Telnyx integration, and Ultravox orchestration
"""
import logging
import httpx
from typing import Dict, Any, Optional, List
from app.core.database import DatabaseService
from app.core.encryption import encrypt_api_key, decrypt_api_key
from app.services.ultravox import ultravox_client
from app.core.exceptions import ProviderError, ValidationError
from app.core.config import settings
from app.core.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class TelnyxClient:
    """Client for Telnyx API"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.base_url = "https://api.telnyx.com/v2"
        self.api_key = api_key or settings.TELNYX_API_KEY
        if not self.api_key:
            logger.warning("⚠️  TELNYX_API_KEY is not set. Telnyx features will be disabled.")
        else:
            logger.info("✅ Telnyx client initialized")
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
        """Make HTTP request to Telnyx API"""
        if not self.api_key:
            raise ProviderError(
                provider="telnyx",
                message="Telnyx API key is not configured",
                http_status=500,
            )
        
        url = f"{self.base_url}{endpoint}"
        logger.info(f"[TELNYX] Making request | method={method} | url={url}")
        
        async def _make_request():
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method,
                    url,
                    json=data,
                    params=params,
                    headers=self.headers,
                )
                logger.debug(f"[TELNYX] Response | status_code={response.status_code}")
                if response.status_code >= 400:
                    error_text = response.text[:500] if response.text else "No response body"
                    logger.error(f"[TELNYX] Error | status={response.status_code} | response={error_text}")
                response.raise_for_status()
                return response.json()
        
        try:
            return await retry_with_backoff(_make_request)
        except httpx.HTTPStatusError as e:
            error_detail = "Unknown error"
            try:
                error_body = e.response.json()
                error_detail = error_body.get("errors", [{}])[0].get("detail", str(e)) if error_body.get("errors") else str(e)
            except:
                error_detail = e.response.text[:200] if e.response.text else str(e)
            
            logger.error(f"[TELNYX] API error | status={e.response.status_code} | error={error_detail}")
            raise ProviderError(
                provider="telnyx",
                message=f"Telnyx API error: {e.response.status_code} - {error_detail[:200]}",
                http_status=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"[TELNYX] Request error | error={e}")
            raise ProviderError(
                provider="telnyx",
                message=f"Telnyx API request failed: {e}",
                http_status=502,
            )
    
    async def search_available_numbers(
        self,
        country_code: str = "US",
        locality: Optional[str] = None,
        features: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for available phone numbers
        
        Args:
            country_code: ISO country code (default: US)
            locality: City/region to search in
            features: List of features required (e.g., ['voice'])
            
        Returns:
            List of available phone numbers
        """
        if features is None:
            features = ["voice"]
        
        params = {
            "filter[country_code]": country_code,
            "filter[features][]": features,
        }
        if locality:
            params["filter[locality]"] = locality
        
        response = await self._request("GET", "/available_phone_numbers", params=params)
        return response.get("data", [])
    
    async def purchase_number(self, phone_number: str) -> Dict[str, Any]:
        """Purchase a phone number from Telnyx
        
        Args:
            phone_number: Phone number in E.164 format (e.g., +15551234567)
            
        Returns:
            Number order response
        """
        data = {
            "phone_numbers": [{"phone_number": phone_number}]
        }
        response = await self._request("POST", "/number_orders", data=data)
        return response
    
    async def configure_number(
        self,
        phone_number: str,
        connection_id: Optional[str] = None,
        application_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Configure a phone number's connection/application
        
        Args:
            phone_number: Phone number in E.164 format
            connection_id: SIP Connection ID (for Trudy-managed numbers)
            application_id: TeXML Application ID
            
        Returns:
            Updated number configuration
        """
        data = {}
        if connection_id:
            data["connection_id"] = connection_id
        if application_id:
            data["application_id"] = application_id
        
        response = await self._request("PATCH", f"/phone_numbers/{phone_number}", data=data)
        return response


class TelephonyService:
    """Service for managing telephony operations"""
    
    def __init__(self, db: Optional[DatabaseService] = None):
        self.db = db or DatabaseService()
    
    async def init_telephony_config(self, organization_id: str) -> Dict[str, Any]:
        """Initialize telephony configuration for an organization
        
        Ensures Ultravox telephony-config is in sync with Trudy's master Telnyx credentials.
        This should be called when setting up telephony for the first time.
        
        Args:
            organization_id: Organization UUID
            
        Returns:
            Telephony config response
        """
        # Get Trudy's master Telnyx credentials from settings
        master_telnyx_key = settings.TELNYX_API_KEY
        if not master_telnyx_key:
            raise ValidationError("Master Telnyx API key not configured in settings")
        
        # Update Ultravox telephony config with master Telnyx key
        # This allows Ultravox to generate TeXML responses for Trudy-managed numbers
        response = await ultravox_client.update_telephony_config(
            telnyx_api_key=master_telnyx_key,
        )
        
        logger.info(f"[TELEPHONY] Initialized telephony config for organization {organization_id}")
        return response
    
    async def search_numbers(
        self,
        country_code: str = "US",
        locality: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for available phone numbers
        
        Args:
            country_code: ISO country code
            locality: City/region
            api_key: Optional Telnyx API key (uses master key if not provided)
            
        Returns:
            List of available numbers
        """
        telnyx = TelnyxClient(api_key=api_key)
        return await telnyx.search_available_numbers(
            country_code=country_code,
            locality=locality,
            features=["voice"],
        )
    
    async def purchase_number(
        self,
        organization_id: str,
        phone_number: str,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Purchase a phone number and configure it for Trudy
        
        Args:
            organization_id: Organization UUID
            phone_number: Phone number in E.164 format
            api_key: Optional Telnyx API key (uses master key if not provided)
            
        Returns:
            Purchase and configuration response
        """
        telnyx = TelnyxClient(api_key=api_key)
        
        # Purchase the number
        purchase_response = await telnyx.purchase_number(phone_number)
        
        # Get the number order ID
        order_id = purchase_response.get("data", {}).get("id")
        if not order_id:
            raise ValidationError("Failed to get number order ID from Telnyx")
        
        # Store number in database
        number_record = self.db.insert(
            "phone_numbers",
            {
                "organization_id": organization_id,
                "phone_number": phone_number,
                "provider_id": order_id,
                "status": "pending",  # Will be updated when order completes
                "is_trudy_managed": True,
            },
        )
        
        # Auto-configure Trudy-managed number to point to Ultravox
        try:
            await self.configure_trudy_managed_number(organization_id, number_record["id"])
        except Exception as e:
            logger.warning(f"[TELEPHONY] Failed to auto-configure number {phone_number}: {e}")
            # Don't fail the purchase if auto-config fails - user can configure manually
        
        logger.info(f"[TELEPHONY] Purchased number {phone_number} for organization {organization_id}")
        return {
            "number_id": number_record["id"],
            "phone_number": phone_number,
            "purchase_response": purchase_response,
        }
    
    async def configure_trudy_managed_number(
        self,
        organization_id: str,
        number_id: str,
    ) -> Dict[str, Any]:
        """Auto-configure a Trudy-managed number to point to Ultravox
        
        For Trudy-managed numbers, we automatically set the webhook/connection
        to point to Ultravox's TeXML endpoint.
        
        Args:
            organization_id: Organization UUID
            number_id: Phone number UUID
            
        Returns:
            Configuration response
        """
        number = self.db.select_one("phone_numbers", {"id": number_id, "organization_id": organization_id})
        if not number:
            raise ValidationError("Phone number not found")
        
        if not number.get("is_trudy_managed"):
            raise ValidationError("Number is not Trudy-managed")
        
        # Get Trudy's SIP Connection ID from settings
        # This should be configured once per Trudy account
        from app.core.config import settings
        trudy_connection_id = settings.TELNYX_CONNECTION_ID
        
        if not trudy_connection_id:
            logger.warning("[TELEPHONY] TELNYX_CONNECTION_ID not configured - skipping auto-configuration")
            raise ValidationError("Trudy SIP Connection ID not configured. Set TELNYX_CONNECTION_ID in environment.")
        
        # Configure number in Telnyx to use Trudy's connection
        telnyx = TelnyxClient()
        await telnyx.configure_number(
            phone_number=number["phone_number"],
            connection_id=trudy_connection_id,
        )
        
        logger.info(f"[TELEPHONY] Configured Trudy-managed number {number['phone_number']} to connection {trudy_connection_id}")
        return {"number_id": number_id, "configured": True}
    
    async def import_number(
        self,
        organization_id: str,
        phone_number: str,
        provider_type: str,
        credentials: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Import a BYO (Bring Your Own) phone number
        
        Args:
            organization_id: Organization UUID
            phone_number: Phone number in E.164 format
            provider_type: Provider type (telnyx, twilio, plivo, custom_sip)
            credentials: Provider-specific credentials
            
        Returns:
            Import response
        """
        # Encrypt credentials
        encrypted_creds = {}
        if credentials.get("api_key"):
            encrypted_creds["api_key"] = encrypt_api_key(credentials["api_key"])
        if credentials.get("auth_token"):
            encrypted_creds["auth_token"] = encrypt_api_key(credentials["auth_token"])
        if credentials.get("account_sid"):
            encrypted_creds["account_sid"] = encrypt_api_key(credentials["account_sid"])
        if credentials.get("sip_username"):
            encrypted_creds["sip_username"] = encrypt_api_key(credentials["sip_username"])
        if credentials.get("sip_password"):
            encrypted_creds["sip_password"] = encrypt_api_key(credentials["sip_password"])
        
        # Store credentials
        credential_record = self.db.insert(
            "telephony_credentials",
            {
                "organization_id": organization_id,
                "provider_type": provider_type,
                "friendly_name": credentials.get("friendly_name", f"{provider_type} credentials"),
                **encrypted_creds,
                "sip_server": credentials.get("sip_server"),
            },
        )
        
        # Register SIP with Ultravox if custom_sip
        if provider_type == "custom_sip":
            await ultravox_client.register_sip(
                username=credentials["sip_username"],
                password=credentials["sip_password"],
                proxy=credentials["sip_server"],
            )
        
        # Store number
        number_record = self.db.insert(
            "phone_numbers",
            {
                "organization_id": organization_id,
                "phone_number": phone_number,
                "status": "active",
                "is_trudy_managed": False,
                "telephony_credential_id": credential_record["id"],
            },
        )
        
        logger.info(f"[TELEPHONY] Imported number {phone_number} for organization {organization_id}")
        return {
            "number_id": number_record["id"],
            "credential_id": credential_record["id"],
            "phone_number": phone_number,
        }
    
    async def assign_number_to_agent(
        self,
        organization_id: str,
        number_id: str,
        agent_id: str,
        assignment_type: str = "inbound",
    ) -> Dict[str, Any]:
        """Assign phone number to agent for inbound or outbound
        
        Args:
            organization_id: Organization UUID
            number_id: Phone number UUID
            agent_id: Agent UUID
            assignment_type: "inbound" or "outbound"
            
        Returns:
            Assignment response
        """
        # Get number and agent
        number = self.db.select_one("phone_numbers", {"id": number_id, "organization_id": organization_id})
        if not number:
            raise ValidationError("Phone number not found")
        
        agent = self.db.select_one("agents", {"id": agent_id, "clerk_org_id": organization_id})
        if not agent:
            raise ValidationError("Agent not found")
        
        if assignment_type not in ["inbound", "outbound"]:
            raise ValidationError("assignment_type must be 'inbound' or 'outbound'")
        
        # Update assignment based on type
        if assignment_type == "inbound":
            # Clear any existing inbound assignment for this number
            existing_inbound = self.db.select_one("phone_numbers", {"id": number_id})
            if existing_inbound and existing_inbound.get("inbound_agent_id"):
                # Clear reverse lookup on old agent
                old_agent_id = existing_inbound["inbound_agent_id"]
                self.db.update("agents", {"id": old_agent_id, "clerk_org_id": organization_id}, {"inbound_phone_number_id": None})
            
            # Update phone_numbers table
            self.db.update("phone_numbers", {"id": number_id}, {"inbound_agent_id": agent_id})
            # Update agents table (reverse lookup)
            self.db.update("agents", {"id": agent_id, "clerk_org_id": organization_id}, {"inbound_phone_number_id": number_id})
            
            # Build inbound regex from phone number (for backward compatibility)
            phone_number = number["phone_number"].replace("+", "").replace("-", "").replace(" ", "")
            inbound_regex = f"^{phone_number}$"
            
            # Update agent's inbound_regex in database
            self.db.update("agents", {"id": agent_id, "clerk_org_id": organization_id}, {"inbound_regex": inbound_regex})
            
            # Update Ultravox inboundConfig with all inbound numbers for this agent
            ultravox_agent_id = agent.get("ultravox_agent_id")
            if ultravox_agent_id:
                # Get all inbound numbers for this agent
                all_inbound_numbers = self.db.select(
                    "phone_numbers",
                    {"inbound_agent_id": agent_id, "organization_id": organization_id}
                )
                phone_numbers = [n["phone_number"] for n in all_inbound_numbers]
                
                # Use inboundConfig API (preferred method)
                await ultravox_client.update_agent_inbound_config(
                    agent_id=ultravox_agent_id,
                    phone_numbers=phone_numbers,
                )
                logger.info(f"[TELEPHONY] Updated Ultravox agent {ultravox_agent_id} with inboundConfig: {phone_numbers}")
            
            logger.info(f"[TELEPHONY] Assigned number {number['phone_number']} to agent {agent_id} for INBOUND")
        else:  # outbound
            # Clear any existing outbound assignment for this number
            existing_outbound = self.db.select_one("phone_numbers", {"id": number_id})
            if existing_outbound and existing_outbound.get("outbound_agent_id"):
                # Clear reverse lookup on old agent
                old_agent_id = existing_outbound["outbound_agent_id"]
                self.db.update("agents", {"id": old_agent_id, "clerk_org_id": organization_id}, {"outbound_phone_number_id": None})
            
            # Update phone_numbers table
            self.db.update("phone_numbers", {"id": number_id}, {"outbound_agent_id": agent_id})
            # Update agents table (reverse lookup)
            self.db.update("agents", {"id": agent_id, "clerk_org_id": organization_id}, {"outbound_phone_number_id": number_id})
            
            logger.info(f"[TELEPHONY] Assigned number {number['phone_number']} to agent {agent_id} for OUTBOUND")
        
        return {
            "number_id": number_id,
            "agent_id": agent_id,
            "assignment_type": assignment_type,
        }
    
    async def unassign_number_from_agent(
        self,
        organization_id: str,
        number_id: str,
        assignment_type: str,
    ) -> Dict[str, Any]:
        """Unassign phone number from agent
        
        Args:
            organization_id: Organization UUID
            number_id: Phone number UUID
            assignment_type: "inbound" or "outbound"
            
        Returns:
            Unassignment response
        """
        number = self.db.select_one("phone_numbers", {"id": number_id, "organization_id": organization_id})
        if not number:
            raise ValidationError("Phone number not found")
        
        if assignment_type == "inbound":
            agent_id = number.get("inbound_agent_id")
            if agent_id:
                # Clear assignment
                self.db.update("phone_numbers", {"id": number_id}, {"inbound_agent_id": None})
                self.db.update("agents", {"id": agent_id, "clerk_org_id": organization_id}, {"inbound_phone_number_id": None})
                
                # Update Ultravox - remove this number from agent's inboundConfig
                agent = self.db.select_one("agents", {"id": agent_id, "clerk_org_id": organization_id})
                ultravox_agent_id = agent.get("ultravox_agent_id") if agent else None
                if ultravox_agent_id:
                    # Get remaining inbound numbers for this agent
                    remaining_numbers = self.db.select(
                        "phone_numbers",
                        {"inbound_agent_id": agent_id, "organization_id": organization_id}
                    )
                    phone_numbers = [n["phone_number"] for n in remaining_numbers]
                    
                    # Update Ultravox with remaining numbers (empty list if none)
                    await ultravox_client.update_agent_inbound_config(
                        agent_id=ultravox_agent_id,
                        phone_numbers=phone_numbers,
                    )
                    logger.info(f"[TELEPHONY] Removed number from Ultravox agent {ultravox_agent_id} inboundConfig")
        else:  # outbound
            agent_id = number.get("outbound_agent_id")
            if agent_id:
                # Clear assignment
                self.db.update("phone_numbers", {"id": number_id}, {"outbound_agent_id": None})
                self.db.update("agents", {"id": agent_id, "clerk_org_id": organization_id}, {"outbound_phone_number_id": None})
        
        return {"number_id": number_id, "assignment_type": assignment_type, "unassigned": True}
    
    async def get_agent_phone_numbers(
        self,
        organization_id: str,
        agent_id: str,
    ) -> Dict[str, Any]:
        """Get all phone numbers assigned to an agent
        
        Returns:
            Dict with "inbound" and "outbound" lists of phone number records
        """
        inbound_numbers = self.db.select(
            "phone_numbers",
            {"inbound_agent_id": agent_id, "organization_id": organization_id}
        )
        outbound_numbers = self.db.select(
            "phone_numbers",
            {"outbound_agent_id": agent_id, "organization_id": organization_id}
        )
        
        return {
            "inbound": inbound_numbers,
            "outbound": outbound_numbers,
        }
