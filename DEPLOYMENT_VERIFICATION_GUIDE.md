# Deployment Verification Guide

This guide explains the comprehensive deployment verification system that has been set up.

## Overview

The deployment process now includes **automatic verification** that checks:
- ✅ Service status
- ✅ Health endpoints
- ✅ Database migrations
- ✅ Recent errors
- ✅ CORS configuration
- ✅ Port listening
- ✅ Environment variables
- ✅ Python dependencies
- ✅ API endpoint structure

## Files Created/Updated

### 1. `scripts/comprehensive_deployment_check.sh`
**Purpose:** Runs on the server after deployment to verify everything is working.

**Checks:**
- Service status (systemd)
- Health endpoints (`/health`, `/internal/health`)
- Database connection
- Database migrations (verifies `clerk_org_id` columns exist)
- Recent error logs
- CORS configuration
- Port 8000 listening
- Environment variables
- Python dependencies

**Usage:**
```bash
# Automatically runs during deploy.sh
# Or run manually:
bash scripts/comprehensive_deployment_check.sh
```

### 2. `scripts/verify_deployment_remote.sh` (Updated)
**Purpose:** Run from your local machine to verify remote deployment.

**Checks:**
- Public health endpoint
- Service status (via SSH)
- Recent errors (via SSH)
- Database migrations (via SSH)
- API endpoint structure
- CORS configuration

**Usage:**
```bash
bash scripts/verify_deployment_remote.sh
```

### 3. `sync-server.ps1` (Updated)
**Purpose:** PowerShell script to push code and deploy.

**New Features:**
- ✅ Captures deployment output
- ✅ Shows deployment summary
- ✅ Runs post-deployment verification:
  - Health check
  - Service status check
  - Recent errors check
  - Port verification
- ✅ Better error handling and debugging info

**Usage:**
```powershell
.\sync-server.ps1 "Your commit message"
```

### 4. `deploy.sh` (Updated)
**Purpose:** Server-side deployment script.

**New Features:**
- ✅ Automatically runs `comprehensive_deployment_check.sh` after deployment
- ✅ Shows verification results
- ✅ Non-fatal if verification has warnings (allows deployment to complete)

## Verification Flow

```
1. sync-server.ps1 (Local)
   ↓
2. Git push to GitHub
   ↓
3. SSH to server
   ↓
4. deploy.sh (Server)
   ├─ Install dependencies
   ├─ Run migrations
   ├─ Restart service
   ├─ Health checks
   ├─ CORS verification
   └─ comprehensive_deployment_check.sh (NEW!)
      ├─ Service status
      ├─ Health endpoints
      ├─ Database checks
      ├─ Error logs
      └─ Dependencies
   ↓
5. sync-server.ps1 (Local)
   ├─ Capture deployment output
   ├─ Show summary
   └─ Post-deployment verification
      ├─ Health check
      ├─ Service status
      ├─ Recent errors
      └─ Port check
```

## What Gets Verified

### ✅ Service Status
- Checks if `trudy-backend` service is active
- Shows service state details

### ✅ Health Endpoints
- Tests `/health` endpoint
- Tests `/internal/health` endpoint
- Verifies HTTP 200 response
- Parses health status JSON

### ✅ Database Migrations
- Verifies `clerk_org_id` columns exist in:
  - `agents`
  - `calls`
  - `voices`
  - `knowledge_bases`
  - `tools`
  - `contacts`
  - `contact_folders`
  - `campaigns`
  - `webhook_endpoints`

### ✅ Recent Errors
- Checks last 50-100 log entries
- Looks for: `error`, `exception`, `traceback`, `failed`, `critical`
- Shows top 5 errors if found

### ✅ CORS Configuration
- Tests CORS headers
- Verifies Origin handling
- Checks Nginx configuration

### ✅ Port & Dependencies
- Verifies port 8000 is listening
- Checks critical Python packages installed
- Verifies environment variables set

## Example Output

### Successful Deployment
```
✅ Deployment Status: SUCCESS

✨ Your backend is updated and live at:
   https://truedy.closi.tech

💡 Next steps:
   - Test API endpoints with your application
   - Verify organization isolation works correctly
   - Check database migrations if needed
```

### Deployment with Warnings
```
⚠️  Deployment Status: COMPLETED WITH WARNINGS

The deployment script completed, but health check failed.
This may be normal if the service is still starting.

Check manually:
   ssh root@hetzner-truedy 'systemctl status trudy-backend'
   ssh root@hetzner-truedy 'journalctl -u trudy-backend -n 100'
```

### Failed Deployment
```
❌ Deployment FAILED!

Deployment output:
[shows last 30 lines of output]

Debugging steps:
1. Check service status:
   ssh root@hetzner-truedy 'systemctl status trudy-backend'
...
```

## Manual Verification

If automatic verification fails or you want to verify manually:

### 1. Check Service Status
```bash
ssh root@hetzner-truedy 'systemctl status trudy-backend'
```

### 2. Check Health Endpoint
```bash
curl https://truedy.closi.tech/health
```

### 3. Check Recent Logs
```bash
ssh root@hetzner-truedy 'journalctl -u trudy-backend -n 100'
```

### 4. Run Comprehensive Check
```bash
ssh root@hetzner-truedy 'cd /opt/backend && bash scripts/comprehensive_deployment_check.sh'
```

### 5. Run Remote Verification
```bash
bash scripts/verify_deployment_remote.sh
```

## Troubleshooting

### Health Check Fails
- Service may still be starting (wait 10-30 seconds)
- Check service logs: `journalctl -u trudy-backend -n 100`
- Verify port 8000 is listening: `ss -tlnp | grep :8000`

### Database Migration Check Fails
- Run migrations manually: `bash database/migrations/run_migrations.sh`
- Check database connection: Verify `.env` file has correct credentials
- Verify migration files exist: `ls database/migrations/026_*.sql`

### Service Not Active
- Check service status: `systemctl status trudy-backend`
- Check service logs: `journalctl -u trudy-backend -n 100`
- Restart service: `systemctl restart trudy-backend`

### Recent Errors Found
- Review error logs: `journalctl -u trudy-backend -n 100 | grep -i error`
- Check if errors are critical or warnings
- Verify environment variables are set correctly

## Next Steps After Deployment

1. **Test API Endpoints**
   - Use Postman or your frontend
   - Test with valid Clerk tokens
   - Verify organization isolation

2. **Test CRUD Operations**
   - Create/update agents
   - Create knowledge bases
   - Test calls, campaigns, contacts

3. **Verify Organization Isolation**
   - Switch organizations in frontend
   - Verify data is properly scoped
   - Test billing/subscription flows

4. **Monitor Logs**
   - Watch for any new errors
   - Verify performance
   - Check database queries

## Summary

✅ **Automatic verification** runs during every deployment  
✅ **Comprehensive checks** verify all critical components  
✅ **Clear output** shows what passed/failed  
✅ **Manual verification** scripts available for troubleshooting  
✅ **Better error handling** with debugging steps  

Your deployment process is now **production-ready** with comprehensive verification! 🚀
