#!/bin/bash
# Remote Deployment Verification Script
# Run this on your local machine to verify remote deployment
# Usage: bash scripts/verify_deployment_remote.sh

set -e

echo "🔍 Comprehensive Remote Deployment Verification"
echo "================================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BACKEND_URL="https://truedy.closi.tech"
SSH_HOST="root@hetzner-truedy"
SERVICE_NAME="trudy-backend"

FAILURES=0
WARNINGS=0

# 1. Health Check
echo "1. Checking backend health..."
HEALTH_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/health" || echo "000")

if [ "$HEALTH_RESPONSE" = "200" ]; then
    echo -e "${GREEN}✅ Backend is healthy (HTTP $HEALTH_RESPONSE)${NC}"
    
    # Get health details
    HEALTH_BODY=$(curl -s "$BACKEND_URL/health")
    echo "   Health status: $(echo $HEALTH_BODY | grep -o '"status":"[^"]*"' | cut -d'"' -f4)"
else
    echo -e "${RED}❌ Backend health check failed (HTTP $HEALTH_RESPONSE)${NC}"
    exit 1
fi

# 2. Internal Health Check
echo ""
echo "2. Checking internal health endpoint..."
INTERNAL_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/internal/health" || echo "000")

if [ "$INTERNAL_HEALTH" = "200" ]; then
    echo -e "${GREEN}✅ Internal health check passed${NC}"
else
    echo -e "${YELLOW}⚠️  Internal health check returned HTTP $INTERNAL_HEALTH${NC}"
fi

# 3. CORS Check
echo ""
echo "3. Checking CORS configuration..."
CORS_RESPONSE=$(curl -s -i -H "Origin: https://truedy.sendora.ai" "$BACKEND_URL/api/v1/cors-health" || echo "FAILED")

if echo "$CORS_RESPONSE" | grep -qi "access-control-allow-origin"; then
    echo -e "${GREEN}✅ CORS headers present${NC}"
    echo "$CORS_RESPONSE" | grep -i "access-control" | head -3
else
    echo -e "${YELLOW}⚠️  CORS headers not found (may be handled by Nginx)${NC}"
fi

# 4. Check Service Status (via SSH)
echo ""
echo "4. Checking service status on server..."
SSH_OUTPUT=$(ssh root@hetzner-truedy "systemctl is-active trudy-backend 2>&1" || echo "SSH_FAILED")

if [ "$SSH_OUTPUT" = "active" ]; then
    echo -e "${GREEN}✅ Service is active${NC}"
elif [ "$SSH_OUTPUT" = "SSH_FAILED" ]; then
    echo -e "${YELLOW}⚠️  Could not check service status (SSH failed)${NC}"
else
    echo -e "${RED}❌ Service status: $SSH_OUTPUT${NC}"
fi

# 5. Check Recent Logs for Errors
echo ""
echo "5. Checking recent logs for errors..."
RECENT_ERRORS=$(ssh root@hetzner-truedy "journalctl -u trudy-backend -n 50 --no-pager | grep -i 'error\|exception\|traceback' | tail -5" 2>&1 || echo "SSH_FAILED")

if [ "$RECENT_ERRORS" = "SSH_FAILED" ]; then
    echo -e "${YELLOW}⚠️  Could not check logs (SSH failed)${NC}"
elif [ -z "$RECENT_ERRORS" ]; then
    echo -e "${GREEN}✅ No recent errors found${NC}"
else
    echo -e "${YELLOW}⚠️  Recent errors found:${NC}"
    echo "$RECENT_ERRORS"
fi

# 6. Database Migration Status (via SSH)
echo ""
echo "6. Checking database migration status..."
MIGRATION_STATUS=$(ssh "$SSH_HOST" "cd /opt/backend && bash -c 'source venv/bin/activate 2>/dev/null || true; python3 -c \"
import sys
sys.path.insert(0, \\\"/opt/backend\\\")
try:
    from app.core.database import get_supabase_admin_client
    client = get_supabase_admin_client()
    tables = [\\\"agents\\\", \\\"calls\\\", \\\"voices\\\", \\\"knowledge_bases\\\"]
    missing = []
    for table in tables:
        try:
            client.table(table).select(\\\"clerk_org_id\\\").limit(1).execute()
        except:
            missing.append(table)
    if missing:
        print(f\\\"MISSING:{}\\\".join(missing))
    else:
        print(\\\"SUCCESS\\\")
except Exception as e:
    print(f\\\"ERROR:{e}\\\")
\"' 2>&1" || echo "SSH_FAILED")

if echo "$MIGRATION_STATUS" | grep -q "SUCCESS"; then
    echo -e "${GREEN}✅ Database migrations verified${NC}"
elif echo "$MIGRATION_STATUS" | grep -q "MISSING"; then
    MISSING=$(echo "$MIGRATION_STATUS" | grep -o "MISSING:.*")
    echo -e "${RED}❌ Missing migrations: $MISSING${NC}"
    ((FAILURES++))
elif echo "$MIGRATION_STATUS" | grep -q "SSH_FAILED"; then
    echo -e "${YELLOW}⚠️  Could not check migrations (SSH failed)${NC}"
    ((WARNINGS++))
else
    echo -e "${YELLOW}⚠️  Migration check inconclusive${NC}"
    ((WARNINGS++))
fi

# 7. Test API Endpoint (if token available)
echo ""
echo "7. Testing API endpoint structure..."
# Test if /api/v1/auth/me endpoint exists (will fail auth, but should return 401, not 404)
AUTH_TEST=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/api/v1/auth/me" || echo "000")

if [ "$AUTH_TEST" = "401" ] || [ "$AUTH_TEST" = "403" ]; then
    echo -e "${GREEN}✅ API endpoint exists (auth required as expected)${NC}"
elif [ "$AUTH_TEST" = "404" ]; then
    echo -e "${RED}❌ API endpoint not found (404)${NC}"
    ((FAILURES++))
else
    echo -e "${YELLOW}⚠️  API endpoint returned HTTP $AUTH_TEST${NC}"
    ((WARNINGS++))
fi

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Deployment Verification Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $FAILURES -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✅ All checks passed! Deployment is successful.${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Test API endpoints with valid Clerk tokens"
    echo "2. Test creating/updating agents, knowledge bases, etc."
    echo "3. Verify organization isolation works correctly"
    echo "4. Test billing/subscription flows"
    exit 0
elif [ $FAILURES -eq 0 ]; then
    echo -e "${YELLOW}⚠️  Deployment completed with $WARNINGS warning(s)${NC}"
    echo ""
    echo "Review warnings above. Deployment may still be functional."
    echo "Test manually to confirm."
    exit 0
else
    echo -e "${RED}❌ Deployment verification failed with $FAILURES error(s)${NC}"
    echo ""
    echo "Debugging steps:"
    echo "1. SSH into server: ssh $SSH_HOST"
    echo "2. Check service: systemctl status $SERVICE_NAME"
    echo "3. Check logs: journalctl -u $SERVICE_NAME -n 100"
    echo "4. Run deployment check: cd /opt/backend && bash scripts/comprehensive_deployment_check.sh"
    exit 1
fi
