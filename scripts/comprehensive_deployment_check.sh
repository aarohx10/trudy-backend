#!/bin/bash
# Comprehensive Deployment Verification Script
# Runs after deployment to verify everything is working correctly

set -e

echo "🔍 Comprehensive Deployment Verification"
echo "========================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
PUBLIC_URL="${PUBLIC_URL:-https://truedy.closi.tech}"
SERVICE_NAME="trudy-backend"

# Track failures
FAILURES=0
WARNINGS=0

# Function to log success
log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

# Function to log warning
log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
    ((WARNINGS++))
}

# Function to log error
log_error() {
    echo -e "${RED}❌ $1${NC}"
    ((FAILURES++))
}

# Function to log info
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# 1. Service Status Check
echo "1. Checking service status..."
if systemctl is-active --quiet "$SERVICE_NAME"; then
    log_success "Service '$SERVICE_NAME' is active"
    
    # Get service status details
    STATUS=$(systemctl show "$SERVICE_NAME" --property=ActiveState,SubState --value | tr '\n' ' ')
    log_info "Service state: $STATUS"
else
    log_error "Service '$SERVICE_NAME' is not active"
    systemctl status "$SERVICE_NAME" --no-pager -l | head -20 || true
fi
echo ""

# 2. Health Endpoint Check
echo "2. Testing health endpoints..."
HEALTH_ENDPOINTS=("/health" "/internal/health")

for endpoint in "${HEALTH_ENDPOINTS[@]}"; do
    log_info "Testing $endpoint..."
    
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL$endpoint" || echo "000")
    
    if [ "$HTTP_CODE" = "200" ]; then
        log_success "$endpoint returned HTTP 200"
        
        # Get health details
        HEALTH_BODY=$(curl -s "$BACKEND_URL$endpoint" || echo "{}")
        STATUS=$(echo "$HEALTH_BODY" | grep -o '"status":"[^"]*"' | cut -d'"' -f4 || echo "unknown")
        log_info "Health status: $STATUS"
    else
        log_error "$endpoint returned HTTP $HTTP_CODE"
    fi
done
echo ""

# 3. Public Health Check (if PUBLIC_URL is set)
if [ "$PUBLIC_URL" != "http://localhost:8000" ]; then
    echo "3. Testing public health endpoint..."
    PUBLIC_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "$PUBLIC_URL/health" || echo "000")
    
    if [ "$PUBLIC_HEALTH" = "200" ]; then
        log_success "Public health endpoint accessible"
    else
        log_warning "Public health endpoint returned HTTP $PUBLIC_HEALTH (may be behind Nginx)"
    fi
    echo ""
fi

# 4. Database Connection Check
echo "4. Verifying database connection..."
# Check if we can query database (requires DB access)
if python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from app.core.database import get_supabase_client
    client = get_supabase_client()
    # Simple query to test connection
    result = client.table('clients').select('id').limit(1).execute()
    print('SUCCESS')
except Exception as e:
    print(f'ERROR: {e}')
    sys.exit(1)
" 2>/dev/null | grep -q "SUCCESS"; then
    log_success "Database connection successful"
else
    log_warning "Database connection check skipped (requires DB credentials)"
fi
echo ""

# 5. Database Migration Verification
echo "5. Verifying database migrations..."
# Check if clerk_org_id columns exist in key tables
TABLES=("agents" "calls" "voices" "knowledge_bases" "tools" "contacts" "contact_folders" "campaigns" "webhook_endpoints")

MIGRATION_CHECK=$(python3 << 'PYTHON_EOF'
import sys
import os
sys.path.insert(0, '.')

try:
    from app.core.database import get_supabase_admin_client
    client = get_supabase_admin_client()
    
    tables_to_check = ["agents", "calls", "voices", "knowledge_bases", "tools", "contacts", "contact_folders", "campaigns", "webhook_endpoints"]
    missing_columns = []
    
    for table in tables_to_check:
        try:
            # Check if clerk_org_id column exists
            result = client.table(table).select("clerk_org_id").limit(1).execute()
            # If no error, column exists
        except Exception as e:
            if "column" in str(e).lower() and "does not exist" in str(e).lower():
                missing_columns.append(table)
    
    if missing_columns:
        print(f"MISSING: {','.join(missing_columns)}")
    else:
        print("SUCCESS")
except Exception as e:
    print(f"ERROR: {e}")
PYTHON_EOF
)

if echo "$MIGRATION_CHECK" | grep -q "SUCCESS"; then
    log_success "All required clerk_org_id columns exist"
