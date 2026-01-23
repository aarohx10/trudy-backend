"""
Telephony & SIP Endpoints
"""
from fastapi import APIRouter, Header, Depends, Query
from typing import Optional, List
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ForbiddenError, ValidationError
from app.services.ultravox import ultravox_client
from app.models.schemas import ResponseMeta
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/config")
async def get_telephony_config(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get SIP/Telephony configuration"""
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "operation": "get_telephony_config",
            "client_id": current_user.get("client_id") if current_user else None,
        }
        logger.error(f"[TELEPHONY] Error getting telephony config (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        # If Ultravox doesn't support this endpoint yet, return default
        return {
            "data": {
                "sip_endpoint": "sip.ultravox.ai",
                "username": current_user.get("client_id", ""),
                "password": "",  # Should be retrieved from secure storage
                "domain": "ultravox.ai",
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }


@router.get("/numbers")
async def list_phone_numbers(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    limit: int = 50,
    offset: int = 0,
):
    """List phone numbers (from Ultravox/Telnyx)"""
    # Note: This endpoint should integrate with Telnyx or Ultravox to get phone numbers
    # For now, we return an empty list as phone numbers are managed externally
    # TODO: Integrate with Telnyx API to fetch phone numbers
    # TODO: Store phone numbers in database if needed
    
    try:
        # If Ultravox has a phone numbers endpoint, use it
        # For now, return empty list
        phone_numbers = []
        
        return {
            "data": phone_numbers,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
            "pagination": {
                "total": len(phone_numbers),
                "limit": limit,
                "offset": offset,
                "has_more": False,
            },
        }
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "operation": "list_phone_numbers",
            "limit": limit,
            "offset": offset,
            "client_id": current_user.get("client_id") if current_user else None,
        }
        logger.error(f"[TELEPHONY] Failed to list phone numbers (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        # Return empty list on error
        return {
            "data": [],
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
            "pagination": {
                "total": 0,
                "limit": limit,
                "offset": offset,
                "has_more": False,
            },
        }


@router.post("/numbers/purchase")
async def purchase_phone_number(
    phone_number: Optional[str] = None,
    area_code: Optional[str] = None,
    country: str = "US",
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Purchase phone number from Telnyx/Ultravox"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Validate input
    if not phone_number and not area_code:
        raise ValidationError("Either phone_number or area_code must be provided")
    
    # TODO: Integrate with Telnyx API to purchase phone number
    # This requires:
    # 1. Telnyx API key configuration
    # 2. Telnyx SDK or HTTP client
    # 3. Phone number purchase API call
    # 4. Store purchased number in database if needed
    
    # For now, return a placeholder response
    logger.warning("Phone number purchase not yet implemented - requires Telnyx API integration")
    
    return {
        "data": {
            "message": "Phone number purchase requires Telnyx API integration",
            "phone_number": phone_number or f"+1{area_code}XXXXXXX",
            "status": "pending_integration",
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

