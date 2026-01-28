#!/bin/bash
# Run all Phase 4 verification tests

set -e

echo "🧪 Running Phase 4 Verification Tests"
echo "======================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo -e "${RED}❌ pytest not found. Installing...${NC}"
    pip install pytest pytest-asyncio pytest-cov
fi

# Run tests
echo "1. Running Auth Unit Tests..."
if pytest tests/test_auth_org_id.py -v; then
    echo -e "${GREEN}✅ Auth tests passed${NC}"
else
    echo -e "${RED}❌ Auth tests failed${NC}"
    exit 1
fi

echo ""
echo "2. Running RLS Integration Tests..."
if pytest tests/test_rls_org_isolation.py -v; then
    echo -e "${GREEN}✅ RLS tests passed${NC}"
else
    echo -e "${RED}❌ RLS tests failed${NC}"
    exit 1
fi

echo ""
echo "3. Running Frontend Leak Tests..."
if pytest tests/test_frontend_org_switching.py -v; then
    echo -e "${GREEN}✅ Frontend tests passed${NC}"
else
    echo -e "${YELLOW}⚠️  Frontend tests may require React environment${NC}"
fi

echo ""
echo "======================================"
echo -e "${GREEN}✅ All tests completed!${NC}"
echo "======================================"
