# Phase 4: Verification & Deployment Checklist

Complete this checklist before deploying to production.

## ✅ Pre-Deployment Checks

### 1. Unit Test - Auth
- [ ] Run `pytest tests/test_auth_org_id.py -v`
- [ ] Verify `verify_clerk_jwt` returns correct `org_id` for invited members
- [ ] Verify personal workspace fallback (user_id as org_id) works
- [ ] Verify `_effective_org_id` is set correctly in claims

**Command:**
```bash
pytest tests/test_auth_org_id.py -v
```

### 2. Integration Test - RLS
- [ ] Run `pytest tests/test_rls_org_isolation.py -v`
- [ ] Verify Org A data cannot be accessed with Org B token
- [ ] Verify database context isolation works correctly
- [ ] Verify RLS policies prevent cross-organization data access

**Command:**
```bash
pytest tests/test_rls_org_isolation.py -v
```

### 3. Frontend Leak Test
- [ ] Manually test organization switching in browser
- [ ] Verify agents list clears when switching organizations
- [ ] Verify agents list repopulates with new organization's data
- [ ] Verify no stale data from previous organization is displayed
- [ ] Check browser console for any errors

**Manual Steps:**
1. Log in as user in Org A
2. View agents list (should show Org A agents)
3. Switch to Org B using workspace switcher
4. Verify agents list clears immediately
5. Verify agents list shows Org B agents (or empty if none)
6. Switch back to Org A
7. Verify Org A agents reappear

### 4. Database Migrations
- [ ] Verify migration `021_add_org_id_context.sql` is applied
- [ ] Verify migration `022_add_clerk_org_id_to_tables.sql` is applied
- [ ] Check that `clerk_org_id` columns exist in all relevant tables:
  - [ ] `agents`
  - [ ] `calls`
  - [ ] `voices`
  - [ ] `knowledge_bases`
  - [ ] `tools`
  - [ ] `contacts`
  - [ ] `contact_folders`
  - [ ] `campaigns`

**SQL Check:**
```sql
SELECT column_name 
FROM information_schema.columns 
WHERE table_name IN ('agents', 'calls', 'voices', 'knowledge_bases', 'tools', 'contacts', 'contact_folders', 'campaigns')
AND column_name = 'clerk_org_id';
```

### 5. API Endpoint Verification
- [ ] Test `/api/v1/auth/me` returns `clerk_org_id`
- [ ] Test `/api/v1/agents` filters by `clerk_org_id`
- [ ] Test `/api/v1/calls` filters by `clerk_org_id`
- [ ] Test `/api/v1/voices` filters by `clerk_org_id`
- [ ] Test `/api/v1/contacts/list-folders` filters by `clerk_org_id`

**Test with curl:**
```bash
# Get token from Clerk
TOKEN="your_clerk_jwt_token"

# Test auth/me
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me

# Test agents list
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/agents
```

## 🚀 Deployment Steps

### 1. Run Deployment Verification Script
```bash
chmod +x scripts/verify_deployment.sh
./scripts/verify_deployment.sh
```

### 2. Deploy to Hetzner VPS
```powershell
# Windows PowerShell
.\sync-server.ps1 "Phase 4: Organization-first refactor deployment"
```

Or manually:
```bash
# SSH into server
ssh root@hetzner-truedy

# Navigate to backend directory
cd /opt/backend

# Pull latest changes
git fetch origin master
git reset --hard origin/master

# Run migrations (if not auto-applied)
# Check migration status first

# Restart backend service
systemctl restart trudy-backend
```

### 3. Verify Deployment
- [ ] Backend health check: `curl https://truedy.closi.tech/health`
- [ ] Test API endpoints with production URL
- [ ] Verify frontend can connect to backend
- [ ] Check logs for any errors

## 🧪 Post-Deployment Smoke Tests

### 1. Organization Access Test
Run the smoke test script:
```bash
python scripts/smoke_test_org_access.py
```

**Manual Steps:**
1. Create an organization in Clerk
2. Invite a test user (dummy email) to the organization
3. Admin creates an agent in the organization
4. Test user logs in
5. Verify test user can see the agent
6. Verify test user can edit the agent
7. Verify test user cannot see agents from other organizations

### 2. Team Collaboration Test
- [ ] User A creates an agent in Org X
- [ ] User B (same org) can see and edit the agent
- [ ] User C (different org) cannot see the agent
- [ ] User A creates a call
- [ ] User B can see the call in call logs
- [ ] User C cannot see the call

### 3. Data Isolation Test
- [ ] Create data in Org A (agents, calls, voices, contacts)
- [ ] Switch to Org B
- [ ] Verify Org B cannot see Org A's data
- [ ] Create data in Org B
- [ ] Switch back to Org A
- [ ] Verify Org A cannot see Org B's data

### 4. Personal Workspace Test
- [ ] User without organization (personal workspace)
- [ ] Verify user_id is used as org_id
- [ ] Verify user can create and access their own data
- [ ] Verify data is isolated from other users

## 📋 Final Checklist

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Frontend organization switching works correctly
- [ ] Database migrations applied successfully
- [ ] API endpoints return correct org-scoped data
- [ ] Deployment successful
- [ ] Smoke tests pass
- [ ] Team collaboration works
- [ ] Data isolation verified
- [ ] Personal workspace fallback works
- [ ] No errors in logs
- [ ] Performance is acceptable

## 🐛 Troubleshooting

### Issue: Agents not showing after org switch
**Solution:** Check React Query cache clearing in `app-store.ts`

### Issue: Cross-org data visible
**Solution:** Verify RLS policies and `set_org_context` RPC function

### Issue: Personal workspace not working
**Solution:** Verify `verify_clerk_jwt` fallback logic (user_id as org_id)

### Issue: API returns 403 for valid requests
**Solution:** Check JWT token includes `org_id` or verify fallback logic

## 📝 Notes

- All tests use mocks for external services
- Integration tests require database access
- Frontend tests may require React environment
- Smoke tests require actual Clerk tokens
- Manual verification is required for some scenarios
