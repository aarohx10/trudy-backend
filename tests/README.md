# Phase 4: Verification & Deployment Tests

This directory contains tests for verifying the organization-first refactor.

## Test Files

### 1. `test_auth_org_id.py`
**Unit Test - Auth**: Verifies that `verify_clerk_jwt` returns the correct `org_id` for invited members.

**Tests:**
- `test_verify_clerk_jwt_with_org_id`: Verifies org_id extraction when present
- `test_verify_clerk_jwt_personal_workspace_fallback`: Verifies user_id fallback for personal workspaces
- `test_verify_clerk_jwt_invited_member`: Verifies org_id for invited organization members

**Run:**
```bash
pytest tests/test_auth_org_id.py -v
```

### 2. `test_rls_org_isolation.py`
**Integration Test - RLS**: Attempts to fetch "Org A" data using an "Org B" token. Must return 403 or empty list.

**Tests:**
- `test_rls_prevents_cross_org_agent_access`: Verifies agents are isolated by organization
- `test_rls_prevents_cross_org_call_access`: Verifies calls are isolated by organization
- `test_database_context_isolation`: Verifies database context properly isolates queries

**Run:**
```bash
pytest tests/test_rls_org_isolation.py -v
```

### 3. `test_frontend_org_switching.py`
**Frontend Leak Test**: Verifies that switching organizations clears the agents list and repopulates with zero lag or stale data.

**Tests:**
- `test_org_switch_clears_query_cache`: Verifies React Query cache is cleared on org switch
- `test_agents_query_key_includes_org_id`: Verifies query keys include orgId for cache isolation
- `test_hard_refresh_on_org_switch`: Verifies hard refresh is triggered on org switch

**Run:**
```bash
pytest tests/test_frontend_org_switching.py -v
```

## Scripts

### 1. `scripts/verify_deployment.sh`
**Deployment Verification Script**: Checks that the organization-first refactor is properly deployed.

**Checks:**
- Backend health
- Database migrations
- API endpoints
- Frontend build

**Run:**
```bash
chmod +x scripts/verify_deployment.sh
./scripts/verify_deployment.sh
```

### 2. `scripts/smoke_test_org_access.py`
**Final Smoke Test**: Invites a dummy email to an organization, logs in, and confirms they can edit an agent created by the admin.

**Prerequisites:**
1. Create an organization in Clerk
2. Invite a test user to the organization
3. Admin creates an agent
4. Test user logs in

**Run:**
```bash
python scripts/smoke_test_org_access.py
```

## Running All Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html

# Run specific test file
pytest tests/test_auth_org_id.py -v
```

## Manual Verification Checklist

- [ ] Unit Test - Auth: `verify_clerk_jwt` returns correct `org_id` for invited members
- [ ] Integration Test - RLS: Org A data with Org B token returns 403 or empty list
- [ ] Frontend Leak Test: Switch organizations and verify agents list clears and repopulates
- [ ] Deployment: Push changes to Hetzner via PowerShell script
- [ ] Smoke Test: Invite dummy email, log in, confirm they can edit admin's agent

## Notes

- Tests use mocks for external services (Clerk, Supabase)
- Integration tests require database access
- Frontend tests require React Query setup
- Smoke tests require actual Clerk tokens (manual input)
