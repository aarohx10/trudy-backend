# PowerShell script to push changes and trigger deployment on the server
# Usage: .\sync-server.ps1 "Your commit message"

$commitMsg = if ($args[0]) { $args[0] } else { "Update backend" }

Write-Host "🚀 Syncing changes to GitHub..." -ForegroundColor Yellow

# 1. Add all changes in the backend
git add .

# 2. Commit
git commit -m $commitMsg

# 3. Push to master
git push origin master

Write-Host "✅ Code pushed to GitHub" -ForegroundColor Green
Write-Host "🔄 Triggering deployment on Hetzner VPS..." -ForegroundColor Yellow

# 4. SSH into server and run deploy.sh
ssh root@hetzner-truedy "cd /opt/backend && git clean -fd && git fetch origin master && git reset --hard origin/master && bash deploy.sh"

Write-Host "✨ All done! Your backend is updated and live at https://truedy.closi.tech" -ForegroundColor Green
