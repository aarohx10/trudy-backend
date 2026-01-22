"""
Tool Endpoints - Pure Ultravox Proxy
"""
from fastapi import APIRouter, Header, Depends
from starlette.requests import Request
from typing import Optional, Dict, Any
from datetime import datetime
import uuid
import json

from app.core.auth import get_current_user
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError, ProviderError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.services.ultravox import ultravox_client
from app.models.schemas import ResponseMeta
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_tools(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List tools from Ultravox (ownership=private filter)"""
    try:
        # Fetch tools from Ultravox with ownership=private filter
        ultravox_response = await ultravox_client.list_tools(ownership="private")
        
        # Extract results from Ultravox response
        tools = ultravox_response.get("results", [])
        
        return {
            "data": tools,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to list tools from Ultravox: {e}", exc_info=True)
        if isinstance(e, ProviderError):
            raise
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to list tools: {str(e)}",
            http_status=500,
        )


@router.get("/{tool_id}")
async def get_tool(
    tool_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get tool from Ultravox"""
    try:
        tool = await ultravox_client.get_tool(tool_id)
        
        return {
            "data": tool,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to get tool {tool_id} from Ultravox: {e}", exc_info=True)
        if isinstance(e, ProviderError):
            raise
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to get tool: {str(e)}",
            http_status=500,
        )


@router.post("")
async def create_tool(
    tool_data: Dict[str, Any],
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create tool in Ultravox - Direct proxy"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Check idempotency key
    if idempotency_key:
        cached = await check_idempotency_key(
            current_user["client_id"],
            idempotency_key,
            request,
            tool_data,
        )
        if cached:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                content=cached["response_body"],
                status_code=cached["status_code"],
            )
    
    try:
        # Forward directly to Ultravox
        ultravox_response = await ultravox_client.create_tool(tool_data)
        
        response_data = {
            "data": ultravox_response,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
        # Store idempotency response
        if idempotency_key:
            await store_idempotency_response(
                current_user["client_id"],
                idempotency_key,
                request,
                tool_data,
                response_data,
                201,
            )
        
        return response_data
    except Exception as e:
        logger.error(f"Failed to create tool in Ultravox: {e}", exc_info=True)
        if isinstance(e, ProviderError):
            raise
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to create tool: {str(e)}",
            http_status=500,
        )


@router.put("/{tool_id}")
async def update_tool(
    tool_id: str,
    tool_data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update tool in Ultravox - Full definition replacement (Ultravox standard)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        # Ultravox requires full definition replacement (PUT, not PATCH)
        updated_tool = await ultravox_client.update_tool(tool_id, tool_data)
        
        return {
            "data": updated_tool,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to update tool {tool_id} in Ultravox: {e}", exc_info=True)
        if isinstance(e, ProviderError):
            raise
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to update tool: {str(e)}",
            http_status=500,
        )


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete tool from Ultravox"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        await ultravox_client.delete_tool(tool_id)
        
        return {
            "data": {"id": tool_id, "deleted": True},
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to delete tool {tool_id} from Ultravox: {e}", exc_info=True)
        if isinstance(e, ProviderError):
            raise
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to delete tool: {str(e)}",
            http_status=500,
        )


@router.post("/{tool_id}/test")
async def test_tool(
    tool_id: str,
    test_data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Test tool in Ultravox"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        test_result = await ultravox_client.test_tool(tool_id, test_data)
        
        return {
            "data": test_result,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except Exception as e:
        logger.error(f"Failed to test tool {tool_id} in Ultravox: {e}", exc_info=True)
        if isinstance(e, ProviderError):
            raise
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to test tool: {str(e)}",
            http_status=500,
        )
