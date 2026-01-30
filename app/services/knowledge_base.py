"""
Knowledge Base Service
Modular service for knowledge base operations including text extraction, storage, and Ultravox tool creation.
"""
import logging
import os
from typing import Optional
from pathlib import Path
from datetime import datetime
from app.core.database import DatabaseService
from app.services.text_extraction import extract_text_from_file
from app.services.ultravox import UltravoxClient
from app.core.config import settings

logger = logging.getLogger(__name__)


async def extract_and_store_content(
    file_path: str,
    file_type: str,
    kb_id: str,
    client_id: str,
    file_name: str,
    file_size: int
) -> str:
    """
    Extract text from file and store in knowledge_bases table.
    
    Args:
        file_path: Path to the uploaded file
        file_type: File type (pdf, txt, docx, md)
        kb_id: Knowledge base UUID
        client_id: Client UUID for ownership validation
        file_name: Original filename
        file_size: File size in bytes
    
    Returns:
        Extracted text content
    """
    try:
        # Map file extension to content type
        content_type_map = {
            'pdf': 'application/pdf',
            'txt': 'text/plain',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'md': 'text/markdown',
        }
        content_type = content_type_map.get(file_type.lower(), 'application/octet-stream')
        
        # Extract text from file
        logger.info(f"[KB_SERVICE] Extracting text from {file_type} file: {file_path}")
        extracted_text = await extract_text_from_file(file_path, content_type)
        
        if not extracted_text or len(extracted_text.strip()) < 10:
            raise ValueError(f"Extracted text is too short or empty from file: {file_name}")
        
        # Store in database
        db = DatabaseService()
        update_data = {
            "content": extracted_text,
            "file_type": file_type.lower(),
            "file_size": file_size,
            "file_name": file_name,
            "status": "ready",
        }
        
        db.update("knowledge_bases", {"id": kb_id, "client_id": client_id}, update_data)
        
        logger.info(f"[KB_SERVICE] Successfully stored {len(extracted_text)} characters for KB {kb_id}")
        return extracted_text
        
    except Exception as e:
        logger.error(f"[KB_SERVICE] Failed to extract and store content: {e}", exc_info=True)
        # Update status to failed
        try:
            db = DatabaseService()
            db.update("knowledge_bases", {"id": kb_id, "client_id": client_id}, {
                "status": "failed"
            })
        except:
            pass
        raise


async def get_knowledge_base_content(kb_id: str, org_id: Optional[str] = None, client_id: Optional[str] = None) -> str:
    """
    Fetch content from knowledge_bases table.
    
    Args:
        kb_id: Knowledge base UUID
        org_id: Organization ID for scoping (organization-first approach)
        client_id: Optional client ID for backward compatibility (deprecated)
    
    Returns:
        Plain text content
    """
    try:
        # CRITICAL: Use org_id for organization-first approach
        db = DatabaseService(org_id=org_id) if org_id else DatabaseService()
        filters = {"id": kb_id}
        
        # Filter by org_id if provided (preferred)
        if org_id:
            filters["clerk_org_id"] = org_id
        elif client_id:
            # Fallback to client_id for backward compatibility
            filters["client_id"] = client_id
        
        kb_record = db.select_one("knowledge_bases", filters)
        
        if not kb_record:
            raise ValueError(f"Knowledge base not found: {kb_id}")
        
        content = kb_record.get("content", "")
        if not content:
            raise ValueError(f"Knowledge base has no content: {kb_id}")
        
        return content
        
    except Exception as e:
        logger.error(f"[KB_SERVICE] Failed to get KB content: {e}", exc_info=True)
        raise


