"""
Campaign Endpoints
"""
from fastapi import APIRouter, Header, Depends
from starlette.requests import Request
from typing import Optional
from datetime import datetime
import uuid
import csv
import io
import json
import logging

logger = logging.getLogger(__name__)

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.storage import generate_presigned_url
import os
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.core.idempotency import check_idempotency_key, store_idempotency_response
from app.core.events import emit_campaign_created, emit_campaign_scheduled
from app.services.ultravox import ultravox_client
from app.models.schemas import (
    CampaignCreate,
    CampaignUpdate,
    CampaignContactsUpload,
    CampaignResponse,
    BulkDeleteRequest,
    BulkDeleteResponse,
    ResponseMeta,
)
from app.core.config import settings

router = APIRouter()


@router.post("")
async def create_campaign(
    campaign_data: CampaignCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Create campaign"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    # Check idempotency key
    body_dict = campaign_data.dict() if hasattr(campaign_data, 'dict') else json.loads(json.dumps(campaign_data, default=str))
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
    
    # Validate agent
    agent = db.get_agent(campaign_data.agent_id, current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", campaign_data.agent_id)
    if agent.get("status") != "active":
        raise ValidationError("Agent must be active")
    
    # Create campaign record
    campaign_id = str(uuid.uuid4())
    campaign_record = {
        "id": campaign_id,
        "client_id": current_user["client_id"],
        "agent_id": campaign_data.agent_id,
        "name": campaign_data.name,
        "schedule_type": campaign_data.schedule_type.value,
        "scheduled_at": campaign_data.scheduled_at.isoformat() if campaign_data.scheduled_at else None,
        "timezone": campaign_data.timezone,
        "max_concurrent_calls": campaign_data.max_concurrent_calls,
        "status": "draft",
        "stats": {"pending": 0, "calling": 0, "completed": 0, "failed": 0},
    }
    
    db.insert("campaigns", campaign_record)
    
    # Emit event
    await emit_campaign_created(
        campaign_id=campaign_id,
        client_id=current_user["client_id"],
        agent_id=campaign_data.agent_id,
        name=campaign_data.name,
    )
    
    response_data = {
        "data": CampaignResponse(**campaign_record),
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


@router.post("/{campaign_id}/contacts/presign")
async def presign_contacts_csv(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get presigned URL for contacts CSV upload"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    if campaign.get("status") != "draft":
        raise ValidationError("Campaign must be in draft status")
    
    storage_key = f"uploads/client_{current_user['client_id']}/campaigns/{campaign_id}/contacts.csv"
    url = generate_presigned_url(
        bucket=settings.STORAGE_BUCKET_UPLOADS,
        key=storage_key,
        operation="put_object",
        expires_in=3600,
        content_type="text/csv",
    )
    
    return {
        "data": {
            "upload_url": url,
            "storage_key": storage_key,
            "headers": {"Content-Type": "text/csv"},
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{campaign_id}/contacts")
async def upload_campaign_contacts(
    campaign_id: str,
    contacts_data: CampaignContactsUpload,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Upload campaign contacts (CSV or direct array)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    if campaign.get("status") != "draft":
        raise ValidationError("Campaign must be in draft status")
    
    contacts = []
    
    if contacts_data.storage_key:
        # Parse CSV from local storage
        from app.core.storage import get_file_path
        try:
            file_path = get_file_path("uploads", contacts_data.storage_key)
            with open(file_path, 'r', encoding='utf-8') as f:
                csv_content = f.read()
            reader = csv.DictReader(io.StringIO(csv_content))
            
            for row in reader:
                phone_number = row.get("phone_number", "").strip()
                if not phone_number:
                    continue
                
                contacts.append({
                    "phone_number": phone_number,
                    "first_name": row.get("first_name", "").strip() or None,
                    "last_name": row.get("last_name", "").strip() or None,
                    "email": row.get("email", "").strip() or None,
                    "custom_fields": {k: v for k, v in row.items() if k not in ["phone_number", "first_name", "last_name", "email"]},
                })
        except Exception as e:
            raise ValidationError(f"Failed to parse CSV: {str(e)}")
    elif contacts_data.contacts:
        contacts = [c.dict() for c in contacts_data.contacts]
    
    # Insert contacts
    contacts_added = 0
    for contact in contacts:
        try:
            db.insert(
                "campaign_contacts",
                {
                    "campaign_id": campaign_id,
                    "phone_number": contact["phone_number"],
                    "first_name": contact.get("first_name"),
                    "last_name": contact.get("last_name"),
                    "email": contact.get("email"),
                    "custom_fields": contact.get("custom_fields", {}),
                    "status": "pending",
                },
            )
            contacts_added += 1
        except Exception:
            # Skip duplicates
            continue
    
    # Update campaign stats
    db.update_campaign_stats(campaign_id)
    
    return {
        "data": {
            "campaign_id": campaign_id,
            "contacts_added": contacts_added,
            "contacts_failed": len(contacts) - contacts_added,
            "stats": db.get_campaign(campaign_id, current_user["client_id"]).get("stats", {}),
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{campaign_id}/schedule")
async def schedule_campaign(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Schedule campaign with atomic pre-flight checks.
    Validates agent and credits before calling Ultravox.
    Rolls back to draft status if Ultravox returns an error.
    """
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    if campaign.get("status") != "draft":
        raise ValidationError("Campaign must be in draft status")
    
    # Get contacts
    contacts = db.get_campaign_contacts(campaign_id)
    pending_contacts = [c for c in contacts if c.get("status") == "pending"]
    
    if not pending_contacts:
        raise ValidationError("No pending contacts found")
    
    # PRE-FLIGHT CHECK 1: Verify agent has valid ultravox_agent_id
    agent = db.get_agent(campaign["agent_id"], current_user["client_id"])
    if not agent:
        raise NotFoundError("agent", campaign["agent_id"])
    
    if agent.get("status") != "active":
        raise ValidationError("Agent must be active", {"agent_status": agent.get("status")})
    
    ultravox_agent_id = agent.get("ultravox_agent_id")
    if not ultravox_agent_id:
        raise ValidationError(
            "Agent must be synced with Ultravox to schedule campaigns. Use /agents/{agent_id}/sync first.",
            {"agent_id": campaign["agent_id"]}
        )
    
    # PRE-FLIGHT CHECK 2: Verify client has enough credits
    # Estimate: 1 credit per contact
    required_credits = len(pending_contacts)
    client = db.get_client(current_user["client_id"])
    if not client:
        raise NotFoundError("client", current_user["client_id"])
    
    available_credits = client.get("credits_balance", 0)
    if available_credits < required_credits:
        from app.core.exceptions import PaymentRequiredError
        raise PaymentRequiredError(
            f"Insufficient credits for campaign. Required: {required_credits}, Available: {available_credits}",
            {
                "required": required_credits,
                "available": available_credits,
                "contacts_count": len(pending_contacts),
            }
        )
    
    # Check if Ultravox is configured
    if not settings.ULTRAVOX_API_KEY:
        raise ValidationError("Ultravox API key is not configured")
    
    # ATOMIC OPERATION: Update status to 'scheduling' (temporary)
    db.update(
        "campaigns",
        {"id": campaign_id},
        {"status": "scheduling"},  # Temporary status
    )
    
    try:
        # Prepare batch data
        batch_contacts = []
        for contact in pending_contacts:
            batch_contacts.append({
                "phone_number": contact["phone_number"],
                "context": {
                    "first_name": contact.get("first_name"),
                    "last_name": contact.get("last_name"),
                    "campaign_id": campaign_id,
                    "custom_fields": contact.get("custom_fields", {}),
                },
            })
        
        batch_data = {
            "batches": [{
                "contacts": batch_contacts,
                "medium": {"telnyx": {}},
                "schedule": {
                    "at": campaign.get("scheduled_at"),
                    "timezone": campaign.get("timezone", "UTC"),
                },
                "settings": {
                    "max_concurrent": campaign.get("max_concurrent_calls", 10),
                    "recording_enabled": True,
                },
            }],
        }
        
        # Call Ultravox API
        ultravox_response = await ultravox_client.create_scheduled_batch(
            ultravox_agent_id,
            batch_data,
        )
        
        batch_ids = [b.get("batch_id") for b in ultravox_response.get("batches", [])]
        
        if not batch_ids:
            raise ValidationError("Ultravox did not return batch IDs", {"response": ultravox_response})
        
        # SUCCESS: Update campaign to scheduled with batch IDs
        db.update(
            "campaigns",
            {"id": campaign_id},
            {
                "status": "scheduled",
                "ultravox_batch_ids": batch_ids,
                "updated_at": datetime.utcnow().isoformat(),
            },
        )
        
        # Emit event
        await emit_campaign_scheduled(
            campaign_id=campaign_id,
            client_id=current_user["client_id"],
            scheduled_at=campaign.get("scheduled_at"),
            contact_count=len(pending_contacts),
            batch_ids=batch_ids,
        )
        
    except Exception as e:
        # ROLLBACK: Revert to draft status and return specific error
        logger.error(f"Failed to schedule campaign {campaign_id}: {e}", exc_info=True)
        
        db.update(
            "campaigns",
            {"id": campaign_id},
            {
                "status": "draft",  # Rollback to draft
                "updated_at": datetime.utcnow().isoformat(),
            },
        )
        
        # Extract error details if it's a ProviderError
        if isinstance(e, ProviderError):
            provider_error_details = e.details.get("provider_details", {})
            error_message = str(e)
            
            # Check for common Ultravox errors
            if "Invalid Telephony Config" in error_message or "telephony" in error_message.lower():
                error_message = "Invalid telephony configuration. Please check your SIP/telephony settings in Ultravox."
            elif "401" in error_message or "403" in error_message:
                error_message = "Ultravox API authentication failed. Please check your API key."
            
            raise ValidationError(
                f"Failed to schedule campaign: {error_message}",
                {
                    "error": error_message,
                    "provider_details": provider_error_details,
                    "campaign_id": campaign_id,
                }
            )
        else:
            # Generic error
            raise ValidationError(
                f"Failed to schedule campaign: {str(e)}",
                {"error": str(e), "campaign_id": campaign_id}
            )
    
    updated_campaign = db.get_campaign(campaign_id, current_user["client_id"])
    
    return {
        "data": CampaignResponse(**updated_campaign),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("")
async def list_campaigns(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    List campaigns with filtering and pagination.
    Campaigns in scheduled/active status are reconciled with Ultravox for live stats.
    """
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Build filters
    filters = {"client_id": current_user["client_id"]}
    if agent_id:
        filters["agent_id"] = agent_id
    if status:
        filters["status"] = status
    
    # Get campaigns with pagination
    all_campaigns = db.select("campaigns", filters, order_by="created_at")
    
    # Apply pagination manually
    total = len(all_campaigns)
    paginated_campaigns = all_campaigns[offset:offset + limit]
    
    # Background reconciliation for active/scheduled campaigns
    from app.core.config import settings
    if settings.ULTRAVOX_API_KEY:
        for campaign in paginated_campaigns:
            campaign_status = campaign.get("status", "").lower()
            if campaign_status in ["scheduled", "active"] and campaign.get("ultravox_batch_ids"):
                try:
                    batch_ids = campaign.get("ultravox_batch_ids", [])
                    ultravox_stats = {
                        "pending": 0,
                        "calling": 0,
                        "completed": 0,
                        "failed": 0,
                    }
                    all_completed = True
                    
                    # Get agent_id from campaign for batch lookup
                    agent_id = campaign.get("agent_id")
                    agent = db.get_agent(agent_id, current_user["client_id"]) if agent_id else None
                    ultravox_agent_id = agent.get("ultravox_agent_id") if agent else None
                    
                    if ultravox_agent_id:
                        for batch_id in batch_ids:
                            try:
                                batch_data = await ultravox_client.get_batch(ultravox_agent_id, batch_id)
                                
                                # Map Ultravox batch stats
                                total_count = batch_data.get("totalCount", 0)
                                completed_count = batch_data.get("completedCount", 0)
                                ultravox_stats["completed"] += completed_count
                                ultravox_stats["pending"] += batch_data.get("pendingCount", 0) or max(0, total_count - completed_count)
                                # Failed is typically total - completed (if failedCount not explicitly provided)
                                ultravox_stats["failed"] += max(0, total_count - completed_count - ultravox_stats["pending"])
                                
                                # Check if batch is completed
                                if completed_count >= total_count and total_count > 0:
                                    pass  # Batch completed
                                else:
                                    all_completed = False
                            except Exception as e:
                                logger.warning(f"Failed to fetch batch {batch_id} for campaign {campaign['id']}: {e}")
                                all_completed = False
                    else:
                        logger.warning(f"Cannot reconcile campaign {campaign['id']}: agent {agent_id} has no ultravox_agent_id")
                    
                    # Update stats
                    db.update(
                        "campaigns",
                        {"id": campaign["id"]},
                        {"stats": ultravox_stats},
                    )
                    
                    # Update status if all batches completed
                    if all_completed and campaign_status != "completed":
                        db.update(
                            "campaigns",
                            {"id": campaign["id"]},
                            {
                                "status": "completed",
                                "updated_at": datetime.utcnow().isoformat(),
                            },
                        )
                except Exception as e:
                    logger.warning(f"Failed to reconcile campaign {campaign['id']}: {e}")
            else:
                # For non-active campaigns, just update local stats
                db.update_campaign_stats(campaign["id"])
    
    # Refresh campaigns after stats update
    paginated_campaigns = [db.get_campaign(c["id"], current_user["client_id"]) for c in paginated_campaigns]
    
    return {
        "data": [CampaignResponse(**campaign) for campaign in paginated_campaigns],
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


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """
    Get campaign with live reconciliation from Ultravox.
    When campaign is scheduled or active, fetches real-time stats from Ultravox batches.
    """
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    # Background reconciliation: If campaign is scheduled or active, sync with Ultravox
    campaign_status = campaign.get("status", "").lower()
    if campaign_status in ["scheduled", "active"] and campaign.get("ultravox_batch_ids"):
        try:
            from app.core.config import settings
            if settings.ULTRAVOX_API_KEY:
                batch_ids = campaign.get("ultravox_batch_ids", [])
                if batch_ids:
                    # Fetch latest batch stats from Ultravox
                    ultravox_stats = {
                        "pending": 0,
                        "calling": 0,
                        "completed": 0,
                        "failed": 0,
                    }
                    all_completed = True
                    
                    # Get agent_id from campaign for batch lookup
                    agent_id = campaign.get("agent_id")
                    agent = db.get_agent(agent_id, current_user["client_id"]) if agent_id else None
                    ultravox_agent_id = agent.get("ultravox_agent_id") if agent else None
                    
                    if not ultravox_agent_id:
                        logger.warning(f"Cannot reconcile campaign {campaign_id}: agent {agent_id} has no ultravox_agent_id")
                    else:
                        for batch_id in batch_ids:
                            try:
                                batch_data = await ultravox_client.get_batch(ultravox_agent_id, batch_id)
                                
                                # Map Ultravox batch stats to our stats format
                                # Ultravox provides: completedCount, totalCount, etc.
                                ultravox_stats["completed"] += batch_data.get("completedCount", 0)
                                # Calculate failed as total - completed (if failedCount not available)
                                total_count = batch_data.get("totalCount", 0)
                                completed_count = batch_data.get("completedCount", 0)
                                ultravox_stats["failed"] += max(0, total_count - completed_count - batch_data.get("pendingCount", 0))
                                ultravox_stats["pending"] += batch_data.get("pendingCount", 0) or (total_count - completed_count)
                                
                                # Check if batch is completed (all calls finished)
                                if completed_count >= total_count and total_count > 0:
                                    # All calls in batch are completed
                                    pass
                                else:
                                    all_completed = False
                            except Exception as e:
                                logger.warning(f"Failed to fetch batch {batch_id} from Ultravox: {e}")
                                all_completed = False
                    
                    # Update campaign stats with live Ultravox data
                    db.update(
                        "campaigns",
                        {"id": campaign_id},
                        {"stats": ultravox_stats},
                    )
                    
                    # If all batches are completed, update campaign status
                    if all_completed and campaign_status != "completed":
                        db.update(
                            "campaigns",
                            {"id": campaign_id},
                            {
                                "status": "completed",
                                "updated_at": datetime.utcnow().isoformat(),
                            },
                        )
                        campaign["status"] = "completed"
                    
                    campaign["stats"] = ultravox_stats
        except Exception as e:
            # Log error but don't fail the request - return cached stats
            logger.warning(f"Failed to reconcile campaign {campaign_id} with Ultravox: {e}")
    else:
        # For non-active campaigns, just update local stats
        db.update_campaign_stats(campaign_id)
        campaign = db.get_campaign(campaign_id, current_user["client_id"])
    
    return {
        "data": CampaignResponse(**campaign),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    campaign_data: CampaignUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Update campaign"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if campaign exists
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    # Only allow updates for draft campaigns
    if campaign.get("status") != "draft":
        raise ValidationError("Campaign can only be updated when in draft status")
    
    # Prepare update data (only non-None fields)
    update_data = campaign_data.dict(exclude_unset=True)
    if not update_data:
        # No updates provided
        return {
            "data": CampaignResponse(**campaign),
            "meta": ResponseMeta(
                request_id=str(uuid.uuid4()),
                ts=datetime.utcnow(),
            ),
        }
    
    # Validate agent if agent_id is being updated
    if "agent_id" in update_data:
        agent = db.get_agent(update_data["agent_id"], current_user["client_id"])
        if not agent:
            raise NotFoundError("agent", update_data["agent_id"])
        if agent.get("status") != "active":
            raise ValidationError("Agent must be active")
    
    # Convert enum to string if needed
    if "schedule_type" in update_data and hasattr(update_data["schedule_type"], "value"):
        update_data["schedule_type"] = update_data["schedule_type"].value
    
    # Convert datetime to ISO string if needed
    if "scheduled_at" in update_data and update_data["scheduled_at"]:
        if hasattr(update_data["scheduled_at"], "isoformat"):
            update_data["scheduled_at"] = update_data["scheduled_at"].isoformat()
    
    # Update database
    update_data["updated_at"] = datetime.utcnow().isoformat()
    db.update("campaigns", {"id": campaign_id}, update_data)
    
    # Get updated campaign
    updated_campaign = db.get_campaign(campaign_id, current_user["client_id"])
    
    return {
        "data": CampaignResponse(**updated_campaign),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Pause a running campaign"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if campaign exists
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    # Only allow pausing for running or scheduled campaigns
    current_status = campaign.get("status")
    if current_status not in ["running", "scheduled"]:
        raise ValidationError(
            f"Campaign can only be paused when in 'running' or 'scheduled' status. Current status: {current_status}"
        )
    
    # Update campaign status to paused
    db.update(
        "campaigns",
        {"id": campaign_id},
        {
            "status": "paused",
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    
    # Note: If Ultravox has pause batch endpoint, call it here
    # For now, we just update the database status
    # The actual pausing of calls will be handled by the campaign execution logic
    
    # Get updated campaign
    updated_campaign = db.get_campaign(campaign_id, current_user["client_id"])
    
    return {
        "data": CampaignResponse(**updated_campaign),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Resume a paused campaign"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if campaign exists
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    # Only allow resuming for paused campaigns
    current_status = campaign.get("status")
    if current_status != "paused":
        raise ValidationError(
            f"Campaign can only be resumed when in 'paused' status. Current status: {current_status}"
        )
    
    # Determine the status to resume to
    # If campaign was scheduled before, resume to scheduled
    # Otherwise resume to running
    # Check if campaign has scheduled_at and it's in the future
    scheduled_at = campaign.get("scheduled_at")
    resume_status = "running"
    
    if scheduled_at:
        try:
            # Parse ISO format datetime string
            if isinstance(scheduled_at, str):
                scheduled_datetime = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
            else:
                scheduled_datetime = scheduled_at
            
            # Compare with UTC now
            if scheduled_datetime.replace(tzinfo=None) > datetime.utcnow():
                resume_status = "scheduled"
        except Exception:
            # If parsing fails, default to running
            resume_status = "running"
    
    # Update campaign status
    db.update(
        "campaigns",
        {"id": campaign_id},
        {
            "status": resume_status,
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    
    # Note: If Ultravox has resume batch endpoint, call it here
    # For now, we just update the database status
    # The actual resuming of calls will be handled by the campaign execution logic
    
    # Get updated campaign
    updated_campaign = db.get_campaign(campaign_id, current_user["client_id"])
    
    return {
        "data": CampaignResponse(**updated_campaign),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/bulk")
async def bulk_delete_campaigns(
    request_data: BulkDeleteRequest,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Bulk delete campaigns"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    deleted_ids = []
    failed_ids = []
    
    for campaign_id in request_data.ids:
        try:
            campaign = db.get_campaign(campaign_id, current_user["client_id"])
            if not campaign:
                failed_ids.append(campaign_id)
                continue
            
            # Only allow deletion for draft or failed campaigns
            if campaign.get("status") not in ["draft", "failed"]:
                failed_ids.append(campaign_id)
                continue
            
            # Delete campaign contacts first (if cascade delete is not enabled)
            try:
                contacts = db.get_campaign_contacts(campaign_id)
                for contact in contacts:
                    db.delete("campaign_contacts", {"id": contact["id"]})
            except Exception:
                pass  # Continue even if contacts deletion fails
            
            # Delete campaign
            db.delete("campaigns", {"id": campaign_id})
            deleted_ids.append(campaign_id)
        except Exception as e:
            logger.error(f"Failed to delete campaign {campaign_id}: {e}")
            failed_ids.append(campaign_id)
    
    return {
        "data": BulkDeleteResponse(
            deleted_count=len(deleted_ids),
            failed_count=len(failed_ids),
            deleted_ids=deleted_ids,
            failed_ids=failed_ids,
        ),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete campaign"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check if campaign exists
    campaign = db.get_campaign(campaign_id, current_user["client_id"])
    if not campaign:
        raise NotFoundError("campaign", campaign_id)
    
    # Only allow deletion for draft or failed campaigns
    if campaign.get("status") not in ["draft", "failed"]:
        raise ValidationError("Campaign can only be deleted when in draft or failed status")
    
    # Delete campaign contacts first (if cascade delete is not enabled)
    try:
        contacts = db.get_campaign_contacts(campaign_id)
        for contact in contacts:
            db.delete("campaign_contacts", {"id": contact["id"]})
    except Exception:
        pass  # Continue even if contacts deletion fails
    
    # Delete campaign
    db.delete("campaigns", {"id": campaign_id})
    
    return {
        "data": {"id": campaign_id, "deleted": True},
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

