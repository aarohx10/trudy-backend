"""
Outbound Webhook Tasks
Handles sending call data to external CRM systems (HighLevel, Hubspot, Zapier, etc.)
"""
import logging
import httpx
import json
import time
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.database import DatabaseAdminService
from app.core.webhooks import generate_webhook_signature

logger = logging.getLogger(__name__)


# Note: trigger_crm_webhook function removed - was dependent on agents table
# CRM webhook functionality can be re-implemented using call/campaign settings if needed
