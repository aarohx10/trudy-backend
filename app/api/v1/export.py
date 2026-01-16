"""
Export Endpoints (CSV)
"""
from fastapi import APIRouter, Header, Depends, Query
from fastapi.responses import Response
from typing import Optional
from datetime import datetime
import uuid
import csv
import io

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ForbiddenError
from app.models.schemas import ResponseMeta

router = APIRouter()


@router.get("/calls")
async def export_calls(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    agent_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """Export calls to CSV"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Build filters
    filters = {"client_id": current_user["client_id"]}
    if agent_id:
        filters["agent_id"] = agent_id
    if status:
        filters["status"] = status
    
    # Get all calls
    all_calls = db.select("calls", filters, order_by="created_at")
    
    # Filter by date range if provided
    if date_from or date_to:
        from datetime import timedelta
        filtered_calls = []
        for call in all_calls:
            call_created_at = call.get("created_at")
            if not call_created_at:
                continue
            
            try:
                if isinstance(call_created_at, str):
                    call_dt = datetime.fromisoformat(call_created_at.replace('Z', '+00:00'))
                else:
                    call_dt = call_created_at
                
                call_dt = call_dt.replace(tzinfo=None) if hasattr(call_dt, 'tzinfo') and call_dt.tzinfo else call_dt
                
                if date_from:
                    date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                    date_from_dt = date_from_dt.replace(tzinfo=None) if hasattr(date_from_dt, 'tzinfo') and date_from_dt.tzinfo else date_from_dt
                    if call_dt < date_from_dt:
                        continue
                
                if date_to:
                    date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                    date_to_dt = (date_to_dt + timedelta(days=1)).replace(tzinfo=None) if hasattr(date_to_dt, 'tzinfo') and date_to_dt.tzinfo else date_to_dt + timedelta(days=1)
                    if call_dt >= date_to_dt:
                        continue
                
                filtered_calls.append(call)
            except Exception:
                continue
        all_calls = filtered_calls
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "ID",
        "Agent ID",
        "Phone Number",
        "Direction",
        "Status",
        "Started At",
        "Ended At",
        "Duration (seconds)",
        "Cost (USD)",
        "Created At",
    ])
    
    # Write data
    for call in all_calls:
        writer.writerow([
            call.get("id", ""),
            call.get("agent_id", ""),
            call.get("phone_number", ""),
            call.get("direction", ""),
            call.get("status", ""),
            call.get("started_at", ""),
            call.get("ended_at", ""),
            call.get("duration_seconds", ""),
            call.get("cost_usd", ""),
            call.get("created_at", ""),
        ])
    
    csv_content = output.getvalue()
    output.close()
    
    # Generate filename with timestamp
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"calls_export_{timestamp}.csv"
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


@router.get("/campaigns")
async def export_campaigns(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    agent_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """Export campaigns to CSV"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Build filters
    filters = {"client_id": current_user["client_id"]}
    if agent_id:
        filters["agent_id"] = agent_id
    if status:
        filters["status"] = status
    
    # Get all campaigns
    all_campaigns = db.select("campaigns", filters, order_by="created_at")
    
    # Filter by date range if provided
    if date_from or date_to:
        from datetime import timedelta
        filtered_campaigns = []
        for campaign in all_campaigns:
            campaign_created_at = campaign.get("created_at")
            if not campaign_created_at:
                continue
            
            try:
                if isinstance(campaign_created_at, str):
                    campaign_dt = datetime.fromisoformat(campaign_created_at.replace('Z', '+00:00'))
                else:
                    campaign_dt = campaign_created_at
                
                campaign_dt = campaign_dt.replace(tzinfo=None) if hasattr(campaign_dt, 'tzinfo') and campaign_dt.tzinfo else campaign_dt
                
                if date_from:
                    date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                    date_from_dt = date_from_dt.replace(tzinfo=None) if hasattr(date_from_dt, 'tzinfo') and date_from_dt.tzinfo else date_from_dt
                    if campaign_dt < date_from_dt:
                        continue
                
                if date_to:
                    date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                    date_to_dt = (date_to_dt + timedelta(days=1)).replace(tzinfo=None) if hasattr(date_to_dt, 'tzinfo') and date_to_dt.tzinfo else date_to_dt + timedelta(days=1)
                    if campaign_dt >= date_to_dt:
                        continue
                
                filtered_campaigns.append(campaign)
            except Exception:
                continue
        all_campaigns = filtered_campaigns
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "ID",
        "Name",
        "Agent ID",
        "Schedule Type",
        "Scheduled At",
        "Timezone",
        "Max Concurrent Calls",
        "Status",
        "Stats (Pending)",
        "Stats (Calling)",
        "Stats (Completed)",
        "Stats (Failed)",
        "Created At",
        "Updated At",
    ])
    
    # Write data
    for campaign in all_campaigns:
        stats = campaign.get("stats", {})
        writer.writerow([
            campaign.get("id", ""),
            campaign.get("name", ""),
            campaign.get("agent_id", ""),
            campaign.get("schedule_type", ""),
            campaign.get("scheduled_at", ""),
            campaign.get("timezone", ""),
            campaign.get("max_concurrent_calls", ""),
            campaign.get("status", ""),
            stats.get("pending", 0),
            stats.get("calling", 0),
            stats.get("completed", 0),
            stats.get("failed", 0),
            campaign.get("created_at", ""),
            campaign.get("updated_at", ""),
        ])
    
    csv_content = output.getvalue()
    output.close()
    
    # Generate filename with timestamp
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"campaigns_export_{timestamp}.csv"
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


@router.get("/agents")
async def export_agents(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    status: Optional[str] = Query(None),
):
    """Export agents to CSV"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Build filters
    filters = {"client_id": current_user["client_id"]}
    if status:
        filters["status"] = status
    
    # Get all agents
    all_agents = db.select("agents", filters, order_by="created_at")
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "ID",
        "Name",
        "Description",
        "Voice ID",
        "Model",
        "Status",
        "Ultravox Agent ID",
        "Created At",
        "Updated At",
    ])
    
    # Write data
    for agent in all_agents:
        writer.writerow([
            agent.get("id", ""),
            agent.get("name", ""),
            agent.get("description", ""),
            agent.get("voice_id", ""),
            agent.get("model", ""),
            agent.get("status", ""),
            agent.get("ultravox_agent_id", ""),
            agent.get("created_at", ""),
            agent.get("updated_at", ""),
        ])
    
    csv_content = output.getvalue()
    output.close()
    
    # Generate filename with timestamp
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"agents_export_{timestamp}.csv"
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8",
        },
    )

