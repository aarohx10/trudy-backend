# PowerShell script to push changes and trigger deployment on the server
# Usage: .\sync-server.ps1 "Your commit message"

$commitMsg = if ($args[0]) { $args[0] } else { "Update backend" }

Write-Host "Syncing changes to GitHub..." -ForegroundColor Yellow

# 1. Add all changes in the backend
git add .

# 2. Commit
git commit -m $commitMsg

# 3. Push to master
git push origin master

Write-Host "Code pushed to GitHub" -ForegroundColor Green
Write-Host "Triggering deployment on Hetzner VPS..." -ForegroundColor Yellow

# 4. SSH into server and run deploy.sh (with output capture)
Write-Host "Connecting to server and running deployment..." -ForegroundColor Yellow
Write-Host ""

# Run deployment and capture output
# Use semicolons instead of && for PowerShell compatibility (bash will still execute them sequentially)
$deployCommand = 'cd /opt/backend; git clean -fd; git fetch origin master; git reset --hard origin/master; bash deploy.sh 2>&1'
$deployOutput = ssh root@hetzner-truedy $deployCommand

# Check exit code
if ($LASTEXITCODE -eq 0) {
    Write-Host "Deployment script completed!" -ForegroundColor Green
    Write-Host ""
    
    # Show deployment output summary
    Write-Host "Deployment Output Summary:" -ForegroundColor Cyan
    $deployOutput | Select-String -Pattern "ERROR|FAILED|completed successfully" | Select-Object -Last 10
    
    Write-Host ""
    Write-Host "Running post-deployment verification..." -ForegroundColor Yellow
    
    # Wait a moment for service to stabilize
    Start-Sleep -Seconds 5
    
    # 1. Health Check
    Write-Host "1. Testing health endpoint..." -ForegroundColor Cyan
    $healthCheck = try {
        $response = Invoke-WebRequest -Uri "https://truedy.closi.tech/health" -Method GET -TimeoutSec 15 -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Host "   Health check passed (HTTP 200)" -ForegroundColor Green
            
            # Parse health status
            $healthJson = $response.Content | ConvertFrom-Json
            if ($healthJson.status) {
                Write-Host "   Status: $($healthJson.status)" -ForegroundColor Gray
            }
            $true
        } else {
            Write-Host "   Health check returned HTTP $($response.StatusCode)" -ForegroundColor Yellow
            $false
        }
    } catch {
        Write-Host "   Health check failed: $($_.Exception.Message)" -ForegroundColor Red
        $false
    }
    
    # 2. Service Status Check
    Write-Host "2. Checking service status..." -ForegroundColor Cyan
    $serviceStatusCmd = 'systemctl is-active trudy-backend 2>&1'
    $serviceStatus = ssh root@hetzner-truedy $serviceStatusCmd
    if ($serviceStatus -eq "active") {
        Write-Host "   Service is active" -ForegroundColor Green
    } else {
        Write-Host "   Service status: $serviceStatus" -ForegroundColor Yellow
    }
    
    # 3. Recent Errors Check
    Write-Host "3. Checking for recent errors..." -ForegroundColor Cyan
    $errorsCmd = "journalctl -u trudy-backend -n 50 --no-pager | grep -iE 'error|exception|traceback|failed' | tail -5"
    $recentErrors = ssh root@hetzner-truedy $errorsCmd 2>&1
    if ($recentErrors -and $recentErrors -notmatch "No entries") {
        Write-Host "   Found recent errors in logs:" -ForegroundColor Yellow
        $recentErrors | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
    } else {
        Write-Host "   No recent errors found" -ForegroundColor Green
    }
    
    # 4. Port Check
    Write-Host "4. Verifying port 8000..." -ForegroundColor Cyan
    $portCmd = "ss -tlnp | grep :8000 || netstat -tlnp | grep :8000"
    $portCheck = ssh root@hetzner-truedy $portCmd 2>&1
    if ($portCheck) {
        Write-Host "   Port 8000 is listening" -ForegroundColor Green
    } else {
        Write-Host "   Port 8000 check inconclusive" -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "Deployment Summary" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Gray
    
    if ($healthCheck) {
        Write-Host "Deployment Status: SUCCESS" -ForegroundColor Green
        Write-Host ""
        Write-Host "Your backend is updated and live at:" -ForegroundColor Green
        Write-Host "   https://truedy.closi.tech" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Next steps:" -ForegroundColor Yellow
        Write-Host "   - Test API endpoints with your application" -ForegroundColor Gray
        Write-Host "   - Verify organization isolation works correctly" -ForegroundColor Gray
        Write-Host "   - Check database migrations if needed" -ForegroundColor Gray
    } else {
        Write-Host "Deployment Status: COMPLETED WITH WARNINGS" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "The deployment script completed, but health check failed." -ForegroundColor Yellow
        Write-Host "This may be normal if the service is still starting." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Check manually:" -ForegroundColor Cyan
        $manualCmd1 = "ssh root@hetzner-truedy `"systemctl status trudy-backend`""
        Write-Host "   $manualCmd1" -ForegroundColor Gray
        $manualCmd2 = "ssh root@hetzner-truedy `"journalctl -u trudy-backend -n 100`""
        Write-Host "   $manualCmd2" -ForegroundColor Gray
    }
    
    Write-Host ""
} else {
    Write-Host "Deployment FAILED!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Deployment output:" -ForegroundColor Yellow
    $deployOutput | Select-Object -Last 30
    
    Write-Host ""
    Write-Host "Debugging steps:" -ForegroundColor Cyan
    Write-Host "1. Check service status:" -ForegroundColor Gray
    $cmd1 = "ssh root@hetzner-truedy `"systemctl status trudy-backend`""
    Write-Host "   $cmd1" -ForegroundColor White
    Write-Host ""
    Write-Host "2. Check recent logs:" -ForegroundColor Gray
    $cmd2 = "ssh root@hetzner-truedy `"journalctl -u trudy-backend -n 100`""
    Write-Host "   $cmd2" -ForegroundColor White
    Write-Host ""
    Write-Host "3. Check deployment script:" -ForegroundColor Gray
    $cmd3 = "ssh root@hetzner-truedy `"cd /opt/backend; bash deploy.sh`""
    Write-Host "   $cmd3" -ForegroundColor White
    
    exit 1
}
