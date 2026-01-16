# Sync and Deploy Helper Script for Backend (Windows PowerShell)
# Usage: .\sync-and-deploy.ps1 "Your commit message"

$msg = if ($args[0]) { $args[0] } else { "Update: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }

Write-Host "🚀 Preparing to sync and deploy backend..." -ForegroundColor Cyan

# 1. Sync with GitHub
Write-Host "📦 Committing and pushing to GitHub..." -ForegroundColor Green
git add .
git commit -m "$msg"
git push

# 2. Trigger remote deployment
Write-Host "🌍 Triggering remote deployment on Hetzner..." -ForegroundColor Green
ssh root@hetzner-truedy "cd /opt/backend && git pull && bash deploy.sh"

Write-Host "✅ Backend update complete!" -ForegroundColor Cyan
