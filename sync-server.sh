#!/bin/bash
# Local script to push changes and trigger deployment on the server
# Usage: ./sync-server.sh "Your commit message"

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

COMMIT_MSG=${1:-"Update backend"}

echo -e "${YELLOW}🚀 Syncing changes to GitHub...${NC}"

# 1. Add all changes in the backend
git add .

# 2. Commit
git commit -m "$COMMIT_MSG" || echo "No changes to commit"

# 3. Push to master (backend-only repo)
git push origin master

echo -e "${GREEN}✅ Code pushed to GitHub${NC}"
echo -e "${YELLOW}🔄 Triggering deployment on Hetzner VPS...${NC}"

# 4. SSH into server and run deploy.sh
ssh root@hetzner-truedy "cd /opt/backend && git pull origin master && bash deploy.sh"

echo -e "${GREEN}✨ All done! Your backend is updated and live at https://truedy.closi.tech${NC}"
