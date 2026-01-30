"""
Knowledge Base API Endpoints
Handles document upload, text extraction, content management, and Ultravox tool integration.
"""
from fastapi import APIRouter, Depends, Header, Body, HTTPException
from fastapi.responses import Response, PlainTextResponse
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import logging
import os
import tempfile
import base64
from pathlib import Path
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import NotFoundError, ValidationError, ForbiddenError
from app.models.schemas import ResponseMeta
from app.core.config import settings
from app.services.knowledge_base import (
    extract_and_store_content,
    get_knowledge_base_content,
    update_knowledge_base_content,
    create_ultravox_tool_for_kb
)
from app.services.ultravox import ultravox_client

logger = logging.getLogger(__name__)
router = APIRouter()

# Maximum file size (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Allowed file types
ALLOWED_FILE_TYPES = {
    'application/pdf': 'pdf',
    'text/plain': 'txt',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/msword': 'docx',
    'text/markdown': 'md',
}

ALLOWED_EXTENSIONS = {'.pdf', '.txt', '.docx', '.doc', '.md'}


class KnowledgeBaseFetchRequest(BaseModel):
    """Request model for KB fetch endpoint (called by Ultravox)"""
    kb_id: str


class FileData(BaseModel):
    """File data with base64 encoding"""
    filename: str
    data: str  # Base64 encoded file data
    content_type: str = "application/octet-stream"


class KnowledgeBaseCreateRequest(BaseModel):
    """Request model for creating knowledge base with base64 file"""
    name: str
    description: Optional[str] = None
    file: FileData


