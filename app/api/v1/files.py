"""
File Serving Endpoint (Hetzner VPS)
Serves files with signature verification (replaces direct storage access)
"""
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse
import hmac
import hashlib
import time
import logging
from app.core.storage import get_file_path, check_file_exists
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


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

