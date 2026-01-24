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
from app.core.database import DatabaseService
from app.models.schemas import ResponseMeta
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_tools(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List tools from database for current client"""
    try:
        # Fetch tools from database for current client
        client_id = current_user.get("client_id")
        db = DatabaseService()
        tools_list = db.select("tools", {"client_id": client_id}, order_by="created_at DESC")
        
        return {
            "data": list(tools_list),
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
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[TOOLS] [LIST] Failed to list tools from database (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list tools: {str(e)}")


@router.get("/{tool_id}")
async def get_tool(
    tool_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get tool from database"""
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Fetch tool from database
        tool_record = db.select_one("tools", {"id": tool_id, "client_id": client_id})
        
        if not tool_record:
            raise NotFoundError("tool", tool_id)
        
        return {
            "data": tool_record,
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
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "tool_id": tool_id,
        }
        logger.error(f"[TOOLS] [GET] Failed to get tool from database (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to get tool: {str(e)}")


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
        
        # Extract Ultravox tool ID from response
        ultravox_tool_id = ultravox_response.get("toolId") or ultravox_response.get("id")
        
        # Save tool to database
        if ultravox_tool_id:
            try:
                db = DatabaseService()
                now = datetime.utcnow()
                
                # Extract tool definition from request or response
                tool_definition = tool_data.get("definition", {})
                http_config = tool_definition.get("http", {})
                
                # Build database record
                tool_db_record = {
                    "id": str(uuid.uuid4()),
                    "client_id": current_user["client_id"],
                    "ultravox_tool_id": ultravox_tool_id,
                    "name": tool_data.get("name") or ultravox_response.get("name", ""),
                    "description": tool_definition.get("description") or tool_data.get("description"),
                    "category": tool_data.get("category"),  # Optional
                    "endpoint": http_config.get("baseUrlPattern") or http_config.get("url") or "",
                    "method": http_config.get("httpMethod") or http_config.get("method") or "POST",
                    "authentication": tool_definition.get("requirements", {}).get("httpSecurityOptions", {}),
                    "parameters": tool_definition.get("dynamicParameters", []),
                    "response_schema": tool_definition.get("responseSchema", {}),
                    "status": "active",  # Tool created successfully
                    "is_verified": False,  # Default to unverified
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
                
                # Insert into database
                db.insert("tools", tool_db_record)
                logger.info(f"[TOOLS] [CREATE] Tool saved to database: {tool_db_record['id']} (Ultravox: {ultravox_tool_id})")
                
            except Exception as db_error:
                # Log database error but don't fail the request (tool was created in Ultravox)
                logger.error(f"[TOOLS] [CREATE] Failed to save tool to database (non-critical): {db_error}", exc_info=True)
        
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "tool_data": tool_data if 'tool_data' in locals() else None,
        }
        logger.error(f"[TOOLS] [CREATE] Failed to create tool in Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
    """Update tool in both Ultravox and database"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # First, get the tool from database to get ultravox_tool_id
        tool_record = db.select_one("tools", {"id": tool_id, "client_id": client_id})
        if not tool_record:
            raise NotFoundError("tool", tool_id)
        
        ultravox_tool_id = tool_record.get("ultravox_tool_id")
        
        # Update in Ultravox if we have ultravox_tool_id
        if ultravox_tool_id:
            try:
                # Ultravox requires full definition replacement (PUT, not PATCH)
                await ultravox_client.update_tool(ultravox_tool_id, tool_data)
            except Exception as uv_error:
                logger.warning(f"[TOOLS] [UPDATE] Failed to update tool in Ultravox (non-critical): {uv_error}", exc_info=True)
                # Continue to update database even if Ultravox update fails
        
        # Update in database
        now = datetime.utcnow()
        tool_definition = tool_data.get("definition", {})
        http_config = tool_definition.get("http", {})
        
        update_data = {
            "updated_at": now.isoformat(),
        }
        
        # Update fields if provided
        if "name" in tool_data:
            update_data["name"] = tool_data["name"]
        if tool_definition.get("description"):
            update_data["description"] = tool_definition.get("description")
        if http_config.get("baseUrlPattern") or http_config.get("url"):
            update_data["endpoint"] = http_config.get("baseUrlPattern") or http_config.get("url")
        if http_config.get("httpMethod") or http_config.get("method"):
            update_data["method"] = http_config.get("httpMethod") or http_config.get("method")
        if tool_definition.get("dynamicParameters"):
            update_data["parameters"] = tool_definition.get("dynamicParameters")
        if tool_definition.get("requirements", {}).get("httpSecurityOptions"):
            update_data["authentication"] = tool_definition.get("requirements", {}).get("httpSecurityOptions")
        
        db.update("tools", {"id": tool_id, "client_id": client_id}, update_data)
        
        # Fetch updated record
        updated_tool = db.select_one("tools", {"id": tool_id, "client_id": client_id})
        
        return {
            "data": updated_tool,
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
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "tool_id": tool_id,
            "tool_data": tool_data if 'tool_data' in locals() else None,
        }
        logger.error(f"[TOOLS] [UPDATE] Failed to update tool (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to update tool: {str(e)}")


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete tool from both Ultravox and database"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    try:
        client_id = current_user.get("client_id")
        db = DatabaseService()
        
        # Get tool from database to get ultravox_tool_id
        tool_record = db.select_one("tools", {"id": tool_id, "client_id": client_id})
        if not tool_record:
            raise NotFoundError("tool", tool_id)
        
        ultravox_tool_id = tool_record.get("ultravox_tool_id")
        
        # Delete from Ultravox if we have ultravox_tool_id
        if ultravox_tool_id:
            try:
                await ultravox_client.delete_tool(ultravox_tool_id)
            except Exception as uv_error:
                logger.warning(f"[TOOLS] [DELETE] Failed to delete tool from Ultravox (non-critical): {uv_error}", exc_info=True)
                # Continue to delete from database even if Ultravox delete fails
        
        # Delete from database
        db.delete("tools", {"id": tool_id, "client_id": client_id})
        
        return {
            "data": {"id": tool_id, "deleted": True},
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
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "tool_id": tool_id,
        }
        logger.error(f"[TOOLS] [DELETE] Failed to delete tool (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, NotFoundError):
            raise
        raise ValidationError(f"Failed to delete tool: {str(e)}")


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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "tool_id": tool_id,
            "test_data": test_data if 'test_data' in locals() else None,
        }
        logger.error(f"[TOOLS] [TEST] Failed to test tool in Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        if isinstance(e, ProviderError):
            raise
        raise ProviderError(
            provider="ultravox",
            message=f"Failed to test tool: {str(e)}",
            http_status=500,
        )
