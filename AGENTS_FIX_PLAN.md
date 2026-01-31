# Agents Creation Fix Plan

## Core Principle
**Match Knowledge Bases Pattern EXACTLY** - Knowledge bases work perfectly, so agents should follow the same pattern.

## Critical Fixes Required

### Fix 1: Never Modify Record Dictionary After Creation
**Problem:** Agents modify `agent_record` dictionary after creation (adding `ultravox_agent_id`, changing `status`), which could corrupt `clerk_org_id`.

**Solution:**
- Create COMPLETE record dictionary BEFORE any external calls
- Set `clerk_org_id` ONCE and NEVER modify it
- If Ultravox ID needs to be added, use separate `db.update()` call AFTER insert

**Implementation:**
```python
# BEFORE (BROKEN):
agent_record = {"id": agent_id, "clerk_org_id": clerk_org_id, ...}
ultravox_response = await create_agent_ultravox_first(...)
agent_record["ultravox_agent_id"] = ultravox_response.get("agentId")  # MODIFICATION!
agent_record["status"] = "active"  # MODIFICATION!
db.insert("agents", agent_record)

# AFTER (FIXED):
agent_record = {"id": agent_id, "clerk_org_id": clerk_org_id, ...}
# Insert FIRST (like knowledge bases)
db.insert("agents", agent_record)
# Then do external operations
ultravox_response = await create_agent_ultravox_first(...)
# Update database with Ultravox ID (separate call)
db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
    "ultravox_agent_id": ultravox_response.get("agentId"),
    "status": "active"
})
```

---

### Fix 2: Insert FIRST, External Operations SECOND
**Problem:** Agents create in Ultravox FIRST, then insert to database. If Ultravox succeeds but insert fails, we have orphaned Ultravox agents.

**Solution:**
- Match knowledge bases: Insert to database FIRST
- Then do external operations (Ultravox creation)
- Then update database with external IDs

**Implementation:**
```python
# BEFORE (BROKEN):
ultravox_response = await create_agent_ultravox_first(...)  # External FIRST
db.insert("agents", agent_record)  # Database SECOND

# AFTER (FIXED):
db.insert("agents", agent_record)  # Database FIRST
ultravox_response = await create_agent_ultravox_first(...)  # External SECOND
db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
    "ultravox_agent_id": ultravox_response.get("agentId")
})
```

---

### Fix 3: Remove Immediate Re-Fetch
**Problem:** Agents try to re-fetch immediately after insert, which fails. Knowledge bases don't do this.

**Solution:**
- Don't capture insert return value (like knowledge bases)
- Trust that insert succeeded
- Only re-fetch at the END (after all operations), if needed for response

**Implementation:**
```python
# BEFORE (BROKEN):
inserted_result = db.insert("agents", agent_record)
created_agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})  # FAILS!

# AFTER (FIXED):
db.insert("agents", agent_record)  # Don't capture return value
# ... do other operations ...
# Re-fetch only at the end (if needed for response)
created_agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})
```

---

### Fix 4: Consolidate Conditional Paths
**Problem:** Multiple code paths (Ultravox sync vs draft-only) with different insert logic.

**Solution:**
- Single insert function used by ALL paths
- All paths use same pattern
- Conditional logic only affects WHAT gets inserted, not HOW

**Implementation:**
```python
# BEFORE (BROKEN):
if validation_result["can_sync"]:
    # Insert logic here
    db.insert("agents", agent_record)
else:
    if reason == "voice_required":
        # Different insert logic here
        db.insert("agents", agent_record)

# AFTER (FIXED):
# Prepare record based on conditions
if validation_result["can_sync"]:
    agent_record["status"] = "creating"  # Will be updated after Ultravox
else:
    agent_record["status"] = "draft"

# Single insert point (used by ALL paths)
db.insert("agents", agent_record)

# Then handle external operations
if validation_result["can_sync"]:
    ultravox_response = await create_agent_ultravox_first(...)
    db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
        "ultravox_agent_id": ultravox_response.get("agentId"),
        "status": "active"
    })
```

---

### Fix 5: Ensure clerk_org_id Never Changes
**Problem:** `clerk_org_id` might be getting corrupted during dictionary modifications.

**Solution:**
- Store `clerk_org_id` in a separate variable
- Always use that variable, never `agent_record.get("clerk_org_id")`
- Add assertion before insert: `assert agent_record["clerk_org_id"] == clerk_org_id`

**Implementation:**
```python
# Store in variable (source of truth)
clerk_org_id = current_user.get("clerk_org_id")

# Always use variable, never dictionary value
agent_record = {
    "id": agent_id,
    "clerk_org_id": clerk_org_id,  # Use variable
    ...
}

# Before ANY modification, verify
assert agent_record["clerk_org_id"] == clerk_org_id

# Before insert, verify again
assert agent_record["clerk_org_id"] == clerk_org_id
db.insert("agents", agent_record)
```

---

### Fix 6: Match Knowledge Bases Response Pattern
**Problem:** Agents try to return immediately after insert, but knowledge bases return after async operations.

**Solution:**
- Insert record
- Do async operations
- Re-fetch at end
- Return fetched record

**Implementation:**
```python
# Match knowledge bases exactly:
db.insert("agents", agent_record)  # Insert FIRST

# Do async operations
if validation_result["can_sync"]:
    ultravox_response = await create_agent_ultravox_first(...)
    db.update("agents", {"id": agent_id, "clerk_org_id": clerk_org_id}, {
        "ultravox_agent_id": ultravox_response.get("agentId"),
        "status": "active"
    })

# Re-fetch at end (like knowledge bases)
created_agent = db.select_one("agents", {"id": agent_id, "clerk_org_id": clerk_org_id})

return {
    "data": created_agent,
    "meta": ResponseMeta(...)
}
```

---

## Implementation Order

1. **Fix 5**: Ensure `clerk_org_id` never changes (add assertions)
2. **Fix 1**: Never modify record dictionary after creation
3. **Fix 2**: Insert FIRST, external operations SECOND
4. **Fix 3**: Remove immediate re-fetch
5. **Fix 4**: Consolidate conditional paths
6. **Fix 6**: Match knowledge bases response pattern

## Testing Strategy

After each fix:
1. Create agent via `/agents/draft`
2. Verify agent is created successfully
3. Immediately fetch via `GET /agents/{id}`
4. Verify agent is found
5. List via `GET /agents`
6. Verify agent appears in list
7. Check logs for `clerk_org_id` consistency

## Success Criteria

- Agent creation succeeds (200 response)
- Agent is immediately retrievable (no 404)
- Agent appears in list endpoint
- All logs show consistent `clerk_org_id` throughout
- No "Failed to create/retrieve agent" errors
