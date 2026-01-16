"""
Knowledge Base Endpoints
"""
from fastapi import APIRouter, Header, Depends
from starlette.requests import Request
from typing import Optional
from datetime import datetime
import uuid
import json

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.storage import generate_presigned_url, check_object_exists
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_knowledge_base_created, emit_knowledge_base_ingestion_started
from app.services.ultravox import ultravox_client
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
        s3_key = f"uploads/client_{current_user['client_id']}/kb/{kb_id}/file_{i}.{file.filename.split('.')[-1]}"
        
        url = generate_presigned_url(
            bucket=settings.S3_BUCKET_UPLOADS,
            key=s3_key,
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
                "s3_key": s3_key,
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
    """Ingest uploaded files into knowledge base"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    kb = db.get_knowledge_base(kb_id, current_user["client_id"])
    if not kb:
        raise NotFoundError("knowledge_base", kb_id)
    
    if kb.get("status") != "ready":
        raise ValidationError("Knowledge base must be ready", {"kb_status": kb.get("status")})
    
    results = []
    for doc_id in request_data.document_ids:
        doc = db.select_one("knowledge_base_documents", {"id": doc_id, "knowledge_base_id": kb_id})
        if not doc:
            continue
        
        # Check storage file exists
        if not check_object_exists(settings.S3_BUCKET_UPLOADS, doc["s3_key"]):
            db.update(
                "knowledge_base_documents",
                {"id": doc_id},
                {"status": "failed", "error_message": "File not found in storage"},
            )
            continue
        
        # Generate presigned URL for Ultravox
        file_url = generate_presigned_url(
            bucket=settings.S3_BUCKET_UPLOADS,
            key=doc["s3_key"],
            operation="get_object",
            expires_in=86400,
        )
        
        # Update status
        db.update(
            "knowledge_base_documents",
            {"id": doc_id},
            {"status": "processing"},
        )
        
        # Call Ultravox API
        try:
            ultravox_data = {
                "type": "file",
                "url": file_url,
                "metadata": {
                    "fileName": doc["s3_key"].split("/")[-1],
                    "fileType": doc["file_type"],
                },
            }
            ultravox_response = await ultravox_client.add_corpus_source(kb["ultravox_corpus_id"], ultravox_data)
            
            db.update(
                "knowledge_base_documents",
                {"id": doc_id},
                {
                    "ultravox_source_id": ultravox_response.get("id"),
                    "status": "processing",
                },
            )
            
            results.append({
                "doc_id": doc_id,
                "status": "processing",
                "ultravox_source_id": ultravox_response.get("id"),
            })
        except Exception as e:
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

