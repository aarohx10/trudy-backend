#!/bin/bash
# Sync and Deploy Helper Script for Backend (Linux/Mac)
# This script pushes changes to GitHub and then triggers the deployment on the server

# Use provided commit message or generic timestamp
COMMIT_MSG=${1:-"Update: $(date +'%Y-%m-%d %H:%M:%S')"}

echo "🚀 Syncing and Deploying Backend..."

# 1. Push to GitHub
echo "📦 Pushing changes to GitHub..."
git add .
git commit -m "$COMMIT_MSG"
git push

# 2. Trigger remote deployment
echo "🌍 Triggering remote deployment on Hetzner..."
ssh root@hetzner-truedy "cd /opt/backend && git pull && bash deploy.sh"

echo "✅ Backend updated and deployed!"
