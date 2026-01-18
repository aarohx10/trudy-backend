"""
Knowledge Base Endpoints
"""
from fastapi import APIRouter, Header, Depends, Request, HTTPException
from starlette.requests import Request
from typing import Optional, Dict, Any
from datetime import datetime
import uuid
import json
import logging
import os

from app.core.auth import get_current_user, verify_ultravox_signature
from app.core.database import DatabaseService, DatabaseAdminService
from app.core.storage import generate_presigned_url, check_object_exists, get_file_path
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_knowledge_base_created, emit_knowledge_base_ingestion_started
from app.services.ultravox import ultravox_client
from app.services.embeddings import generate_embeddings_batch
from app.services.text_extraction import extract_text_from_file, chunk_text
from app.models.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseResponse,
    KBFilePresignRequest,
    KBFileIngestRequest,
    PresignResponse,
    ResponseMeta,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("")
async def create_knowledge_base(
    kb_data: KnowledgeBaseCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create knowledge base"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Check idempotency key
    body_dict = kb_data.dict() if hasattr(kb_data, 'dict') else json.loads(json.dumps(kb_data, default=str))
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
    
    # Create knowledge base record
    kb_id = str(uuid.uuid4())
    kb_record = {
        "id": kb_id,
        "client_id": current_user["client_id"],
        "name": kb_data.name,
        "description": kb_data.description,
        "language": kb_data.language,
        "status": "creating",
    }
    
    db.insert("knowledge_documents", kb_record)
    
    # Call Ultravox API
    try:
        ultravox_data = {
            "name": kb_data.name,
            "language": kb_data.language,
        }
        ultravox_response = await ultravox_client.create_corpus(ultravox_data)
        
        # Update with Ultravox ID
        db.update(
            "knowledge_documents",
            {"id": kb_id},
            {
                "ultravox_corpus_id": ultravox_response.get("id"),
                "status": "ready",
            },
        )
        kb_record["ultravox_corpus_id"] = ultravox_response.get("id")
        kb_record["status"] = "ready"
        
    except Exception as e:
        db.update(
            "knowledge_documents",
            {"id": kb_id},
            {"status": "failed"},
        )
        raise
    
    # Emit event
    await emit_knowledge_base_created(
        kb_id=kb_id,
        client_id=current_user["client_id"],
        ultravox_corpus_id=kb_record["ultravox_corpus_id"],
    )
    
    response_data = {
        "data": KnowledgeBaseResponse(**kb_record),
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


@router.post("/{kb_id}/files/presign")
async def presign_kb_files(
    kb_id: str,
    request_data: KBFilePresignRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get presigned URLs for knowledge base file uploads"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    documents = []
    for i, file in enumerate(request_data.files):
        doc_id = str(uuid.uuid4())
        storage_key = f"uploads/client_{current_user['client_id']}/kb/{kb_id}/file_{i}.{file.filename.split('.')[-1]}"
        
        url = generate_presigned_url(
            bucket=settings.STORAGE_BUCKET_UPLOADS,
            key=storage_key,
            operation="put_object",
            expires_in=3600,
            content_type=file.content_type,
        )
        
        # Create document record
        db.insert(
            "knowledge_base_documents",
            {
                "id": doc_id,
                "client_id": current_user["client_id"],
                "knowledge_base_id": kb_id,
                "s3_key": storage_key,  # Database column name remains s3_key for backward compatibility
                "file_type": file.content_type,
                "file_size": file.file_size,
                "status": "pending_upload",
            },
        )
        
        documents.append({
            "doc_id": doc_id,
            "url": url,
            "headers": {"Content-Type": file.content_type},
        })
    
    return {
        "data": {"documents": documents},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{kb_id}/files/ingest")
async def ingest_kb_files(
    kb_id: str,
    request_data: KBFileIngestRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Ingest uploaded files into knowledge base using Proxy Knowledge Base system.
    Extracts text, chunks it, generates embeddings, and stores in Supabase pgvector.
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    admin_db = DatabaseAdminService()  # For batch inserts
    
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    if kb.get("status") != "ready":
        raise ValidationError("Knowledge base must be ready", {"kb_status": kb.get("status")})
    
    # Check if tool already exists, if not create it
    tool_name = f"search_kb_{kb_id}"
    ultravox_tool_id = kb.get("ultravox_tool_id")
    
    if not ultravox_tool_id:
        # Create durable tool in Ultravox
        try:
            backend_url = settings.FILE_SERVER_URL or "https://api.truedy.ai"
            tool_endpoint = f"{backend_url}/api/v1/kb/query"
            
            tool_data = await ultravox_client.create_durable_tool(
                name=tool_name,
                description=f"Search the knowledge base '{kb.get('name', kb_id)}' for relevant information to answer questions accurately.",
                endpoint=tool_endpoint,
                http_method="POST",
                dynamic_parameters=[
                    {
                        "name": "query",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "type": "string",
                            "description": "The search query to find relevant information in the knowledge base",
                        },
                        "required": True,
                    }
                ],
                static_parameters=[
                    {
                        "name": "kb_id",
                        "location": "PARAMETER_LOCATION_BODY",
                        "value": kb_id,
                    }
                ],
            )
            
            ultravox_tool_id = tool_data.get("toolId")
            if ultravox_tool_id:
                db.update(
                    "knowledge_documents",
                    {"id": kb_id},
                    {"ultravox_tool_id": ultravox_tool_id},
                )
                kb["ultravox_tool_id"] = ultravox_tool_id
        except Exception as e:
            logger.error(f"Failed to create Ultravox tool for KB {kb_id}: {e}", exc_info=True)
            raise ValidationError(f"Failed to register knowledge base tool: {str(e)}")
    
    results = []
    for doc_id in request_data.document_ids:
        doc = db.select_one("knowledge_base_documents", {"id": doc_id, "knowledge_base_id": kb_id})
        if not doc:
            continue
        
        # Check storage file exists
        storage_key = doc.get("s3_key")  # Database column name remains s3_key for backward compatibility
        if not check_object_exists(settings.STORAGE_BUCKET_UPLOADS, storage_key):
            db.update(
                "knowledge_base_documents",
                {"id": doc_id},
                {"status": "failed", "error_message": "File not found in storage"},
            )
            results.append({
                "doc_id": doc_id,
                "status": "failed",
                "error_message": "File not found in storage",
            })
            continue
        
        # Update status
        db.update(
            "knowledge_base_documents",
            {"id": doc_id},
            {"status": "processing"},
        )
        
        try:
            # Get file path
            file_path = get_file_path("uploads", storage_key)
            
            # Extract text from file
            extracted_text = await extract_text_from_file(file_path, doc.get("file_type", ""))
            
            if not extracted_text or not extracted_text.strip():
                raise ValidationError(f"Could not extract text from file {doc_id}")
            
            # Chunk text (1000 chars with 200 overlap)
            chunks = chunk_text(extracted_text, chunk_size=1000, overlap=200)
            
            if not chunks:
                raise ValidationError(f"No chunks generated from file {doc_id}")
            
            # Generate embeddings for all chunks
            logger.info(f"Generating embeddings for {len(chunks)} chunks from document {doc_id}")
            embeddings = await generate_embeddings_batch(chunks, batch_size=100)
            
            if len(embeddings) != len(chunks):
                raise ValidationError(f"Embedding count mismatch: {len(embeddings)} != {len(chunks)}")
            
            # Prepare chunks for batch insert
            chunk_records = []
            for i, (chunk_content, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_records.append({
                    "kb_id": kb_id,
                    "content": chunk_content,
                    "embedding": embedding,
                })
            
            # Batch insert chunks
            logger.info(f"Inserting {len(chunk_records)} chunks into database")
            admin_db.insert_chunks_batch(chunk_records)
            
            # Update document status
            db.update(
                "knowledge_base_documents",
                {"id": doc_id},
                {
                    "status": "indexed",
                    "chunk_count": len(chunks),
                },
            )
            
            results.append({
                "doc_id": doc_id,
                "status": "indexed",
                "chunk_count": len(chunks),
            })
            
        except Exception as e:
            logger.error(f"Failed to ingest document {doc_id}: {e}", exc_info=True)
            db.update(
                "knowledge_base_documents",
                {"id": doc_id},
                {"status": "failed", "error_message": str(e)},
            )
            results.append({
                "doc_id": doc_id,
                "status": "failed",
                "error_message": str(e),
            })
    
    return {
        "data": {"documents": results},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("")
async def list_knowledge_bases(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List knowledge bases with filtering and pagination"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Build filters
    filters = {"client_id": current_user["client_id"]}
    if status:
        filters["status"] = status
    
    # Get knowledge bases with pagination
    all_kb = db.select("knowledge_documents", filters, order_by="created_at")
    
    # Apply pagination manually
    total = len(all_kb)
    paginated_kb = all_kb[offset:offset + limit]
    
    return {
        "data": [KnowledgeBaseResponse(**kb) for kb in paginated_kb],
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


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get knowledge base"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    # Get document counts
    documents = db.select("knowledge_base_documents", {"knowledge_base_id": kb_id})
    document_counts = {
        "total": len(documents),
        "indexed": sum(1 for d in documents if d.get("status") == "indexed"),
        "processing": sum(1 for d in documents if d.get("status") == "processing"),
        "failed": sum(1 for d in documents if d.get("status") == "failed"),
    }
    
    kb["document_counts"] = document_counts
    
    return {
        "data": KnowledgeBaseResponse(**kb),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/{kb_id}")
async def update_knowledge_base(
    kb_id: str,
    kb_data: KnowledgeBaseUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update knowledge base"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if knowledge base exists
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    # Only allow updates for knowledge bases that are not actively processing
    if kb.get("status") in ["processing", "indexing"]:
        raise ValidationError("Knowledge base cannot be updated while processing or indexing")
    
    # Prepare update data (only non-None fields)
    update_data = kb_data.dict(exclude_unset=True)
    if not update_data:
        # No updates provided
        return {
            "data": KnowledgeBaseResponse(**kb),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    # Update database
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("knowledge_documents", {"id": kb_id}, update_data)
    
    # Get updated knowledge base
    updated_kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    
    return {
        "data": KnowledgeBaseResponse(**updated_kb),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/kb/query")
async def query_knowledge_base(
    request: Request,
    authenticated: bool = Depends(verify_ultravox_signature),
):
    """
    Public endpoint for Ultravox to query knowledge bases.
    Bypasses Clerk auth but requires X-Tool-Secret header for security.
    """
    
    try:
        body = await request.json()
        kb_id = body.get("kb_id")
        query_text = body.get("query")
        
        if not kb_id or not query_text:
            raise HTTPException(status_code=400, detail="Missing required fields: kb_id and query")
        
        # Use admin DB to bypass RLS
        admin_db = DatabaseAdminService()
        
        # Generate embedding for query
        from app.services.embeddings import generate_embedding
        query_embedding = await generate_embedding(query_text)
        
        # Search for matching documents
        matches = admin_db.match_kb_documents(
            query_embedding=query_embedding,
            kb_id=kb_id,
            match_threshold=0.7,
            match_count=3,
        )
        
        # Combine top 3 snippets into a single string
        if not matches:
            return {
                "data": {
                    "result": "No relevant information found in the knowledge base.",
                },
            }
        
        # Combine chunks with their content
        combined_result = "\n\n".join([
            f"[Relevance: {match.get('similarity', 0):.2%}]\n{match.get('content', '')}"
            for match in matches
        ])
        
        return {
            "data": {
                "result": combined_result,
                "matches_count": len(matches),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to query knowledge base: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete knowledge base"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if knowledge base exists
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    # Only allow deletion for knowledge bases that are not actively processing
    if kb.get("status") in ["processing", "indexing"]:
        raise ValidationError("Knowledge base cannot be deleted while processing or indexing")
    
    # Delete knowledge base documents first
    try:
        documents = db.select("knowledge_base_documents", {"knowledge_base_id": kb_id})
        for doc in documents:
            db.delete("knowledge_base_documents", {"id": doc["id"]})
    except Exception:
        pass  # Continue even if documents deletion fails
    
    # Delete knowledge base
    db.delete("knowledge_documents", {"id": kb_id})
    
    return {
        "data": {"id": kb_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

