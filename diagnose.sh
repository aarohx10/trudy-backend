#!/bin/bash
# Diagnosis script to check Nginx and Service state

echo "=========================================="
echo "DIAGNOSIS START: $(date)"
echo "=========================================="

echo ""
echo "--- 1. Checking Listening Ports ---"
netstat -tulpn | grep -E ':(80|443|8000)' || echo "No services found on 80, 443, or 8000"

echo ""
echo "--- 2. Checking Nginx Status ---"
systemctl status nginx --no-pager

echo ""
echo "--- 3. Nginx Configuration Dump ---"
# Check sites-enabled
ls -la /etc/nginx/sites-enabled/
echo "--- Content of trudy-backend (if exists) ---"
if [ -f /etc/nginx/sites-enabled/trudy-backend ]; then
    cat /etc/nginx/sites-enabled/trudy-backend
elif [ -f /etc/nginx/sites-enabled/default ]; then
    echo "WARNING: trudy-backend config not found. Showing default:"
    cat /etc/nginx/sites-enabled/default
else
    echo "No config found in sites-enabled"
fi

echo ""
echo "--- 4. Checking SSL Certificates ---"
if [ -d "/etc/letsencrypt/live" ]; then
    ls -R /etc/letsencrypt/live
else
    echo "No Let's Encrypt directory found"
fi

echo ""
echo "--- 5. Checking Backend Service Status ---"
systemctl status trudy-backend --no-pager
systemctl status uvicorn --no-pager || true

echo ""
echo "--- 6. Checking Backend Logs (Last 20 lines) ---"
journalctl -u trudy-backend -n 20 --no-pager

echo ""
echo "=========================================="
echo "DIAGNOSIS END"
echo "=========================================="
