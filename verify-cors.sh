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

# Test 1: Health endpoint with Origin header
echo "Test 1: Health endpoint with Origin header"
HEALTH_RESPONSE=$(curl -s -i -H "Origin: $TEST_ORIGIN" "$BACKEND_URL/api/v1/health" || echo "FAILED")
if echo "$HEALTH_RESPONSE" | grep -qi "access-control-allow-origin"; then
    CORS_HEADER=$(echo "$HEALTH_RESPONSE" | grep -i "Access-Control-Allow-Origin" | head -1)
    echo "✅ PASS: CORS header found"
    echo "   $CORS_HEADER"
else
    echo "❌ FAIL: No CORS header found"
    echo "Response headers:"
    echo "$HEALTH_RESPONSE" | head -20
fi
echo ""

# Test 2: CORS health endpoint
echo "Test 2: CORS health check endpoint"
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
    echo "❌ FAIL: No CORS header found"
fi
echo ""

# Test 3: OPTIONS preflight request
echo "Test 3: OPTIONS preflight request"
OPTIONS_RESPONSE=$(curl -s -i -X OPTIONS -H "Origin: $TEST_ORIGIN" -H "Access-Control-Request-Method: GET" "$BACKEND_URL/api/v1/health" || echo "FAILED")
if echo "$OPTIONS_RESPONSE" | grep -qi "access-control-allow-origin"; then
    CORS_HEADER=$(echo "$OPTIONS_RESPONSE" | grep -i "Access-Control-Allow-Origin" | head -1)
    STATUS=$(echo "$OPTIONS_RESPONSE" | head -1 | grep -o "[0-9]\{3\}" || echo "unknown")
    echo "✅ PASS: OPTIONS preflight returns CORS headers"
    echo "   Status: $STATUS"
    echo "   $CORS_HEADER"
else
    echo "❌ FAIL: OPTIONS preflight missing CORS headers"
    echo "Response:"
    echo "$OPTIONS_RESPONSE" | head -10
fi
echo ""

# Test 4: Test through nginx (if accessible)
if command -v curl >/dev/null 2>&1; then
    echo "Test 4: Testing through nginx (if configured)"
    NGINX_URL="http://localhost"
    NGINX_RESPONSE=$(curl -s -i -H "Origin: $TEST_ORIGIN" "$NGINX_URL/api/v1/health" 2>/dev/null || echo "NGINX_NOT_ACCESSIBLE")
    if [ "$NGINX_RESPONSE" != "NGINX_NOT_ACCESSIBLE" ]; then
        if echo "$NGINX_RESPONSE" | grep -qi "access-control-allow-origin"; then
            CORS_HEADER=$(echo "$NGINX_RESPONSE" | grep -i "Access-Control-Allow-Origin" | head -1)
            echo "✅ PASS: Nginx forwards request and backend adds CORS headers"
            echo "   $CORS_HEADER"
        else
            echo "⚠️  WARNING: Nginx request missing CORS headers"
        fi
    else
        echo "ℹ️  INFO: Nginx not accessible from this location (expected if running on server)"
    fi
    echo ""
fi

# Summary
echo "=========================================="
echo "Verification Complete"
echo "=========================================="
echo ""
echo "If all tests passed, CORS is configured correctly."
echo "If tests failed, check:"
echo "  1. Backend service is running"
echo "  2. CORS origins include: $TEST_ORIGIN"
echo "  3. Backend middleware is adding CORS headers"
echo "  4. Nginx is forwarding Origin header"
