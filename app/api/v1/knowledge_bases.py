"""
Knowledge Base Endpoints
"""
from fastapi import APIRouter, Header, Depends, Request, HTTPException, UploadFile
from starlette.requests import Request
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path
import uuid
import json
import logging
import os
import httpx
import tempfile

from app.core.auth import get_current_user, verify_ultravox_signature
from app.core.database import DatabaseService, DatabaseAdminService
from app.core.storage import generate_presigned_url, check_object_exists, get_file_path
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_knowledge_base_created, emit_knowledge_base_ingestion_started
from app.services.ultravox import ultravox_client
from app.services.embeddings import generate_embeddings_batch
from app.services.text_extraction import extract_text_from_file, extract_text_from_url, chunk_text
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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "kb_id": kb_id,
            "kb_data": kb_data if 'kb_data' in locals() else None,
        }
        logger.error(f"[KB] [CREATE] Failed to create knowledge base (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        
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
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "kb_id": kb_id,
                "kb": kb if 'kb' in locals() else None,
            }
            logger.error(f"[KB] [CREATE] Failed to create Ultravox tool (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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
            import traceback
            import json
            error_details_raw = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_args": e.args if hasattr(e, 'args') else None,
                "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                "full_traceback": traceback.format_exc(),
                "doc_id": doc_id,
                "kb_id": kb_id if 'kb_id' in locals() else None,
            }
            logger.error(f"[KB] [INGEST] Failed to ingest document (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
            
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
    """Get knowledge base with documents list"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    # Get documents list
    documents = db.select("knowledge_base_documents", {"knowledge_base_id": kb_id}, order_by="created_at")
    document_counts = {
        "total": len(documents),
        "indexed": sum(1 for d in documents if d.get("status") == "indexed"),
        "processing": sum(1 for d in documents if d.get("status") == "processing"),
        "failed": sum(1 for d in documents if d.get("status") == "failed"),
    }
    
    kb["document_counts"] = document_counts
    kb["documents"] = [
        {
            "id": doc.get("id"),
            "name": doc.get("s3_key") or "Unknown",
            "type": doc.get("file_type") or "unknown",
            "size": doc.get("file_size") or 0,
            "status": doc.get("status") or "unknown",
            "chunk_count": doc.get("chunk_count") or 0,
            "created_at": doc.get("created_at"),
        }
        for doc in documents
    ]
    
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


@router.post("/{kb_id}/add-content")
async def add_content_to_knowledge_base(
    kb_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Muscular RAG endpoint: Direct-to-Vector content addition.
    
    Accepts either:
    - url: str (optional) - Web URL to fetch and index
    - file: UploadFile (optional) - File to upload and index
    
    Processing flow:
    1. Extract text (from URL or file)
    2. Chunk text
    3. Generate embeddings
    4. Insert chunks into database
    5. Return status immediately
    
    FormData (multipart/form-data):
    - url: str (optional)
    - file: UploadFile (optional)
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    admin_db = DatabaseAdminService()  # For batch inserts
    
    # Verify knowledge base exists
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    if kb.get("status") != "ready":
        raise ValidationError("Knowledge base must be ready", {"kb_status": kb.get("status")})
    
    # Parse form data
    form = await request.form()
    url = form.get("url")
    file_item = form.get("file")
    
    if not url and not file_item:
        raise ValidationError("Either 'url' or 'file' must be provided")
    
    if url and file_item:
        raise ValidationError("Provide either 'url' or 'file', not both")
    
    # Create document record
    doc_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    try:
        extracted_text = ""
        source_name = ""
        file_type = ""
        file_size = 0
        
        # Extract text based on input type
        if url:
            # Action: Fetch URL and extract text
            logger.info(f"[KB] [ADD-CONTENT] Fetching URL | kb_id={kb_id} | url={url}")
            extracted_text = await extract_text_from_url(url)
            source_name = url
            file_type = "url"
            file_size = len(extracted_text.encode('utf-8'))
        else:
            # Action: Extract text from file
            if not isinstance(file_item, UploadFile):
                raise ValidationError("Invalid file upload")
            
            filename = file_item.filename or "unknown"
            file_type = file_item.content_type or ""
            file_content = await file_item.read()
            file_size = len(file_content)
            
            # Validate file size (20MB max for direct processing)
            if file_size > 20 * 1024 * 1024:
                raise ValidationError("File exceeds 20MB limit. Use presign/ingest flow for large files.")
            
            # Save to temporary file for extraction
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp_file:
                tmp_file.write(file_content)
                tmp_path = tmp_file.name
            
            try:
                logger.info(f"[KB] [ADD-CONTENT] Extracting text from file | kb_id={kb_id} | filename={filename}")
                extracted_text = await extract_text_from_file(tmp_path, file_type)
                source_name = filename
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        
        if not extracted_text or not extracted_text.strip():
            raise ValidationError("Could not extract text from source")
        
        # Create document record
        doc_record = {
            "id": doc_id,
            "client_id": current_user["client_id"],
            "knowledge_base_id": kb_id,
            "s3_key": source_name,  # Store source name/URL
            "file_type": file_type,
            "file_size": file_size,
            "status": "processing",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        db.insert("knowledge_base_documents", doc_record)
        logger.info(f"[KB] [ADD-CONTENT] Document record created | doc_id={doc_id} | kb_id={kb_id}")
        
        # Chunk text (1000 chars with 200 overlap)
        logger.info(f"[KB] [ADD-CONTENT] Chunking text | doc_id={doc_id} | text_length={len(extracted_text)}")
        chunks = chunk_text(extracted_text, chunk_size=1000, overlap=200)
        
        if not chunks:
            raise ValidationError("No chunks generated from extracted text")
        
        # Generate embeddings for all chunks
        logger.info(f"[KB] [ADD-CONTENT] Generating embeddings | doc_id={doc_id} | chunks={len(chunks)}")
        embeddings = await generate_embeddings_batch(chunks, batch_size=100)
        
        if len(embeddings) != len(chunks):
            raise ValidationError(f"Embedding count mismatch: {len(embeddings)} != {len(chunks)}")
        
        # Prepare chunks for batch insert
        chunk_records = []
        for chunk_content, embedding in zip(chunks, embeddings):
            chunk_records.append({
                "kb_id": kb_id,
                "content": chunk_content,
                "embedding": embedding,
            })
        
        # Batch insert chunks
        logger.info(f"[KB] [ADD-CONTENT] Inserting chunks | doc_id={doc_id} | chunks={len(chunk_records)}")
        admin_db.insert_chunks_batch(chunk_records)
        
        # Update document status
        db.update(
            "knowledge_base_documents",
            {"id": doc_id},
            {
                "status": "indexed",
                "chunk_count": len(chunks),
                "updated_at": datetime.utcnow().isoformat(),
            },
        )
        
        logger.info(f"[KB] [ADD-CONTENT] Content indexed successfully | doc_id={doc_id} | kb_id={kb_id} | chunks={len(chunks)}")
        
        return {
            "data": {
                "doc_id": doc_id,
                "status": "indexed",
                "chunk_count": len(chunks),
                "source": source_name,
            },
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
        
    except (ValidationError, NotFoundError, ForbiddenError):
        # Re-raise known errors
        raise
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "error_module": getattr(e, '__module__', None),
            "error_class": type(e).__name__,
            "full_traceback": traceback.format_exc(),
            "doc_id": doc_id,
            "kb_id": kb_id,
            "content_type": content_type if 'content_type' in locals() else None,
        }
        logger.error(f"[KB] [ADD-CONTENT] Failed to add content (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        
        # Update document status to failed
        try:
            db.update(
                "knowledge_base_documents",
                {"id": doc_id},
                {
                    "status": "failed",
                    "error_message": str(e),
                    "updated_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception as update_error:
            import traceback
            import json
            update_error_details = {
                "error_type": type(update_error).__name__,
                "error_message": str(update_error),
                "full_traceback": traceback.format_exc(),
                "doc_id": doc_id,
            }
            logger.error(f"[KB] [ADD-CONTENT] Failed to update document status (RAW ERROR): {json.dumps(update_error_details, indent=2, default=str)}", exc_info=True)
        raise ValidationError(f"Failed to add content to knowledge base: {str(e)}")


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
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
            "full_traceback": traceback.format_exc(),
            "kb_id": kb_id,
            "query": query if 'query' in locals() else None,
        }
        logger.error(f"[KB] [QUERY] Failed to query knowledge base (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
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

