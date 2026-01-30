#!/bin/bash
# Deployment script for Hetzner VPS
# Run this script on your production server

set -e  # Exit on error

echo "🚀 Starting Trudy Backend Deployment..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo -e "${GREEN}Activating virtual environment...${NC}"
source venv/bin/activate

# Install/update dependencies
echo -e "${GREEN}Installing dependencies...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

# Run database migrations
echo -e "${GREEN}Running database migrations...${NC}"
if [ -f "database/migrations/run_migrations.sh" ]; then
    bash database/migrations/run_migrations.sh
else
    echo -e "${YELLOW}Migration script not found, skipping...${NC}"
fi

# Check and create storage directory
echo -e "${GREEN}Checking storage directory...${NC}"
STORAGE_PATH="${FILE_STORAGE_PATH:-/mnt/storage}"
if [ ! -d "$STORAGE_PATH" ]; then
    echo -e "${YELLOW}Storage directory not found. Creating $STORAGE_PATH...${NC}"
    sudo mkdir -p "$STORAGE_PATH"
    sudo chown root:root "$STORAGE_PATH"
    sudo chmod 755 "$STORAGE_PATH"
    echo -e "${GREEN}Storage directory created${NC}"
else
    echo -e "${GREEN}Storage directory exists: $STORAGE_PATH${NC}"
    # Verify it's writable
    if [ ! -w "$STORAGE_PATH" ]; then
        echo -e "${YELLOW}Storage directory not writable. Fixing permissions...${NC}"
        sudo chmod 755 "$STORAGE_PATH"
    fi
fi

# Check environment variables
echo -e "${GREEN}Checking environment variables...${NC}"
if [ ! -f ".env" ]; then
    echo -e "${RED}ERROR: .env file not found!${NC}"
    echo "Please create .env file from env.hetzner.example"
    exit 1
fi

# Validate critical environment variables
source .env
REQUIRED_VARS=("SUPABASE_URL" "SUPABASE_SERVICE_KEY" "CLERK_SECRET_KEY" "CLERK_WEBHOOK_SECRET" "ENCRYPTION_KEY")
MISSING_VARS=()

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -ne 0 ]; then
    echo -e "${RED}ERROR: Missing required environment variables:${NC}"
    printf '%s\n' "${MISSING_VARS[@]}"
    exit 1
fi

# Nginx Configuration
echo -e "${GREEN}Updating Nginx configuration...${NC}"
if [ -f "nginx-trudy-sendorahq.conf" ]; then
    echo -e "${GREEN}Copying Nginx config...${NC}"
    sudo cp nginx-trudy-sendorahq.conf /etc/nginx/sites-available/trudy-backend
    
    # Ensure symlink exists
    if [ ! -L "/etc/nginx/sites-enabled/trudy-backend" ]; then
        echo -e "${GREEN}Creating symlink...${NC}"
        sudo ln -s /etc/nginx/sites-available/trudy-backend /etc/nginx/sites-enabled/
    fi
    
    # Test and reload Nginx
    echo -e "${GREEN}Testing Nginx config...${NC}"
    NGINX_TEST_OUTPUT=$(sudo nginx -t 2>&1)
    NGINX_TEST_EXIT=$?
    
    if [ $NGINX_TEST_EXIT -eq 0 ]; then
        # Check for warnings about conflicting server names
        if echo "$NGINX_TEST_OUTPUT" | grep -q "conflicting server name"; then
            echo -e "${YELLOW}⚠️  Warning: Conflicting server names detected${NC}"
            echo -e "${YELLOW}This usually means duplicate server blocks exist.${NC}"
            echo -e "${YELLOW}Checking for duplicate configs...${NC}"
            
            # List all enabled configs
            echo -e "${YELLOW}Enabled Nginx configs:${NC}"
            ls -la /etc/nginx/sites-enabled/ || true
            
            # Check if there are multiple configs with same server_name
            echo -e "${YELLOW}Checking for duplicate server_name definitions...${NC}"
            grep -r "server_name.*truedy.sendorahq.com" /etc/nginx/sites-enabled/ 2>/dev/null || true
        fi
        
        echo -e "${GREEN}Reloading Nginx...${NC}"
        sudo systemctl reload nginx
        echo -e "${GREEN}Nginx updated and reloaded${NC}"
    else
        echo -e "${RED}ERROR: Nginx config test failed!${NC}"
        echo "$NGINX_TEST_OUTPUT"
        # Don't exit, try to continue deployment of backend code at least
    fi
else
    echo -e "${YELLOW}nginx-trudy-sendorahq.conf not found, skipping Nginx update...${NC}"
fi

# Restart service (adjust based on your service manager)
echo -e "${GREEN}Restarting service...${NC}"
SERVICE_NAME=""
if systemctl list-units --type=service --all | grep -q "trudy-backend"; then
    SERVICE_NAME="trudy-backend"
    sudo systemctl restart trudy-backend
    echo -e "${GREEN}Service 'trudy-backend' restarted${NC}"