@router.post("")
async def create_knowledge_base(
    request_data: KnowledgeBaseCreateRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Create new knowledge base with document upload (base64 encoded).
    
    JSON Request Body:
    {
        "name": "KB Name",
        "description": "Optional description",
        "file": {
            "filename": "document.pdf",
            "data": "base64_encoded_file_data",
            "content_type": "application/pdf"
        }
    }
    
    Validates file, extracts text, stores in database, and creates Ultravox tool.
    """
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        client_id = current_user.get("client_id")  # Legacy field
        
        # CRITICAL: Auto-upgrade first user to admin if they're trying to create KB
        # This handles edge cases where role upgrade didn't happen during auth
        if current_user["role"] not in ["client_admin", "agency_admin"]:
            # Try to upgrade user if they're the first/only user in their client
            if client_id:
                try:
                    from app.core.database import get_supabase_admin_client
                    admin_db = get_supabase_admin_client()
                    org_users = admin_db.table("users").select("id,role").eq("client_id", client_id).execute()
                    if org_users.data:
                        other_admins = [u for u in org_users.data if u.get("id") != current_user.get("user_id") and u.get("role") == "client_admin"]
                        if not other_admins:
                            # This user is the first admin - upgrade them immediately
                            logger.info(f"Auto-upgrading user {current_user.get('user_id')} to client_admin (first user attempting KB creation)")
                            admin_db.table("users").update({"role": "client_admin"}).eq("clerk_user_id", current_user.get("user_id")).execute()
                            # Update current_user dict for this request
                            current_user["role"] = "client_admin"
                except Exception as e:
                    logger.warning(f"Failed to auto-upgrade user role: {e}")
            
            # Check again after potential upgrade
            if current_user["role"] not in ["client_admin", "agency_admin"]:
                raise ForbiddenError("Insufficient permissions")
        
        # Validate input
        name = request_data.name.strip()
        if not name:
            raise ValidationError("Name is required")
        
        if not request_data.file:
            raise ValidationError("File is required")
        
        # Check file extension
        file_ext = Path(request_data.file.filename).suffix.lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise ValidationError(
                f"Invalid file type. Allowed types: PDF, TXT, DOCX, MD",
                {"allowed_types": list(ALLOWED_EXTENSIONS)}
            )
        
        # Determine file type
        content_type = request_data.file.content_type or ""
        file_type = ALLOWED_FILE_TYPES.get(content_type)
        if not file_type:
            # Try to infer from extension
            ext_map = {'.pdf': 'pdf', '.txt': 'txt', '.docx': 'docx', '.doc': 'docx', '.md': 'md'}
            file_type = ext_map.get(file_ext, 'txt')
        
        # Decode base64 file data to bytes
        try:
            file_content = base64.b64decode(request_data.file.data)
            file_size = len(file_content)
        except Exception as decode_error:
            logger.error(f"[KB] Failed to decode base64 file data: {decode_error}", exc_info=True)
            raise ValidationError(f"Invalid base64 file data: {str(decode_error)}")
        
        # Validate file size
        if file_size > MAX_FILE_SIZE:
            raise ValidationError(
                f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
                {"max_size_mb": MAX_FILE_SIZE // (1024 * 1024), "file_size_mb": file_size // (1024 * 1024)}
            )
        
        if file_size == 0:
            raise ValidationError("File is empty")
        
        # Create KB record with status='creating'
        kb_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        kb_record = {
            "id": kb_id,
            "client_id": client_id,  # Legacy field
            "clerk_org_id": clerk_org_id,  # CRITICAL: Organization ID for data partitioning
            "name": name,
            "description": request_data.description,
            "language": "en-US",
            "status": "creating",
            "file_name": request_data.file.filename,
            "file_type": file_type,
            "file_size": file_size,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        db.insert("knowledge_bases", kb_record)
        
        # Save file temporarily for text extraction
        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name
            
            # Extract text and store content (pass org_id)
            extracted_text = await extract_and_store_content(
                file_path=temp_file_path,
                file_type=file_type,
                kb_id=kb_id,
                client_id=clerk_org_id,  # Pass org_id as client_id for backward compatibility
                file_name=request_data.file.filename,
                file_size=file_size
            )
            
            # Create Ultravox tool (non-blocking - don't fail if this fails)
            ultravox_tool_id = None
            try:
                ultravox_tool_id = await create_ultravox_tool_for_kb(kb_id, name, client_id)
            except Exception as tool_error:
                logger.warning(f"[KB] Failed to create Ultravox tool (non-critical): {tool_error}", exc_info=True)
            
            # Fetch updated record - filter by org_id to enforce org scoping
            updated_kb = db.select_one("knowledge_bases", {"id": kb_id, "clerk_org_id": clerk_org_id})
            
            return {
                "data": updated_kb,
                "meta": ResponseMeta(
                    request_id=str(uuid.uuid4()),
                    ts=datetime.utcnow(),
                ),
            }
            
        finally:
            # Clean up temp file
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except:
                    pass
        
    except (ValidationError, ForbiddenError):
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[KB] [CREATE] Failed to create knowledge base (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to create knowledge base: {str(e)}")


@router.get("")
async def list_knowledge_bases(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    List all knowledge bases for current organization.
    
    CRITICAL: Filters by clerk_org_id to show shared knowledge bases across the team.
    """
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        
        # Filter by org_id instead of client_id - shows all organization knowledge bases
        kb_list = db.select("knowledge_bases", {"clerk_org_id": clerk_org_id}, order_by="created_at DESC")
        
        return {
            "data": list(kb_list),
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
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[KB] [LIST] Failed to list knowledge bases (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to list knowledge bases: {str(e)}")


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get single knowledge base with content"""
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        
        # Filter by org_id instead of client_id
        kb_record = db.select_one("knowledge_bases", {"id": kb_id, "clerk_org_id": clerk_org_id})
        
        if not kb_record:
            raise NotFoundError("knowledge_base", kb_id)
        
        return {
            "data": kb_record,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except NotFoundError:
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[KB] [GET] Failed to get knowledge base (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to get knowledge base: {str(e)}")


@router.put("/{kb_id}")
async def update_knowledge_base(
    kb_id: str,
    request_data: Dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update knowledge base (name, description, or content)"""
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        
        # Verify KB exists and belongs to organization - filter by org_id instead of client_id
        kb_record = db.select_one("knowledge_bases", {"id": kb_id, "clerk_org_id": clerk_org_id})
        if not kb_record:
            raise NotFoundError("knowledge_base", kb_id)
        
        # Build update data
        update_data = {}
        if "name" in request_data:
            update_data["name"] = request_data["name"]
        if "description" in request_data:
            update_data["description"] = request_data["description"]
        
        # Update content separately using service function (handles its own DB update)
        if "content" in request_data:
            await update_knowledge_base_content(kb_id, org_id=clerk_org_id, new_content=request_data["content"])
        
        # Update other fields (name, description) if provided
        if update_data:
            update_data["updated_at"] = datetime.utcnow().isoformat()
            db.update("knowledge_bases", {"id": kb_id, "clerk_org_id": clerk_org_id}, update_data)
        
        # Fetch updated record - filter by org_id instead of client_id
        updated_kb = db.select_one("knowledge_bases", {"id": kb_id, "clerk_org_id": clerk_org_id})
        
        return {
            "data": updated_kb,
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except NotFoundError:
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[KB] [UPDATE] Failed to update knowledge base (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to update knowledge base: {str(e)}")


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete knowledge base and associated Ultravox tool"""
    try:
        # CRITICAL: Use clerk_org_id for organization-first approach
        clerk_org_id = current_user.get("clerk_org_id")
        if not clerk_org_id:
            raise ValidationError("Missing organization ID in token")
        
        # Initialize database service with org_id context
        db = DatabaseService(org_id=clerk_org_id)
        
        # Get KB record to check for Ultravox tool - filter by org_id instead of client_id
        kb_record = db.select_one("knowledge_bases", {"id": kb_id, "clerk_org_id": clerk_org_id})
        if not kb_record:
            raise NotFoundError("knowledge_base", kb_id)
        
        # Delete Ultravox tool if exists
        ultravox_tool_id = kb_record.get("ultravox_tool_id")
        if ultravox_tool_id:
            try:
                await ultravox_client.delete_tool(ultravox_tool_id)
                logger.info(f"[KB] Deleted Ultravox tool {ultravox_tool_id} for KB {kb_id}")
            except Exception as tool_error:
                logger.warning(f"[KB] Failed to delete Ultravox tool (non-critical): {tool_error}", exc_info=True)
        
        # Delete KB record - filter by org_id instead of client_id
        db.delete("knowledge_bases", {"id": kb_id, "clerk_org_id": clerk_org_id})
        
        return {
            "data": {"success": True},
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    except NotFoundError:
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[KB] [DELETE] Failed to delete knowledge base (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to delete knowledge base: {str(e)}")


@router.post("/{kb_id}/fetch")
async def fetch_knowledge_base_content(
    kb_id: str,
    request_data: KnowledgeBaseFetchRequest = Body(...),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Fetch KB content for Ultravox tool calls (internal endpoint).
    
    This endpoint is called by Ultravox during calls. Uses API key authentication.
    """
    try:
        # Validate API key
        if not settings.KB_FETCH_API_KEY:
            logger.error("[KB] [FETCH] KB_FETCH_API_KEY not configured")
            raise HTTPException(status_code=500, detail="KB fetch endpoint not configured")
        
        if not x_api_key or x_api_key != settings.KB_FETCH_API_KEY:
            logger.warning(f"[KB] [FETCH] Invalid API key provided")
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        # Validate kb_id matches path parameter
        if request_data.kb_id != kb_id:
            raise HTTPException(status_code=400, detail="kb_id in body must match path parameter")
        
        # Fetch content (no client_id validation - API key is sufficient)
        # Note: We skip client_id validation for Ultravox tool calls
        content = await get_knowledge_base_content(kb_id, client_id=None)
        
        # Return plain text
        return PlainTextResponse(content, media_type="text/plain")
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "full_traceback": traceback.format_exc(),
        }
        logger.error(f"[KB] [FETCH] Failed to fetch KB content (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch knowledge base content: {str(e)}")
