#!/bin/bash
# Deployment Verification Script
# Verifies that the organization-first refactor is properly deployed

set -e

echo "🔍 Starting Deployment Verification..."
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if backend is running
echo "1. Checking backend health..."
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
HEALTH_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/health" || echo "000")

if [ "$HEALTH_RESPONSE" = "200" ]; then
    echo -e "${GREEN}✅ Backend is healthy${NC}"
else
    echo -e "${RED}❌ Backend health check failed (HTTP $HEALTH_RESPONSE)${NC}"
    exit 1
fi

# Check database migrations
echo ""
echo "2. Verifying database migrations..."
echo "   Checking for org_id context migration (021_add_org_id_context.sql)..."
# This would require database access - placeholder for actual check
echo -e "${YELLOW}⚠️  Database migration check requires DB access${NC}"

# Check for clerk_org_id columns
echo ""
echo "3. Verifying clerk_org_id columns exist..."
echo "   This requires database access - run manually:"
echo "   SELECT column_name FROM information_schema.columns WHERE table_name='agents' AND column_name='clerk_org_id';"

# Check API endpoints
echo ""
echo "4. Testing API endpoints..."
echo "   Testing /api/v1/auth/me endpoint..."
# This would require authentication token
echo -e "${YELLOW}⚠️  API endpoint test requires authentication token${NC}"

# Check frontend build
echo ""
echo "5. Checking frontend build..."
if [ -d "frontend/.next" ]; then
    echo -e "${GREEN}✅ Frontend build exists${NC}"
else
    echo -e "${YELLOW}⚠️  Frontend build not found - run 'npm run build' in frontend directory${NC}"
fi

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Deployment Verification Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ Backend health check: PASSED"
echo "⚠️  Database migrations: Requires manual verification"
echo "⚠️  API endpoints: Requires authentication token"
echo "✅ Frontend build: Checked"
echo ""
echo "Next steps:"
echo "1. Run database migration verification manually"
echo "2. Test API endpoints with valid Clerk tokens"
echo "3. Test frontend organization switching"
echo "4. Run smoke tests with test users"
echo ""
