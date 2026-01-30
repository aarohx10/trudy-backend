#!/bin/bash
# CORS Verification Script
# Tests CORS configuration from server-side

set -e

echo "=========================================="
echo "CORS Verification Script"
echo "=========================================="
echo ""

BACKEND_URL="http://localhost:8000"
TEST_ORIGIN="https://truedy.sendora.ai"

echo "Testing CORS configuration..."
echo "Backend URL: $BACKEND_URL"
echo "Test Origin: $TEST_ORIGIN"
echo ""

# Test 1: Health endpoint with Origin header (direct backend - CORS handled by Nginx)
echo "Test 1: Health endpoint with Origin header (direct backend access)"
echo "NOTE: CORS headers are added by Nginx, not backend, so this test may fail - this is EXPECTED"
HEALTH_RESPONSE=$(curl -s -i -H "Origin: $TEST_ORIGIN" "$BACKEND_URL/internal/health" || echo "FAILED")
if echo "$HEALTH_RESPONSE" | grep -qi "access-control-allow-origin"; then
    CORS_HEADER=$(echo "$HEALTH_RESPONSE" | grep -i "Access-Control-Allow-Origin" | head -1)
    echo "✅ PASS: CORS header found"
    echo "   $CORS_HEADER"
else
    echo "⚠️  EXPECTED: No CORS header (CORS handled by Nginx, not backend)"
    if echo "$HEALTH_RESPONSE" | grep -q "200 OK\|healthy"; then
        echo "✅ Backend is responding correctly"
    else
        echo "Response headers:"
        echo "$HEALTH_RESPONSE" | head -20
    fi
fi
echo ""

# Test 2: CORS health endpoint (direct backend - CORS handled by Nginx)
echo "Test 2: CORS health check endpoint (direct backend access)"
echo "NOTE: CORS headers are added by Nginx, not backend, so this test may fail - this is EXPECTED"
CORS_HEALTH_RESPONSE=$(curl -s -i -H "Origin: $TEST_ORIGIN" "$BACKEND_URL/api/v1/cors-health" || echo "FAILED")
if echo "$CORS_HEALTH_RESPONSE" | grep -qi "access-control-allow-origin"; then
    CORS_HEADER=$(echo "$CORS_HEALTH_RESPONSE" | grep -i "Access-Control-Allow-Origin" | head -1)
    echo "✅ PASS: CORS header found"
    echo "   $CORS_HEADER"
    
    # Check if cors_working is true in response body
    if echo "$CORS_HEALTH_RESPONSE" | grep -q '"cors_working":true'; then
        echo "✅ PASS: CORS health check reports working"
    else
        echo "⚠️  WARNING: CORS health check reports not working"
    fi
else
    echo "⚠️  EXPECTED: No CORS header (CORS handled by Nginx, not backend)"
    if echo "$CORS_HEALTH_RESPONSE" | grep -q "200 OK"; then
        echo "✅ Backend is responding correctly"
    fi
fi
echo ""

# Test 3: OPTIONS preflight request (direct backend - CORS handled by Nginx)
echo "Test 3: OPTIONS preflight request (direct backend access)"
echo "NOTE: CORS headers are added by Nginx, not backend, so this test may fail - this is EXPECTED"
OPTIONS_RESPONSE=$(curl -s -i -X OPTIONS -H "Origin: $TEST_ORIGIN" -H "Access-Control-Request-Method: GET" "$BACKEND_URL/internal/health" || echo "FAILED")
if echo "$OPTIONS_RESPONSE" | grep -qi "access-control-allow-origin"; then
    CORS_HEADER=$(echo "$OPTIONS_RESPONSE" | grep -i "Access-Control-Allow-Origin" | head -1)
    STATUS=$(echo "$OPTIONS_RESPONSE" | head -1 | grep -o "[0-9]\{3\}" || echo "unknown")
    echo "✅ PASS: OPTIONS preflight returns CORS headers"
    echo "   Status: $STATUS"
    echo "   $CORS_HEADER"
else
    echo "⚠️  EXPECTED: No CORS header (CORS handled by Nginx, not backend)"
    STATUS=$(echo "$OPTIONS_RESPONSE" | head -1 | grep -o "[0-9]\{3\}" || echo "unknown")
    echo "   Status: $STATUS (Nginx handles OPTIONS, not backend)"
fi
echo ""

# Test 4: Test through nginx HTTPS (THE IMPORTANT TEST - CORS is handled by Nginx)
if command -v curl >/dev/null 2>&1; then
    echo "Test 4: Testing through Nginx (THE CRITICAL TEST)"
    echo "NOTE: This is the test that matters - CORS is handled by Nginx"
    # Hit HTTPS so we get the actual API response with CORS (port 80 returns 301 with no CORS)
    # Try both internal/health and /health endpoints
    NGINX_RESPONSE=$(curl -s -i -k -H "Origin: $TEST_ORIGIN" "https://localhost/internal/health" 2>/dev/null || curl -s -i -k -H "Origin: $TEST_ORIGIN" "https://localhost/health" 2>/dev/null || echo "NGINX_NOT_ACCESSIBLE")
    if [ "$NGINX_RESPONSE" != "NGINX_NOT_ACCESSIBLE" ]; then
        if echo "$NGINX_RESPONSE" | grep -qi "access-control-allow-origin"; then
            CORS_HEADER=$(echo "$NGINX_RESPONSE" | grep -i "Access-Control-Allow-Origin" | head -1)
            echo "✅✅✅ PASS: Nginx forwards request and adds CORS headers ✅✅✅"
            echo "   $CORS_HEADER"
            echo "   🎉 CORS is working correctly!"
        else
            echo "❌ FAIL: Nginx request missing CORS headers"
            echo "   This indicates a problem with Nginx CORS configuration"
        fi
    else
        echo "ℹ️  INFO: Nginx HTTPS not accessible from localhost (script may be run off-server)"
        echo "   This is OK if you're testing from a remote machine"
    fi
    echo ""
fi

# Summary
echo "=========================================="
echo "Verification Complete"
echo "=========================================="
echo ""
echo "If all tests passed, CORS is configured correctly."
echo ""
echo "NOTE: Tests 1-3 test backend directly (localhost:8000) - CORS headers won't be present"
echo "      because CORS is handled by Nginx, not the backend. This is EXPECTED."
echo "      Test 4 (through Nginx) is the important one - if it passes, CORS is working!"
echo ""
echo "If tests failed, check:"
echo "  1. Backend service is running"
echo "  2. CORS origins include: $TEST_ORIGIN"
echo "  3. Nginx is configured correctly (CORS handled by Nginx, not backend)"
echo "  4. Nginx is forwarding Origin header"
