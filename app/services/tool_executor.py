"""
Tool Execution Service
Handles sandbox execution and logging of tool calls
"""
import httpx
import logging
import time
from typing import Dict, Any, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)


async def execute_tool_test(
    url: str,
    method: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Execute a test call to a tool endpoint.
    Returns status code, response body snippet, and timing information.
    """
    start_time = time.time()
    result = {
        "success": False,
        "status_code": None,
        "response_body": None,
        "response_body_snippet": None,
        "error_message": None,
        "response_time_ms": None,
    }
    
    try:
        # Prepare headers
        request_headers = headers or {}
        if "Content-Type" not in request_headers and body:
            request_headers["Content-Type"] = "application/json"
        
        # Make HTTP request
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=request_headers, params=body)
            elif method.upper() == "POST":
                response = await client.post(url, headers=request_headers, json=body)
            elif method.upper() == "PUT":
                response = await client.put(url, headers=request_headers, json=body)
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=request_headers, params=body)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            # Calculate response time
            response_time_ms = int((time.time() - start_time) * 1000)
            
            # Parse response body
            response_body = None
            response_body_snippet = None
            try:
                response_body = response.json()
                # Create snippet (first 500 chars)
                response_str = json.dumps(response_body, indent=2)
                response_body_snippet = response_str[:500]
                if len(response_str) > 500:
                    response_body_snippet += "..."
            except:
                # Not JSON, use text
                response_text = response.text[:500]
                response_body_snippet = response_text
                if len(response.text) > 500:
                    response_body_snippet += "..."
            
            result.update({
                "success": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "response_body": response_body,
                "response_body_snippet": response_body_snippet,
                "response_time_ms": response_time_ms,
            })
            
            if not result["success"]:
                result["error_message"] = f"HTTP {response.status_code}: {response_body_snippet}"
    
    except httpx.TimeoutException:
        result["error_message"] = f"Request timeout after {timeout}s"
        result["response_time_ms"] = int((time.time() - start_time) * 1000)
        logger.warning(f"Tool execution timeout: {url} (timeout: {timeout}s)")
    except httpx.RequestError as e:
        result["error_message"] = f"Request error: {str(e)}"
        result["response_time_ms"] = int((time.time() - start_time) * 1000)
        logger.warning(f"Tool execution request error: {url} - {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error executing tool test: {e}", exc_info=True)
        result["error_message"] = f"Unexpected error: {str(e)}"
        result["response_time_ms"] = int((time.time() - start_time) * 1000)
    
    return result


async def execute_tool_with_logging(
    db_admin,
    tool_id: str,
    url: str,
    method: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
    call_id: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Execute a tool call with automatic logging.
    Used during agent testing (WebRTC) to capture request/response for debugging.
    """
    start_time = time.time()
    request_headers = headers or {}
    request_body = body
    
    try:
        # Execute the tool
        result = await execute_tool_test(
            url=url,
            method=method,
            headers=request_headers,
            body=request_body,
            timeout=timeout,
        )
        
        # Log the execution
        await log_tool_execution(
            db_admin=db_admin,
            tool_id=tool_id,
            agent_id=agent_id,
            call_id=call_id,
            session_id=session_id,
            request_url=url,
            request_method=method,
            request_headers=request_headers,
            request_body=request_body,
            response_status=result.get("status_code"),
            response_headers={},  # We don't capture response headers in execute_tool_test
            response_body=result.get("response_body"),
            response_time_ms=result.get("response_time_ms"),
            error_message=result.get("error_message"),
        )
        
        return result
    
    except Exception as e:
        # Log error
        await log_tool_execution(
            db_admin=db_admin,
            tool_id=tool_id,
            agent_id=agent_id,
            call_id=call_id,
            session_id=session_id,
            request_url=url,
            request_method=method,
            request_headers=request_headers,
            request_body=request_body,
            response_time_ms=int((time.time() - start_time) * 1000),
            error_message=str(e),
        )
        raise


async def log_tool_execution(
    db_admin,
    tool_id: str,
    agent_id: Optional[str] = None,
    call_id: Optional[str] = None,
    session_id: Optional[str] = None,
    request_url: str = "",
    request_method: str = "",
    request_headers: Optional[Dict[str, Any]] = None,
    request_body: Optional[Dict[str, Any]] = None,
    response_status: Optional[int] = None,
    response_headers: Optional[Dict[str, Any]] = None,
    response_body: Optional[Dict[str, Any]] = None,
    response_time_ms: Optional[int] = None,
    error_message: Optional[str] = None,
) -> str:
    """
    Log a tool execution to the tool_logs table.
    Returns the log ID.
    """
    log_record = {
        "tool_id": tool_id,
        "agent_id": agent_id,
        "call_id": call_id,
        "session_id": session_id,
        "request_url": request_url,
        "request_method": request_method,
        "request_headers": request_headers or {},
        "request_body": request_body or {},
        "response_status": response_status,
        "response_headers": response_headers or {},
        "response_body": response_body or {},
        "response_time_ms": response_time_ms,
        "error_message": error_message,
    }
    
    result = db_admin.insert("tool_logs", log_record)
    return result.get("id", "")


def validate_ultravox_schema(parameters: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate that parameters JSON matches Ultravox API expectations.
    Returns (is_valid, error_message).
    """
    if not parameters:
        return True, None
    
    # Ultravox expects parameters to be a JSON Schema object
    # Check for required schema fields
    if not isinstance(parameters, dict):
        return False, "Parameters must be a JSON object"
    
    # If it's a JSON Schema, validate structure
    if "type" in parameters or "properties" in parameters:
        # This looks like a JSON Schema
        # Validate basic structure
        if "type" in parameters and parameters["type"] not in ["object", "string", "number", "integer", "boolean", "array"]:
            return False, f"Invalid JSON Schema type: {parameters['type']}"
        
        # If properties exist, validate each property
        if "properties" in parameters:
            if not isinstance(parameters["properties"], dict):
                return False, "Schema 'properties' must be an object"
            
            for prop_name, prop_schema in parameters["properties"].items():
                if not isinstance(prop_schema, dict):
                    return False, f"Property '{prop_name}' schema must be an object"
                if "type" not in prop_schema:
                    return False, f"Property '{prop_name}' schema must have a 'type' field"
    
    return True, None
