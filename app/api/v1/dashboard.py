"""
Dashboard Endpoints
"""
from fastapi import APIRouter, Header, Depends, Query
from typing import Optional
from datetime import datetime, timedelta
import uuid

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.exceptions import ValidationError
from app.models.schemas import ResponseMeta

router = APIRouter()


@router.get("/stats")
async def get_dashboard_stats(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
    date_from: Optional[str] = Query(None, description="Start date (ISO format: YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (ISO format: YYYY-MM-DD)"),
):
    """Get dashboard statistics"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    client_id = current_user["client_id"]
    
    # Parse date filters
    date_from_dt = None
    date_to_dt = None
    
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
        except ValueError:
            raise ValidationError(f"Invalid date_from format. Expected ISO format (YYYY-MM-DD), got: {date_from}")
    
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            # Add one day to include the entire end date
            date_to_dt = date_to_dt + timedelta(days=1)
        except ValueError:
            raise ValidationError(f"Invalid date_to format. Expected ISO format (YYYY-MM-DD), got: {date_to}")
    
    # If no date range provided, default to last 30 days
    if not date_from_dt and not date_to_dt:
        date_to_dt = datetime.utcnow()
        date_from_dt = date_to_dt - timedelta(days=30)
    
    # Ensure date_from is before date_to
    if date_from_dt and date_to_dt and date_from_dt > date_to_dt:
        raise ValidationError("date_from must be before date_to")
    
    # Build filters for calls
    call_filters = {"client_id": client_id}
    
    # Get all calls for the client (we'll filter by date in Python)
    all_calls = db.select("calls", call_filters, order_by="created_at")
    
    # Filter calls by date range
    filtered_calls = []
    for call in all_calls:
        call_created_at = call.get("created_at")
        if not call_created_at:
            continue
        
        # Parse call created_at
        try:
            if isinstance(call_created_at, str):
                call_dt = datetime.fromisoformat(call_created_at.replace('Z', '+00:00'))
            else:
                call_dt = call_created_at
            
            # Remove timezone for comparison
            call_dt = call_dt.replace(tzinfo=None) if hasattr(call_dt, 'tzinfo') and call_dt.tzinfo else call_dt
            
            # Check date range
            if date_from_dt and call_dt < date_from_dt.replace(tzinfo=None) if hasattr(date_from_dt, 'tzinfo') and date_from_dt.tzinfo else date_from_dt:
                continue
            if date_to_dt and call_dt >= date_to_dt.replace(tzinfo=None) if hasattr(date_to_dt, 'tzinfo') and date_to_dt.tzinfo else date_to_dt:
                continue
            
            filtered_calls.append(call)
        except Exception:
            # Skip calls with invalid dates
            continue
    
    # Calculate call statistics
    total_calls = len(filtered_calls)
    active_calls = sum(1 for c in filtered_calls if c.get("status") in ["queued", "ringing", "in_progress"])
    completed_calls = sum(1 for c in filtered_calls if c.get("status") == "completed")
    failed_calls = sum(1 for c in filtered_calls if c.get("status") == "failed")
    
    # Calculate success rate (only for completed calls with analysis)
    completed_with_analysis = [c for c in filtered_calls if c.get("status") == "completed" and c.get("analysis_status") == "completed"]
    successful_calls = sum(1 for c in completed_with_analysis if c.get("is_success") is True)
    success_rate = (successful_calls / len(completed_with_analysis) * 100) if completed_with_analysis else 0
    
    # Calculate sentiment distribution
    sentiment_counts = {
        "positive": sum(1 for c in completed_with_analysis if c.get("sentiment") == "positive"),
        "neutral": sum(1 for c in completed_with_analysis if c.get("sentiment") == "neutral"),
        "negative": sum(1 for c in completed_with_analysis if c.get("sentiment") == "negative"),
    }
    
    # Calculate duration statistics
    durations = [c.get("duration_seconds") for c in filtered_calls if c.get("duration_seconds") is not None]
    average_duration_seconds = sum(durations) / len(durations) if durations else 0
    
    # Calculate cost statistics
    costs = [float(c.get("cost_usd", 0)) for c in filtered_calls if c.get("cost_usd") is not None]
    total_cost_usd = sum(costs)
    average_cost_usd = total_cost_usd / len(costs) if costs else 0
    
    # Get campaign statistics
    campaign_filters = {"client_id": client_id}
    
    all_campaigns = db.select("campaigns", campaign_filters)
    
    # Filter campaigns by date range
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
            
            if date_from_dt and campaign_dt < date_from_dt.replace(tzinfo=None) if hasattr(date_from_dt, 'tzinfo') and date_from_dt.tzinfo else date_from_dt:
                continue
            if date_to_dt and campaign_dt >= date_to_dt.replace(tzinfo=None) if hasattr(date_to_dt, 'tzinfo') and date_to_dt.tzinfo else date_to_dt:
                continue
            
            filtered_campaigns.append(campaign)
        except Exception:
            continue
    
    total_campaigns = len(filtered_campaigns)
    active_campaigns = sum(1 for c in filtered_campaigns if c.get("status") in ["running", "scheduled"])
    completed_campaigns = sum(1 for c in filtered_campaigns if c.get("status") == "completed")
    
    # Get client credit balance
    client = db.get_client(client_id)
    credits_balance = client.get("credits_balance", 0) if client else 0
    
    # Format date range for response
    date_range = {
        "from": date_from_dt.isoformat() if date_from_dt else None,
        "to": (date_to_dt - timedelta(days=1)).isoformat() if date_to_dt else None,  # Subtract 1 day since we added it earlier
    }
    
    stats = {
        "calls": {
            "total_all_time": len(db.select("calls", {"client_id": client_id})),  # Total across all time
            "total_date_range": total_calls,
            "active": active_calls,
            "completed": completed_calls,
            "failed": failed_calls,
            "average_duration_seconds": int(average_duration_seconds),
            "success_rate": round(success_rate, 2),  # Success rate percentage
            "successful_calls": successful_calls,
            "sentiment": sentiment_counts,
        },
        "campaigns": {
            "total": total_campaigns,
            "active": active_campaigns,
            "completed": completed_campaigns,
        },
        "cost": {
            "total_usd": round(total_cost_usd, 2),
            "average_per_call_usd": round(average_cost_usd, 2),
            "currency": "USD",
        },
        "credits": {
            "balance": credits_balance,
        },
        "date_range": date_range,
    }
    
    return {
        "data": stats,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

