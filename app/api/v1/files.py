"""
File Serving Endpoint (Hetzner VPS)
Serves files with signature verification (replaces direct storage access)
Handles both GET (download) and PUT (upload) operations
"""
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import FileResponse, Response
import hmac
import hashlib
import time
import os
import re
import logging
from app.core.storage import get_file_path, check_file_exists, get_storage_path, ensure_directory_exists
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum file size for uploads (50MB)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

# Compile CORS regex patterns for origin validation
_cors_compiled_patterns = []
for pattern in settings.CORS_WILDCARD_PATTERNS:
    # Ensure patterns are anchored for full string match
    if not pattern.startswith("^"):
        pattern = f"^{pattern}"
    if not pattern.endswith("$"):
        pattern = f"{pattern}$"
    try:
        _cors_compiled_patterns.append(re.compile(pattern))
    except re.error as e:
        logger.warning(f"Invalid CORS regex pattern '{pattern}': {e}")


def is_origin_allowed(origin: str) -> bool:
    """
    Check if an origin is allowed by CORS configuration.
    Matches the logic in main.py for consistency.
    """
    if not origin:
        return False
    
    # Check exact origins first
    if origin in settings.CORS_ORIGINS:
        return True
    
    # Check regex patterns for dynamic subdomains
    for pattern in _cors_compiled_patterns:
        if pattern.match(origin):
            return True
    
    return False


def add_cors_headers_if_allowed(response: Response, origin: str) -> None:
    """
    Add CORS headers to response only if origin is allowed.
    Matches the logic in main.py for consistency.
    """
    if origin and is_origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Max-Age"] = "86400"


@router.get("/files/{bucket_type}")
async def serve_file(
    bucket_type: str,
    key: str = Query(...),
    expires: int = Query(...),
    signature: str = Query(...),
    operation: str = Query("get", description="Operation type: get or put"),
    content_type: str = Query(None, description="Content type (for PUT operations)"),
):
    """
    Serve file with signature verification
    
    This endpoint replaces direct storage access. Files are served from local storage
    after verifying the HMAC signature.
    """
    try:
        # Verify signature
        secret = settings.WEBHOOK_SIGNING_SECRET.encode() if settings.WEBHOOK_SIGNING_SECRET else b"default-secret-change-me"
        message = f"{operation}:{bucket_type}:{key}:{expires}"
        expected_sig = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(signature, expected_sig):
            logger.warning(f"Invalid signature for file: {bucket_type}/{key}")
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        # Check expiration
        if time.time() > expires:
            logger.warning(f"Expired URL for file: {bucket_type}/{key}")
            raise HTTPException(status_code=403, detail="URL expired")
        
        # Check file exists
        if not check_file_exists(bucket_type, key):
            logger.warning(f"File not found: {bucket_type}/{key}")
            raise HTTPException(status_code=404, detail="File not found")
        
        # Serve file
        file_path = get_file_path(bucket_type, key)
        logger.debug(f"Serving file: {file_path}")
        
        # Determine media type from content_type or file extension
        media_type = content_type
        if not media_type:
            # Try to infer from extension
            if key.endswith('.mp3') or key.endswith('.mpeg'):
                media_type = 'audio/mpeg'
            elif key.endswith('.wav'):
                media_type = 'audio/wav'
            elif key.endswith('.pdf'):
                media_type = 'application/pdf'
            elif key.endswith('.txt'):
                media_type = 'text/plain'
            elif key.endswith('.csv'):
                media_type = 'text/csv'
        
        return FileResponse(
            file_path,
            media_type=media_type,
            filename=key.split('/')[-1],  # Just the filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.options("/files/{bucket_type}")
async def options_file_upload(
    request: Request,
    bucket_type: str,
):
    """
    Handle OPTIONS preflight requests for file uploads.
    """
    origin = request.headers.get("origin")
    response = Response(status_code=200)
    add_cors_headers_if_allowed(response, origin)
    return response


@router.put("/files/{bucket_type}")
async def upload_file(
    request: Request,
    bucket_type: str,
    key: str = Query(...),
    expires: int = Query(...),
    signature: str = Query(...),
    operation: str = Query("put", description="Operation type: get or put"),
    content_type: str = Query(None, description="Content type"),
):
    """
    Upload file with signature verification
    
    This endpoint accepts PUT requests for file uploads using presigned URLs.
    Files are stored in local storage after verifying the HMAC signature.
    """
    try:
        # Verify signature (must be a put operation)
        if operation != "put":
            logger.warning(f"Invalid operation '{operation}' for PUT request")
            raise HTTPException(status_code=400, detail="Invalid operation for PUT request")
        
        secret = settings.WEBHOOK_SIGNING_SECRET.encode() if settings.WEBHOOK_SIGNING_SECRET else b"default-secret-change-me"
        message = f"put:{bucket_type}:{key}:{expires}"
        expected_sig = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(signature, expected_sig):
            logger.warning(f"Invalid signature for file upload: {bucket_type}/{key}")
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        # Check expiration
        if time.time() > expires:
            logger.warning(f"Expired URL for file upload: {bucket_type}/{key}")
            raise HTTPException(status_code=403, detail="URL expired")
        
        # Check content length
        content_length = request.headers.get("content-length")
        if content_length:
            size = int(content_length)
            if size > MAX_UPLOAD_SIZE:
                logger.warning(f"File too large: {size} bytes (max: {MAX_UPLOAD_SIZE})")
                raise HTTPException(
                    status_code=413, 
                    detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024 * 1024)}MB"
                )
        
        # Get storage path and save file
        storage_path = get_storage_path(bucket_type)
        file_path = os.path.join(storage_path, key)
        
        # Ensure directory exists
        ensure_directory_exists(file_path)
        
        # Stream the body directly to file to avoid memory issues and timeouts
        total_bytes = 0
        try:
            with open(file_path, 'wb') as f:
                async for chunk in request.stream():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_UPLOAD_SIZE:
                        # Clean up partial file
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        raise HTTPException(
                            status_code=413, 
                            detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024 * 1024)}MB"
                        )
                    f.write(chunk)
            
            logger.info(f"Uploaded file: {file_path} ({total_bytes} bytes)")
        except Exception as stream_error:
            # Clean up partial file on error
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            raise stream_error
        
        # Create response
        response = Response(
            status_code=200,
            content=f"File uploaded successfully: {key}",
            media_type="text/plain"
        )
        
        # Add CORS headers for allowed origins (explicit for PUT requests)
        origin = request.headers.get("origin")
        add_cors_headers_if_allowed(response, origin)
        
        return response
        
    except HTTPException:
        # HTTPException will be handled by exception handlers in main.py which add CORS
        # Re-raise to let the middleware handle it
        raise
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logger.error(f"Error uploading file: {e}\n{error_traceback}")
        # Create error response with CORS headers
        error_response = Response(
            status_code=500,
            content=f"Internal server error: {str(e)}",
            media_type="text/plain"
        )
        origin = request.headers.get("origin")
        add_cors_headers_if_allowed(error_response, origin)
        return error_response

