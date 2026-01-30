"""
Telephony & Phone Number Management Endpoints
Handles number search, purchase, import, and assignment
"""
from fastapi import APIRouter, Header, Depends, Query, Body
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.permissions import require_admin_role
from app.core.database import DatabaseService
from app.core.exceptions import ForbiddenError, ValidationError, NotFoundError
from app.services.telephony import TelephonyService
from app.models.schemas import (
    ResponseMeta,
    NumberSearchRequest,
    NumberPurchaseRequest,
    NumberImportRequest,
    NumberAssignmentRequest,
    PhoneNumberResponse,
    TelephonyCredentialResponse,
    AvailableNumberResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/init")
async def init_telephony(
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Initialize telephony configuration for the organization"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    telephony_service = TelephonyService(db)
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        result = await telephony_service.init_telephony_config(organization_id)
        
        return {
            "data": result,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to initialize telephony config: {e}", exc_info=True)
        raise ValidationError(f"Failed to initialize telephony: {str(e)}")


@router.post("/numbers/search")
async def search_numbers(
    request: NumberSearchRequest,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Search for available phone numbers"""
    # Permission check handled by require_admin_role dependency
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    telephony_service = TelephonyService(db)
    
    try:
        available_numbers = await telephony_service.search_numbers(
            country_code=request.country_code,
            locality=request.locality,
            api_key=request.api_key,
        )
        
        # Transform Telnyx response to our format
        formatted_numbers = [
            AvailableNumberResponse(
                phone_number=num.get("phone_number", ""),
                region_information=num.get("region_information"),
                features=num.get("features", []),
                cost_information=num.get("cost_information"),
            )
            for num in available_numbers
        ]
        
        return {
            "data": [num.dict() for num in formatted_numbers],
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to search numbers: {e}", exc_info=True)
        raise ValidationError(f"Failed to search numbers: {str(e)}")


@router.post("/numbers/purchase")
async def purchase_number(
    request: NumberPurchaseRequest,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Purchase a phone number from Telnyx"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    telephony_service = TelephonyService(db)
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        result = await telephony_service.purchase_number(
            organization_id=organization_id,
            phone_number=request.phone_number,
            api_key=request.api_key,
        )
        
        return {
            "data": result,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to purchase number: {e}", exc_info=True)
        raise ValidationError(f"Failed to purchase number: {str(e)}")


@router.post("/numbers/import")
async def import_number(
    request: NumberImportRequest,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Import a BYO (Bring Your Own) phone number"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    telephony_service = TelephonyService(db)
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        # Validate credentials based on provider type
        if request.provider_type == "custom_sip":
            if not request.sip_username or not request.sip_password or not request.sip_server:
                raise ValidationError("SIP username, password, and server are required for custom_sip")
        elif request.provider_type in ["telnyx", "twilio"]:
            if not request.api_key:
                raise ValidationError(f"API key is required for {request.provider_type}")
            if request.provider_type == "twilio" and not request.account_sid:
                raise ValidationError("Account SID is required for Twilio")
        
        credentials = {
            "friendly_name": request.friendly_name,
            "api_key": request.api_key,
            "account_sid": request.account_sid,
            "auth_token": request.auth_token,
            "sip_username": request.sip_username,
            "sip_password": request.sip_password,
            "sip_server": request.sip_server,
        }
        
        result = await telephony_service.import_number(
            organization_id=organization_id,
            phone_number=request.phone_number,
            provider_type=request.provider_type.value,
            credentials=credentials,
        )
        
        return {
            "data": result,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to import number: {e}", exc_info=True)
        raise ValidationError(f"Failed to import number: {str(e)}")


@router.get("/numbers")
async def list_phone_numbers(
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List all phone numbers for the organization"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        # Get phone numbers from database
        numbers = db.select(
            "phone_numbers",
            {"organization_id": organization_id},
            order_by="created_at DESC",
            limit=limit,
            offset=offset,
        )
        
        # Get total count
        total = len(db.select("phone_numbers", {"organization_id": organization_id}))
        
        formatted_numbers = [
            PhoneNumberResponse(
                id=num["id"],
                organization_id=num["organization_id"],
                agent_id=num.get("agent_id"),
                phone_number=num["phone_number"],
                provider_id=num.get("provider_id"),
                status=num.get("status", "active"),
                is_trudy_managed=num.get("is_trudy_managed", True),
                telephony_credential_id=num.get("telephony_credential_id"),
                created_at=num["created_at"],
                updated_at=num.get("updated_at", num["created_at"]),
            )
            for num in numbers
        ]
        
        return {
            "data": [num.dict() for num in formatted_numbers],
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
            },
        }
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to list phone numbers: {e}", exc_info=True)
        raise ValidationError(f"Failed to list phone numbers: {str(e)}")


@router.post("/numbers/assign")
async def assign_number_to_agent(
    request: NumberAssignmentRequest,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Assign a phone number to an agent (inbound or outbound)"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    telephony_service = TelephonyService(db)
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        # Validate assignment_type
        if request.assignment_type not in ["inbound", "outbound"]:
            raise ValidationError("assignment_type must be 'inbound' or 'outbound'")
        
        result = await telephony_service.assign_number_to_agent(
            organization_id=organization_id,
            number_id=request.number_id,
            agent_id=request.agent_id,
            assignment_type=request.assignment_type,
        )
        
        return {
            "data": result,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to assign number: {e}", exc_info=True)
        raise ValidationError(f"Failed to assign number: {str(e)}")


@router.post("/numbers/unassign")
async def unassign_number_from_agent(
    request: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Unassign a phone number from an agent"""
    # Permission check handled by require_admin_role dependency
    
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    telephony_service = TelephonyService(db)
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        number_id = request.get("number_id")
        assignment_type = request.get("assignment_type")
        
        if not number_id or not assignment_type:
            raise ValidationError("number_id and assignment_type are required")
        
        # Validate assignment_type
        if assignment_type not in ["inbound", "outbound"]:
            raise ValidationError("assignment_type must be 'inbound' or 'outbound'")
        
        result = await telephony_service.unassign_number_from_agent(
            organization_id=organization_id,
            number_id=number_id,
            assignment_type=assignment_type,
        )
        
        return {
            "data": result,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to unassign number: {e}", exc_info=True)
        raise ValidationError(f"Failed to unassign number: {str(e)}")


@router.get("/credentials")
async def list_telephony_credentials(
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """List all telephony credentials for the organization"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        credentials = db.select(
            "telephony_credentials",
            {"organization_id": organization_id},
            order_by="created_at DESC",
        )
        
        formatted_credentials = [
            TelephonyCredentialResponse(
                id=cred["id"],
                organization_id=cred["organization_id"],
                provider_type=cred["provider_type"],
                friendly_name=cred.get("friendly_name"),
                created_at=cred["created_at"],
                updated_at=cred.get("updated_at", cred["created_at"]),
            )
            for cred in credentials
        ]
        
        return {
            "data": [cred.dict() for cred in formatted_credentials],
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to list credentials: {e}", exc_info=True)
        raise ValidationError(f"Failed to list credentials: {str(e)}")


@router.get("/agents/{agent_id}/numbers")
async def get_agent_numbers(
    agent_id: str,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Get all phone numbers assigned to an agent"""
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    telephony_service = TelephonyService(db)
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        # Verify agent belongs to organization - filter by org_id instead of client_id
        agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})
        if not agent:
            raise NotFoundError("Agent not found")
        
        result = await telephony_service.get_agent_phone_numbers(
            organization_id=organization_id,
            agent_id=agent_id,
        )
        
        # Format response
        formatted_result = {
            "inbound": [
                {
                    "id": n["id"],
                    "phone_number": n["phone_number"],
                    "status": n.get("status", "active"),
                    "is_trudy_managed": n.get("is_trudy_managed", True),
                }
                for n in result["inbound"]
            ],
            "outbound": [
                {
                    "id": n["id"],
                    "phone_number": n["phone_number"],
                    "status": n.get("status", "active"),
                    "is_trudy_managed": n.get("is_trudy_managed", True),
                }
                for n in result["outbound"]
            ],
        }
        
        return {
            "data": formatted_result,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except NotFoundError:
        raise
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to get agent numbers: {e}", exc_info=True)
        raise ValidationError(f"Failed to get agent numbers: {str(e)}")


@router.get("/agents/{agent_id}/webhook-url")
async def get_agent_webhook_url(
    agent_id: str,
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Get Ultravox webhook URL for BYOC setup
    
    Returns the webhook URL that users need to configure in their carrier
    console (Twilio, Telnyx, etc.) for BYOC numbers.
    """
    # CRITICAL: Use clerk_org_id for organization-first approach
    clerk_org_id = current_user.get("clerk_org_id")
    if not clerk_org_id:
        raise ValidationError("Missing organization ID in token")
    
    # Initialize database service with org_id context
    db = DatabaseService(token=current_user["token"], org_id=clerk_org_id)
    db.set_auth(current_user["token"])
    
    try:
        # CRITICAL: Use clerk_org_id instead of client_id
        organization_id = clerk_org_id
        
        # Get agent and verify ownership - filter by org_id instead of client_id
        agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})
        if not agent:
            raise NotFoundError("Agent not found")
        
        ultravox_agent_id = agent.get("ultravox_agent_id")
        if not ultravox_agent_id:
            raise ValidationError("Agent is not synced to Ultravox yet")
        
        # Generate webhook URL
        from app.core.config import settings
        base_url = settings.ULTRAVOX_BASE_URL.rstrip("/")
        webhook_url = f"{base_url}/api/agents/{ultravox_agent_id}/telephony_xml"
        
        return {
            "data": {
                "webhook_url": webhook_url,
                "agent_id": agent_id,
                "ultravox_agent_id": ultravox_agent_id,
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except NotFoundError:
        raise
    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to get webhook URL: {e}", exc_info=True)
        raise ValidationError(f"Failed to get webhook URL: {str(e)}")


@router.get("/config")
async def get_telephony_config(
    current_user: dict = Depends(require_admin_role),
    x_client_id: Optional[str] = Header(None),
):
    """Get SIP/Telephony configuration (legacy endpoint for compatibility)"""
    from app.services.ultravox import ultravox_client
    
    try:
        sip_config = await ultravox_client.get_sip_config()
        
        return {
            "data": {
                "sip_endpoint": sip_config.get("sip_endpoint", "sip.ultravox.ai"),
                "username": sip_config.get("username", ""),
                "password": sip_config.get("password", ""),
                "domain": sip_config.get("domain", "ultravox.ai"),
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.warning(f"[TELEPHONY] Failed to get SIP config from Ultravox: {e}")
        # Return default config if Ultravox doesn't support this endpoint
        return {
            "data": {
                "sip_endpoint": "sip.ultravox.ai",
                "username": current_user.get("clerk_org_id", ""),
                "password": "",
                "domain": "ultravox.ai",
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