async def update_knowledge_base_content(kb_id: str, org_id: Optional[str] = None, client_id: Optional[str] = None, new_content: str = "") -> bool:
    """
    Update content field in knowledge_bases table.
    
    Args:
        kb_id: Knowledge base UUID
        org_id: Organization ID for ownership validation (organization-first approach)
        client_id: Optional client ID for backward compatibility (deprecated)
        new_content: New text content
    
    Returns:
        True if successful
    """
    try:
        # CRITICAL: Use org_id for organization-first approach
        db = DatabaseService(org_id=org_id) if org_id else DatabaseService()
        update_data = {
            "content": new_content,
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        # Filter by org_id if provided (preferred), otherwise fallback to client_id
        filters = {"id": kb_id}
        if org_id:
            filters["clerk_org_id"] = org_id
        elif client_id:
            filters["client_id"] = client_id
        else:
            raise ValueError("Either org_id or client_id must be provided")
        
        result = db.update("knowledge_bases", filters, update_data)
        
        if not result:
            raise ValueError(f"Failed to update knowledge base: {kb_id}")
        
        logger.info(f"[KB_SERVICE] Successfully updated content for KB {kb_id}")
        return True
        
    except Exception as e:
        logger.error(f"[KB_SERVICE] Failed to update KB content: {e}", exc_info=True)
        raise


async def create_ultravox_tool_for_kb(kb_id: str, kb_name: str, client_id: str) -> Optional[str]:
    """
    Create Ultravox tool that points to our backend fetch endpoint.
    
    Args:
        kb_id: Knowledge base UUID
        kb_name: Knowledge base name (for tool description)
        client_id: Client UUID
    
    Returns:
        Ultravox tool ID if successful, None otherwise
    """
    try:
        if not settings.ULTRAVOX_API_KEY:
            logger.warning("[KB_SERVICE] Ultravox API key not configured, skipping tool creation")
            return None
        
        # Get backend base URL from config
        backend_url = settings.WEBHOOK_BASE_URL or settings.FILE_SERVER_URL or "https://truedy.closi.tech"
        # Ensure no trailing slash
        backend_url = backend_url.rstrip('/')
        
        # Construct fetch endpoint URL
        fetch_endpoint = f"{backend_url}/api/v1/kb/{kb_id}/fetch"
        
        # Sanitize KB name for tool name (alphanumeric, hyphens, underscores only)
        import re
        sanitized_name = re.sub(r'[^a-zA-Z0-9_-]', '_', kb_name)[:30]  # Limit to 30 chars
        tool_name = f"kb_{sanitized_name}_{kb_id[:8]}"  # Include first 8 chars of UUID for uniqueness
        
        # Create tool definition
        tool_data = {
            "name": tool_name,
            "definition": {
                "modelToolName": "fetch_knowledge_base",  # Consistent name for LLM
                "description": f"Fetch content from knowledge base: {kb_name}",
                "dynamicParameters": [
                    {
                        "name": "kb_id",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "type": "string",
                            "description": "Knowledge base UUID"
                        },
                        "required": True
                    }
                ],
                "http": {
                    "baseUrlPattern": fetch_endpoint,
                    "httpMethod": "POST"
                },
                "requirements": {
                    "httpSecurityOptions": {
                        "options": [
                            {
                                "requirements": {
                                    "apiKey": {
                                        "headerApiKey": {
                                            "name": "X-API-Key"
                                        }
                                    }
                                }
                            }
                        ]
                    }
                },
                "precomputable": True,  # Non-mutating, can be cached
            }
        }
        
        # Create tool in Ultravox
        ultravox_client = UltravoxClient()
        response = await ultravox_client.create_tool(tool_data)
        
        tool_id = response.get("toolId")
        if not tool_id:
            raise ValueError("Ultravox did not return toolId")
        
        # Store tool ID in database
        db = DatabaseService()
        db.update("knowledge_bases", {"id": kb_id, "client_id": client_id}, {
            "ultravox_tool_id": tool_id
        })
        
        logger.info(f"[KB_SERVICE] Created Ultravox tool {tool_id} for KB {kb_id}")
        return tool_id
        
    except Exception as e:
        logger.error(f"[KB_SERVICE] Failed to create Ultravox tool for KB {kb_id}: {e}", exc_info=True)
        # Don't fail KB creation if tool creation fails (non-critical)
        return None
