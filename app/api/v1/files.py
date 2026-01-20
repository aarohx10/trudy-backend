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
import logging
from app.core.storage import get_file_path, check_file_exists, get_storage_path, ensure_directory_exists
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum file size for uploads (50MB)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024


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
        
        # Read the body
        body = await request.body()
        
        # Double-check size after reading
        if len(body) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, 
                detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024 * 1024)}MB"
            )
        
        # Get storage path and save file
        storage_path = get_storage_path(bucket_type)
        file_path = os.path.join(storage_path, key)
        
        # Ensure directory exists
        ensure_directory_exists(file_path)
        
        # Write file
        with open(file_path, 'wb') as f:
            f.write(body)
        
        logger.info(f"Uploaded file: {file_path} ({len(body)} bytes)")
        
        return Response(
            status_code=200,
            content=f"File uploaded successfully: {key}",
            media_type="text/plain"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