elif systemctl list-units --type=service --all | grep -q "uvicorn"; then
    SERVICE_NAME="uvicorn"
    sudo systemctl restart uvicorn
    echo -e "${GREEN}Service 'uvicorn' restarted${NC}"
else
    echo -e "${YELLOW}No systemd service found. Please restart manually.${NC}"
    SERVICE_NAME="unknown"
fi

# Wait for service to start and check status
if [ "$SERVICE_NAME" != "unknown" ]; then
    echo -e "${GREEN}Waiting for service to start...${NC}"
    sleep 3
    
    # Check service status
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${GREEN}✅ Service '$SERVICE_NAME' is active${NC}"
    else
        echo -e "${YELLOW}⚠️  Service '$SERVICE_NAME' is not active. Checking status...${NC}"
        sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
    fi
fi

# Health check with retries
echo -e "${GREEN}Performing health check...${NC}"
HEALTH_URL="${FILE_SERVER_URL:-http://localhost:8000}/internal/health"
MAX_RETRIES=6
RETRY_DELAY=5
HEALTH_CHECK_PASSED=false

for i in $(seq 1 $MAX_RETRIES); do
    echo -e "${YELLOW}Health check attempt $i/$MAX_RETRIES...${NC}"
    
    # Try health check (try both internal/health and /health endpoints)
    if curl -f -s "$HEALTH_URL" > /dev/null 2>&1 || \
       curl -f -s http://localhost:8000/internal/health > /dev/null 2>&1 || \
       curl -f -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Health check passed!${NC}"
        HEALTH_CHECK_PASSED=true
        break
    else
        if [ $i -lt $MAX_RETRIES ]; then
            echo -e "${YELLOW}Health check failed, retrying in ${RETRY_DELAY} seconds...${NC}"
            sleep $RETRY_DELAY
        fi
    fi
done

if [ "$HEALTH_CHECK_PASSED" = false ]; then
    echo -e "${RED}❌ Health check failed after $MAX_RETRIES attempts!${NC}"
    echo ""
    echo -e "${YELLOW}Debugging information:${NC}"
    
    # Check service status
    if [ "$SERVICE_NAME" != "unknown" ]; then
        echo -e "${YELLOW}Service status:${NC}"
        sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
        echo ""
    fi
    
    # Check if port is listening
    echo -e "${YELLOW}Checking if port 8000 is listening:${NC}"
    netstat -tlnp 2>/dev/null | grep :8000 || ss -tlnp 2>/dev/null | grep :8000 || echo "Port 8000 not found in listening ports"
    echo ""
    
    # Try to get more details from health endpoints
    echo -e "${YELLOW}Attempting to get health endpoint responses:${NC}"
    echo -e "${YELLOW}Trying /internal/health:${NC}"
    curl -v http://localhost:8000/internal/health 2>&1 | head -20 || true
    echo ""
    echo -e "${YELLOW}Trying /health:${NC}"
    curl -v http://localhost:8000/health 2>&1 | head -20 || true
    echo ""
    
    # Show recent logs
    if [ "$SERVICE_NAME" != "unknown" ]; then
        echo -e "${YELLOW}Recent service logs:${NC}"
        sudo journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
    else
        echo -e "${YELLOW}Recent system logs (looking for Python/FastAPI):${NC}"
        sudo journalctl -n 50 --no-pager | grep -i "python\|fastapi\|uvicorn\|trudy" || true
    fi
    
    echo ""
    echo -e "${RED}Please check the logs above to diagnose the issue.${NC}"
    echo "Check logs: sudo journalctl -u $SERVICE_NAME -n 100"
    echo "Service status: sudo systemctl status $SERVICE_NAME"
    exit 1
fi

# CORS verification
echo -e "${GREEN}Verifying CORS configuration...${NC}"
if [ -f "verify-cors.sh" ]; then
    chmod +x verify-cors.sh
    bash verify-cors.sh || echo -e "${YELLOW}⚠️  CORS verification had warnings (non-fatal)${NC}"
else
    echo -e "${YELLOW}CORS verification script not found, skipping...${NC}"
fi

# Log CORS configuration
echo -e "${GREEN}Checking CORS configuration in logs...${NC}"
sleep 2
if journalctl -u trudy-backend -n 20 --no-pager | grep -q "CORS Exact Origins"; then
    echo -e "${GREEN}✅ CORS configuration loaded${NC}"
    journalctl -u trudy-backend -n 5 --no-pager | grep "CORS" || true
else
    echo -e "${YELLOW}⚠️  CORS configuration not found in logs (may need to check manually)${NC}"
fi

echo -e "${GREEN}✅ Deployment completed successfully!${NC}"