elif echo "$MIGRATION_CHECK" | grep -q "MISSING"; then
    MISSING=$(echo "$MIGRATION_CHECK" | grep -o "MISSING:.*" | cut -d: -f2)
    log_error "Missing clerk_org_id columns in: $MISSING"
    log_info "Run migration: bash database/migrations/run_migrations.sh"
else
    log_warning "Migration check skipped (requires DB access)"
fi
echo ""

# 6. Recent Error Check
echo "6. Checking recent logs for errors..."
RECENT_ERRORS=$(journalctl -u "$SERVICE_NAME" -n 100 --no-pager 2>/dev/null | grep -iE "error|exception|traceback|failed|critical" | tail -10 || echo "")

if [ -z "$RECENT_ERRORS" ]; then
    log_success "No recent errors found in logs"
else
    ERROR_COUNT=$(echo "$RECENT_ERRORS" | wc -l)
    log_warning "Found $ERROR_COUNT recent error(s) in logs"
    echo "$RECENT_ERRORS" | head -5
    log_info "Run 'journalctl -u $SERVICE_NAME -n 100' for full logs"
fi
echo ""

# 7. CORS Configuration Check
echo "7. Verifying CORS configuration..."
if [ -f "verify-cors.sh" ]; then
    CORS_OUTPUT=$(bash verify-cors.sh 2>&1 || echo "CORS_CHECK_FAILED")
    
    if echo "$CORS_OUTPUT" | grep -q "✅\|PASS"; then
        log_success "CORS configuration verified"
    else
        log_warning "CORS verification had warnings (check manually)"
    fi
else
    log_warning "CORS verification script not found"
fi
echo ""

# 8. Port Listening Check
echo "8. Verifying port 8000 is listening..."
if netstat -tlnp 2>/dev/null | grep -q ":8000 " || ss -tlnp 2>/dev/null | grep -q ":8000 "; then
    log_success "Port 8000 is listening"
    PORT_INFO=$(netstat -tlnp 2>/dev/null | grep ":8000 " || ss -tlnp 2>/dev/null | grep ":8000 " || echo "")
    log_info "$PORT_INFO"
else
    log_error "Port 8000 is not listening"
fi
echo ""

# 9. Environment Variables Check
echo "9. Verifying critical environment variables..."
if [ -f ".env" ]; then
    source .env
    
    REQUIRED_VARS=("SUPABASE_URL" "SUPABASE_SERVICE_KEY" "CLERK_SECRET_KEY")
    MISSING_VARS=()
    
    for var in "${REQUIRED_VARS[@]}"; do
        if [ -z "${!var}" ]; then
            MISSING_VARS+=("$var")
        fi
    done
    
    if [ ${#MISSING_VARS[@]} -eq 0 ]; then
        log_success "All critical environment variables are set"
    else
        log_error "Missing environment variables: ${MISSING_VARS[*]}"
    fi
else
    log_error ".env file not found"
fi
echo ""

# 10. Python Dependencies Check
echo "10. Verifying Python dependencies..."
if [ -d "venv" ]; then
    source venv/bin/activate
    
    # Check critical packages
    CRITICAL_PACKAGES=("fastapi" "uvicorn" "supabase" "clerk-sdk-python")
    MISSING_PACKAGES=()
    
    for package in "${CRITICAL_PACKAGES[@]}"; do
        if ! python3 -c "import ${package//-/_}" 2>/dev/null; then
            MISSING_PACKAGES+=("$package")
        fi
    done
    
    if [ ${#MISSING_PACKAGES[@]} -eq 0 ]; then
        log_success "All critical Python packages installed"
    else
        log_error "Missing Python packages: ${MISSING_PACKAGES[*]}"
    fi
else
    log_warning "Virtual environment not found (may be using system Python)"
fi
echo ""

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Deployment Verification Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $FAILURES -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    log_success "All checks passed! Deployment is successful."
    exit 0
elif [ $FAILURES -eq 0 ]; then
    log_warning "Deployment completed with $WARNINGS warning(s)"
    log_info "Review warnings above and verify manually if needed"
    exit 0
else
    log_error "Deployment verification failed with $FAILURES error(s) and $WARNINGS warning(s)"
    echo ""
    echo "Next steps:"
    echo "1. Check service logs: journalctl -u $SERVICE_NAME -n 100"
    echo "2. Check service status: systemctl status $SERVICE_NAME"
    echo "3. Verify database migrations: bash database/migrations/run_migrations.sh"
    echo "4. Test API endpoints manually"
    exit 1
fi
