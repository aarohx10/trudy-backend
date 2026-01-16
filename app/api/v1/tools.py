"""
Tool Endpoints
"""
from fastapi import APIRouter, Header, Depends
from starlette.requests import Request
from typing import Optional
from datetime import datetime
import uuid
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ForbiddenError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.services.ultravox import ultravox_client
from app.models.schemas import (
    ToolCreate,
    ToolUpdate,
    ToolResponse,
    ResponseMeta,
)

router = APIRouter()


@router.post("")
async def create_tool(
    tool_data: ToolCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create tool"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Check idempotency key
    body_dict = tool_data.dict() if hasattr(tool_data, 'dict') else json.loads(json.dumps(tool_data, default=str))
    if idempotency_key:
        cached = await check_idempotency_key(
            current_user["client_id"],
            idempotency_key,
            request,
            body_dict,
        )
        if cached:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                content=cached["response_body"],
                status_code=cached["status_code"],
            )
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Create tool record
    tool_id = str(uuid.uuid4())
    tool_record = {
        "id": tool_id,
        "client_id": current_user["client_id"],
        "name": tool_data.name,
        "description": tool_data.description,
        "category": tool_data.category,
        "endpoint": tool_data.endpoint,
        "method": tool_data.method,
        "authentication": tool_data.authentication or {},
        "parameters": tool_data.parameters or {},
        "response_schema": tool_data.response_schema or {},
        "status": "creating",
    }
    
    db.insert("tools", tool_record)
    
    # Call Ultravox API
    try:
        ultravox_data = {
            "name": tool_data.name,
            "description": tool_data.description,
            "endpoint": tool_data.endpoint,
            "method": tool_data.method,
            "authentication": tool_data.authentication,
            "parameters": tool_data.parameters,
            "response_schema": tool_data.response_schema,
        }
        ultravox_response = await ultravox_client.create_tool(ultravox_data)
        
        # Update with Ultravox ID
        db.update(
            "tools",
            {"id": tool_id},
            {
                "ultravox_tool_id": ultravox_response.get("id"),
                "status": "active",
            },
        )
        tool_record["ultravox_tool_id"] = ultravox_response.get("id")
        tool_record["status"] = "active"
        
    except Exception as e:
        db.update(
            "tools",
            {"id": tool_id},
            {"status": "failed"},
        )
        raise
    
    response_data = {
        "data": ToolResponse(**tool_record),
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
            body_dict,
            response_data,
            201,
        )
    
    return response_data


@router.get("")
async def list_tools(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List tools"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    tools = db.select("tools", {"client_id": current_user["client_id"]}, order_by="created_at")
    
    return {
        "data": [ToolResponse(**tool) for tool in tools],
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/{tool_id}")
async def get_tool(
    tool_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single tool"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    tool = db.select_one("tools", {"id": tool_id, "client_id": current_user["client_id"]})
    if not tool:
        raise NotFoundError("tool", tool_id)
    
    return {
        "data": ToolResponse(**tool),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/{tool_id}")
async def update_tool(
    tool_id: str,
    tool_data: ToolUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update tool"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if tool exists
    tool = db.select_one("tools", {"id": tool_id, "client_id": current_user["client_id"]})
    if not tool:
        raise NotFoundError("tool", tool_id)
    
    # Prepare update data (only non-None fields)
    update_data = tool_data.dict(exclude_unset=True)
    if not update_data:
        # No updates provided
        return {
            "data": ToolResponse(**tool),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    # Update local database
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("tools", {"id": tool_id}, update_data)
    
    # Update Ultravox if tool has ultravox_tool_id and relevant fields changed
    if tool.get("ultravox_tool_id") and any(key in update_data for key in ["name", "description", "endpoint", "method", "authentication", "parameters", "response_schema"]):
        try:
            ultravox_data = {
                "name": update_data.get("name", tool.get("name")),
                "description": update_data.get("description", tool.get("description")),
                "endpoint": update_data.get("endpoint", tool.get("endpoint")),
                "method": update_data.get("method", tool.get("method")),
                "authentication": update_data.get("authentication", tool.get("authentication")),
                "parameters": update_data.get("parameters", tool.get("parameters")),
                "response_schema": update_data.get("response_schema", tool.get("response_schema")),
            }
            await ultravox_client.update_tool(tool["ultravox_tool_id"], ultravox_data)
        except Exception as e:
            # Log error but don't fail the request
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to update tool in Ultravox: {e}")
    
    # Get updated tool
    updated_tool = db.select_one("tools", {"id": tool_id, "client_id": current_user["client_id"]})
    
    return {
        "data": ToolResponse(**updated_tool),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete tool"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if tool exists
    tool = db.select_one("tools", {"id": tool_id, "client_id": current_user["client_id"]})
    if not tool:
        raise NotFoundError("tool", tool_id)
    
    # Delete from Ultravox if ultravox_tool_id exists
    if tool.get("ultravox_tool_id"):
        try:
            await ultravox_client.delete_tool(tool["ultravox_tool_id"])
        except Exception as e:
            # Log error but continue with deletion
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to delete tool from Ultravox: {e}")
    
    # Delete from database
    db.delete("tools", {"id": tool_id})
    
    return {
        "data": {"id": tool_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

