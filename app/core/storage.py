"""
Local File Storage Service (Hetzner VPS)
Local file system storage for uploads and recordings
"""
import os
import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# Storage paths (defaults to ./storage for localhost)
STORAGE_BASE_PATH = getattr(settings, 'FILE_STORAGE_PATH', './storage')
UPLOADS_PATH = os.path.join(STORAGE_BASE_PATH, "uploads")
RECORDINGS_PATH = os.path.join(STORAGE_BASE_PATH, "recordings")


def get_storage_path(bucket_type: str) -> str:
    """
    Get storage path for bucket type
    
    Args:
        bucket_type: "uploads" or "recordings"
    
    Returns:
        Full path to storage directory
    """
    # Normalize bucket type
    bucket_lower = bucket_type.lower()
    
    if bucket_lower == "uploads" or "uploads" in bucket_lower:
        return UPLOADS_PATH
    elif bucket_lower == "recordings" or "recordings" in bucket_lower:
        return RECORDINGS_PATH
    else:
        # Default to uploads if unclear
        logger.warning(f"Unknown bucket type '{bucket_type}', defaulting to uploads")
        return UPLOADS_PATH


def ensure_directory_exists(file_path: str):
    """Ensure directory exists for file"""
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
        logger.debug(f"Created directory: {directory}")


def generate_presigned_url(
    bucket: str,
    key: str,
    operation: str = "put_object",
    expires_in: int = 3600,
    content_type: Optional[str] = None,
) -> str:
    """
    Generate signed URL for file access
    
    Args:
        bucket: Bucket name (maps to "uploads" or "recordings")
        key: File key/path
        operation: "put_object" or "get_object"
        expires_in: URL expiration in seconds
        content_type: Content type for PUT operations
    
    Returns:
        Signed URL
    """
    try:
        # Map bucket to bucket type
        bucket_lower = bucket.lower()
        if "uploads" in bucket_lower:
            bucket_type = "uploads"
        elif "recordings" in bucket_lower:
            bucket_type = "recordings"
        else:
            bucket_type = "uploads"  # Default
        
        # Map operation
        op = "get" if operation == "get_object" else "put"
        
        expires_at = int(time.time()) + expires_in
        secret_key = getattr(settings, 'WEBHOOK_SIGNING_SECRET', '') or 'default-secret-change-me'
        secret = secret_key.encode()
        
        # Create signature
        message = f"{op}:{bucket_type}:{key}:{expires_at}"
        signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        
        # Build URL
        base_url = getattr(settings, 'FILE_SERVER_URL', 'http://localhost:8000')
        params = {
            "key": key,
            "expires": expires_at,
            "signature": signature,
            "operation": op,
        }
        if content_type:
            params["content_type"] = content_type
        
        url = f"{base_url}/api/v1/files/{bucket_type}?{urlencode(params)}"
        logger.debug(f"Generated signed URL for {bucket_type}/{key}")
        return url
        
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "bucket": bucket,
            "key": key,
            "operation": operation,
        }
        logger.error(f"[STORAGE] Error generating signed URL (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise


def check_object_exists(bucket: str, key: str) -> bool:
    """
    Check if file exists in storage
    
    Args:
        bucket: Bucket name
        key: File key/path
    
    Returns:
        True if file exists, False otherwise
    """
    try:
        # Map bucket to bucket type
        bucket_lower = bucket.lower()
        if "uploads" in bucket_lower:
            bucket_type = "uploads"
        elif "recordings" in bucket_lower:
            bucket_type = "recordings"
        else:
            bucket_type = "uploads"  # Default
        
        storage_path = get_storage_path(bucket_type)
        file_path = os.path.join(storage_path, key)
        exists = os.path.exists(file_path)
        logger.debug(f"Checked file existence: {file_path} -> {exists}")
        return exists
    except Exception as e:
        logger.error(f"Error checking file existence: {e}")
        return False


def upload_bytes(
    bucket: str,
    key: str,
    data: bytes,
    content_type: Optional[str] = None,
) -> str:
    """
    Upload bytes data directly to local storage
    
    Args:
        bucket: Bucket name
        key: File key/path
        data: Bytes data to upload
        content_type: Content type
    
    Returns:
        URL of the uploaded file
    """
    try:
        # Map bucket to bucket type
        bucket_lower = bucket.lower()
        if "uploads" in bucket_lower:
            bucket_type = "uploads"
        elif "recordings" in bucket_lower:
            bucket_type = "recordings"
        else:
            bucket_type = "uploads"  # Default
        
        storage_path = get_storage_path(bucket_type)
        file_path = os.path.join(storage_path, key)
        
        # Ensure directory exists
        ensure_directory_exists(file_path)
        
        # Write bytes to file
        with open(file_path, 'wb') as f:
            f.write(data)
        
        # Generate URL
        base_url = getattr(settings, 'FILE_SERVER_URL', 'http://localhost:8000')
        file_url = f"{base_url}/api/v1/files/{bucket_type}/{key}"
        
        logger.info(f"Uploaded {len(data)} bytes to: {file_path}")
        return file_url
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "bucket": bucket,
            "key": key,
            "data_size": len(data) if data else 0,
        }
        logger.error(f"[STORAGE] Error uploading bytes (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise


def get_file_path(bucket_type: str, key: str) -> str:
    """
    Get full file path
    
    Args:
        bucket_type: "uploads" or "recordings"
        key: File key/path
    
    Returns:
        Full file path
    """
    storage_path = get_storage_path(bucket_type)
    return os.path.join(storage_path, key)


def check_file_exists(bucket_type: str, key: str) -> bool:
    """
    Check if file exists (wrapper for files.py endpoint)
    
    Args:
        bucket_type: "uploads" or "recordings"
        key: File key/path
    
    Returns:
        True if file exists, False otherwise
    """
    try:
        file_path = get_file_path(bucket_type, key)
        exists = os.path.exists(file_path)
        logger.debug(f"Checked file existence: {file_path} -> {exists}")
        return exists
    except Exception as e:
        logger.error(f"Error checking file existence: {e}")
        return False

