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
from app.core.database import DatabaseService, DatabaseAdminService
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.services.ultravox import ultravox_client
from app.services.tool_executor import execute_tool_test, validate_ultravox_schema, log_tool_execution
from app.models.schemas import (
    ToolCreate,
    ToolUpdate,
    ToolResponse,
    ToolTestRequest,
    ToolTestResponse,
    ResponseMeta,
)
import logging

logger = logging.getLogger(__name__)

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
    
    # Validate Ultravox schema before proceeding
    if tool_data.parameters:
        is_valid, error_msg = validate_ultravox_schema(tool_data.parameters)
        if not is_valid:
            raise ValidationError(f"Invalid Ultravox parameter schema: {error_msg}")
    
    # Create tool record (unverified by default)
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
        "is_verified": False,  # Must be verified before use
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
    
    # Validate Ultravox schema if parameters are being updated
    if "parameters" in update_data and update_data["parameters"]:
        is_valid, error_msg = validate_ultravox_schema(update_data["parameters"])
        if not is_valid:
            raise ValidationError(f"Invalid Ultravox parameter schema: {error_msg}")
    
    # Reset verification if endpoint, method, authentication, or parameters changed
    verification_reset_fields = ["endpoint", "method", "authentication", "parameters"]
    if any(field in update_data for field in verification_reset_fields):
        update_data["is_verified"] = False
        update_data["verification_error"] = None
    
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


@router.post("/test")
async def test_tool(
    test_request: ToolTestRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Test a tool endpoint with sandbox execution.
    Performs actual HTTP call and returns status code and response snippet.
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Execute the test
    result = await execute_tool_test(
        url=test_request.url,
        method=test_request.method,
        headers=test_request.headers,
        body=test_request.body or test_request.test_parameters,
    )
    
    return {
        "data": ToolTestResponse(**result),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{tool_id}/verify")
async def verify_tool(
    tool_id: str,
    test_request: Optional[ToolTestRequest] = None,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Verify a tool by testing it and updating is_verified status.
    If test_request is provided, uses those values. Otherwise uses tool's stored values.
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Get tool
    tool = db.select_one("tools", {"id": tool_id, "client_id": current_user["client_id"]})
    if not tool:
        raise NotFoundError("tool", tool_id)
    
    # Use provided test values or tool's stored values
    if test_request:
        url = test_request.url
        method = test_request.method
        headers = test_request.headers
        body = test_request.body or test_request.test_parameters
    else:
        url = tool["endpoint"]
        method = tool["method"]
        headers = {}
        if tool.get("authentication"):
            # Apply authentication headers
            auth = tool["authentication"]
            if auth.get("type") == "api_key":
                header_name = auth.get("header_name", "X-API-Key")
                headers[header_name] = auth.get("api_key", "")
            elif auth.get("type") == "bearer":
                headers["Authorization"] = f"Bearer {auth.get('token', '')}"
        body = None
    
    # Execute test
    result = await execute_tool_test(
        url=url,
        method=method,
        headers=headers,
        body=body,
    )
    
    # Update tool verification status
    update_data = {
        "is_verified": result["success"],
        "verification_error": result.get("error_message") if not result["success"] else None,
    }
    db.update("tools", {"id": tool_id}, update_data)
    
    return {
        "data": {
            "tool_id": tool_id,
            "is_verified": result["success"],
            "test_result": ToolTestResponse(**result),
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/{tool_id}/logs")
async def get_tool_logs(
    tool_id: str,
    session_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get tool execution logs for debugging"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Verify tool belongs to user
    tool = db.select_one("tools", {"id": tool_id, "client_id": current_user["client_id"]})
    if not tool:
        raise NotFoundError("tool", tool_id)
    
    # Use admin DB to query logs (RLS will filter by tool ownership)
    admin_db = DatabaseAdminService()
    filters = {"tool_id": tool_id}
    if session_id:
        filters["session_id"] = session_id
    
    logs = admin_db.select("tool_logs", filters, order_by="created_at")
    
    # Apply pagination
    total = len(logs)
    paginated_logs = logs[offset:offset + limit]
    
    return {
        "data": paginated_logs,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
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

