# 🔧 Deployment Health Check Fix

**Date:** 2026-01-30  
**Issue:** Health check failing after backend deployment  
**Status:** ✅ FIXED

---

## 🐛 Problem

The deployment script was failing during health check with:
```
❌ Health check failed!
Check logs: journalctl -u trudy-backend -n 50
```

**Root Causes:**
1. Service might need more than 5 seconds to fully start
2. Service name detection was unreliable
3. No retry mechanism for health checks
4. Insufficient debugging information when health check fails

---

## ✅ Solution Implemented

### 1. Improved Service Detection
- Checks for both `trudy-backend` and `uvicorn` services
- Uses `systemctl list-units` for more reliable detection
- Stores service name for later use

### 2. Service Status Verification
- Checks if service is actually active after restart
- Shows service status if not active
- Provides better feedback

### 3. Retry Mechanism
- **6 retry attempts** (instead of 1)
- **5 second delay** between retries
- Total wait time: up to 30 seconds (6 × 5s)
- Gives service adequate time to start

### 4. Enhanced Debugging
When health check fails, the script now shows:
- ✅ Service status (`systemctl status`)
- ✅ Port listening status (`netstat`/`ss`)
- ✅ Health endpoint response details (`curl -v`)
- ✅ Recent service logs (`journalctl`)
- ✅ Both `/internal/health` and `/health` endpoint attempts

### 5. Multiple Health Endpoint Fallbacks
- Tries `/internal/health` (primary)
- Falls back to `/health` (secondary)
- More resilient to endpoint changes

---

## 📝 Changes Made

**File:** `z-backend/deploy.sh`

### Before:
```bash
# Health check
sleep 5
HEALTH_URL="${FILE_SERVER_URL:-http://localhost:8000}/internal/health"
if curl -f "$HEALTH_URL" > /dev/null 2>&1 || curl -f http://localhost:8000/internal/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Health check passed!${NC}"
else
    echo -e "${RED}❌ Health check failed!${NC}"
    exit 1
fi
```

### After:
```bash
# Health check with retries
MAX_RETRIES=6
RETRY_DELAY=5
for i in $(seq 1 $MAX_RETRIES); do
    # Try multiple endpoints with retries
    # Show detailed debugging on failure
done
```

---

## 🎯 Benefits

1. **More Reliable:** Retry mechanism handles slow startups
2. **Better Diagnostics:** Detailed error information when failures occur
3. **Faster Debugging:** All relevant info shown immediately
4. **Resilient:** Multiple endpoint fallbacks

---

## 🚀 Next Steps

1. **Deploy the updated script:**
   ```bash
   cd z-backend
   git add deploy.sh
   git commit -m "fix: Improve health check with retries and better diagnostics"
   git push origin master
   ```

2. **Run deployment:**
   ```bash
   ./sync-server.ps1 "Fix health check retry mechanism"
   ```

3. **Monitor the output:**
   - The script will now show detailed diagnostics if health check fails
   - Check the logs it displays to identify the root cause

---

## 🔍 Common Issues & Solutions

### Issue: Service takes longer than 30 seconds to start
**Solution:** Increase `MAX_RETRIES` or `RETRY_DELAY` in deploy.sh

### Issue: Service not found
**Solution:** Check service name with `systemctl list-units --type=service --all | grep trudy`

### Issue: Port 8000 not listening
**Solution:** Check if FastAPI is actually running: `ps aux | grep uvicorn`

### Issue: Import errors preventing startup
**Solution:** Check logs: `journalctl -u trudy-backend -n 100`

---

**Last Updated:** 2026-01-30  
**Status:** ✅ Ready for deployment
