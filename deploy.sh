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
if [ -f "nginx-trudy-backend.conf" ]; then
    echo -e "${GREEN}Copying Nginx config...${NC}"
    sudo cp nginx-trudy-backend.conf /etc/nginx/sites-available/trudy-backend
    
    # Ensure symlink exists
    if [ ! -L "/etc/nginx/sites-enabled/trudy-backend" ]; then
        echo -e "${GREEN}Creating symlink...${NC}"
        sudo ln -s /etc/nginx/sites-available/trudy-backend /etc/nginx/sites-enabled/
    fi
    
    # Test and reload Nginx
    echo -e "${GREEN}Testing Nginx config...${NC}"
    if sudo nginx -t; then
        echo -e "${GREEN}Reloading Nginx...${NC}"
        sudo systemctl reload nginx
        echo -e "${GREEN}Nginx updated and reloaded${NC}"
    else
        echo -e "${RED}ERROR: Nginx config test failed!${NC}"
        # Don't exit, try to continue deployment of backend code at least
    fi
else
    echo -e "${YELLOW}nginx-trudy-backend.conf not found, skipping Nginx update...${NC}"
fi

# Restart service (adjust based on your service manager)
echo -e "${GREEN}Restarting service...${NC}"
if systemctl is-active --quiet trudy-backend; then
    sudo systemctl restart trudy-backend
    echo -e "${GREEN}Service restarted${NC}"
elif systemctl is-active --quiet uvicorn; then
    sudo systemctl restart uvicorn
    echo -e "${GREEN}Service restarted${NC}"
else
    echo -e "${YELLOW}No systemd service found. Please restart manually.${NC}"
fi

# Health check
echo -e "${GREEN}Performing health check...${NC}"
sleep 5  # Give service more time to start
HEALTH_URL="${FILE_SERVER_URL:-http://localhost:8000}/internal/health"
if curl -f "$HEALTH_URL" > /dev/null 2>&1 || curl -f http://localhost:8000/internal/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Health check passed!${NC}"
else
    echo -e "${RED}❌ Health check failed!${NC}"
    echo "Check logs: journalctl -u trudy-backend -n 50"
    echo "Service status: systemctl status trudy-backend"
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

