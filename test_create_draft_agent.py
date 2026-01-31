"""
Test script: Create a simple blank draft agent (no voice, no tools, no knowledge base).
Saves to Supabase with clerk_org_id to verify persistence. Does NOT call Ultravox.
Run from z-backend: python test_create_draft_agent.py
"""
import json
import logging
import sys
import uuid
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -----------------------------------------------------------------------------
# HARDCODED for this test
# -----------------------------------------------------------------------------
SUPABASE_URL = "https://vixvkphbowjoujtpvoxe.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZpeHZrcGhib3dqb3VqdHB2b3hlIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MjMzNzc5OSwiZXhwIjoyMDc3OTEzNzk5fQ.9-Pl-I2Q3pk5lNpE6j2N1Lkn7-PL4TT9dTnQ0kW7IwY"
CLERK_ORG_ID = "org_38yDFGK71ZjInk1CJMRavhOoJKR"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def main():
    logger.info("=== Test: Create blank agent (no voice, no tools, no KB) and save to Supabase ===")
    logger.info("CLERK_ORG_ID=%s", CLERK_ORG_ID)

    try:
        import httpx
        from supabase import create_client
    except ImportError as e:
        logger.error("Missing dependency: %s. pip install httpx supabase", e)
        sys.exit(1)

    # -------------------------------------------------------------------------
    # 1. Build minimal blank agent record: no voice_id, no tools, no knowledge_bases
    #    Do NOT call Ultravox — ultravox_agent_id stays None
    # -------------------------------------------------------------------------
    now = datetime.utcnow()
    agent_id = str(uuid.uuid4())
    agent_name = "Untitled Agent"
    ultravox_agent_id = None  # Blank agent: no Ultravox create

    agent_record = {
        "id": agent_id,
        "clerk_org_id": CLERK_ORG_ID,
        "ultravox_agent_id": ultravox_agent_id,
        "name": agent_name,
        "description": "Blank draft agent",
        "voice_id": None,
        "system_prompt": "You are a helpful assistant.",
        "model": "ultravox-v0.6",
        "tools": [],
        "knowledge_bases": [],
        "status": "draft",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "temperature": 0.3,
        "language_hint": "en-US",
        "initial_output_medium": "MESSAGE_MEDIUM_VOICE",
        "recording_enabled": False,
        "join_timeout": "30s",
        "max_duration": "3600s",
    }

    logger.info("Saving to Supabase: id=%s clerk_org_id=%s ultravox_agent_id=%s (blank)", agent_id, CLERK_ORG_ID, ultravox_agent_id)
    body = json.loads(json.dumps(agent_record, default=str))
    logger.info("Request body keys sent: %s", list(body.keys()))
    logger.info("Body clerk_org_id=%s ultravox_agent_id=%s", body.get("clerk_org_id"), body.get("ultravox_agent_id"))

    # -------------------------------------------------------------------------
    # 4. Save to Supabase via REST: POST /rest/v1/agents
    # -------------------------------------------------------------------------
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/agents"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    with httpx.Client(timeout=30.0) as http:
        resp = http.post(url, json=body, headers=headers)
    logger.info("Supabase REST insert status=%s", resp.status_code)
    if resp.status_code >= 400:
        logger.error("Supabase insert failed: %s %s", resp.status_code, resp.text[:500])
        sys.exit(1)
    data = resp.json()
    inserted = data[0] if isinstance(data, list) else data
    logger.info("Insert response: id=%s clerk_org_id=%s ultravox_agent_id=%s", inserted.get("id"), inserted.get("clerk_org_id"), inserted.get("ultravox_agent_id"))

    # -------------------------------------------------------------------------
    # 5. Verify row in DB
    # -------------------------------------------------------------------------
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    fetch = client.table("agents").select("id, clerk_org_id, ultravox_agent_id, name, status").eq("id", agent_id).execute()
    if not fetch.data or len(fetch.data) == 0:
        logger.error("Verify: No row found for id=%s", agent_id)
        logger.info("=== Test script finished (FAIL) ===")
        sys.exit(1)

    row = fetch.data[0]
    logger.info("Verify: id=%s clerk_org_id=%s ultravox_agent_id=%s name=%s status=%s",
        row.get("id"), row.get("clerk_org_id"), row.get("ultravox_agent_id"), row.get("name"), row.get("status"))

    # -------------------------------------------------------------------------
    # 6. Fallback: if DB dropped clerk_org_id or ultravox_agent_id, fix with PATCH
    # -------------------------------------------------------------------------
    if row.get("clerk_org_id") == CLERK_ORG_ID and row.get("ultravox_agent_id") == ultravox_agent_id:
        logger.info("SUCCESS: clerk_org_id (and ultravox_agent_id) stored correctly on first insert.")
    else:
        logger.warning("MISMATCH: DB has null/wrong clerk_org_id (or ultravox_agent_id). Applying fallback PATCH.")
        patch_url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/agents?id=eq.{agent_id}"
        patch_body = {"clerk_org_id": CLERK_ORG_ID, "ultravox_agent_id": ultravox_agent_id}  # ultravox_agent_id is None for blank agent
        with httpx.Client(timeout=30.0) as http:
            patch_resp = http.patch(patch_url, json=patch_body, headers=headers)
        logger.info("Fallback PATCH status=%s", patch_resp.status_code)
        if patch_resp.status_code >= 400:
            logger.error("Fallback PATCH failed: %s %s", patch_resp.status_code, patch_resp.text[:300])
        else:
            fetch2 = client.table("agents").select("id, clerk_org_id, ultravox_agent_id").eq("id", agent_id).execute()
            if fetch2.data and len(fetch2.data) > 0:
                row2 = fetch2.data[0]
                if row2.get("clerk_org_id") == CLERK_ORG_ID and row2.get("ultravox_agent_id") == ultravox_agent_id:
                    logger.info("SUCCESS: clerk_org_id and ultravox_agent_id set correctly after fallback PATCH.")
                else:
                    logger.warning("After PATCH still wrong: clerk_org_id=%s ultravox_agent_id=%s", row2.get("clerk_org_id"), row2.get("ultravox_agent_id"))

    logger.info("=== Test script finished ===")


if __name__ == "__main__":
    main()
