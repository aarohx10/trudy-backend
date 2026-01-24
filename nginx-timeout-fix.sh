#!/bin/bash
# Script to fix nginx timeout and CORS issues on Hetzner server
# Run this on the server: ssh root@hetzner-truedy "bash -s" < nginx-timeout-fix.sh

echo "🔧 Fixing nginx timeout and CORS configuration..."

# Find the nginx config file (could be in different locations)
NGINX_CONFIG="/etc/nginx/sites-available/trudy-backend"
if [ ! -f "$NGINX_CONFIG" ]; then
    # Try to find it
    NGINX_CONFIG=$(find /etc/nginx -name "*trudy*" -o -name "*backend*" 2>/dev/null | head -1)
    if [ -z "$NGINX_CONFIG" ]; then
        echo "❌ Could not find nginx config. Please provide the path manually."
        exit 1
    fi
fi

echo "📝 Found nginx config: $NGINX_CONFIG"

# Backup the original config
cp "$NGINX_CONFIG" "${NGINX_CONFIG}.backup.$(date +%Y%m%d_%H%M%S)"
echo "✅ Backup created"

# Check if timeout settings already exist
if grep -q "proxy_read_timeout" "$NGINX_CONFIG"; then
    echo "⚠️  proxy_read_timeout already exists, updating..."
    sed -i 's/proxy_read_timeout.*/proxy_read_timeout 300s;/' "$NGINX_CONFIG"
else
    # Add timeout settings to the location block
    sed -i '/location \/ {/a\        proxy_read_timeout 300s;\n        proxy_connect_timeout 300s;\n        proxy_send_timeout 300s;' "$NGINX_CONFIG"
fi

# Add CORS headers to nginx (as backup in case gateway times out)
if ! grep -q "add_header.*Access-Control-Allow-Origin" "$NGINX_CONFIG"; then
    echo "📝 Adding CORS headers to nginx config..."
    # Add CORS headers after the proxy_pass line
    sed -i '/proxy_cache_bypass/a\        # CORS headers (backup for gateway timeouts)\n        add_header Access-Control-Allow-Origin "$http_origin" always;\n        add_header Access-Control-Allow-Credentials "true" always;\n        add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD" always;\n        add_header Access-Control-Allow-Headers "*" always;\n        add_header Access-Control-Max-Age "86400" always;\n        \n        # Handle OPTIONS preflight\n        if ($request_method = OPTIONS) {\n            add_header Access-Control-Allow-Origin "$http_origin" always;\n            add_header Access-Control-Allow-Credentials "true" always;\n            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD" always;\n            add_header Access-Control-Allow-Headers "*" always;\n            add_header Access-Control-Max-Age "86400" always;\n            add_header Content-Length 0;\n            add_header Content-Type text/plain;\n            return 204;\n        }' "$NGINX_CONFIG"
fi

# Test nginx configuration
echo "🧪 Testing nginx configuration..."
if nginx -t; then
    echo "✅ Nginx config is valid"
    echo "🔄 Reloading nginx..."
    systemctl reload nginx
    echo "✅ Nginx reloaded successfully!"
    echo ""
    echo "📋 Summary of changes:"
    echo "  - Increased proxy_read_timeout to 300s (5 minutes)"
    echo "  - Added CORS headers to nginx (backup)"
    echo "  - Added OPTIONS preflight handler"
else
    echo "❌ Nginx config test failed! Restoring backup..."
    cp "${NGINX_CONFIG}.backup.$(date +%Y%m%d_%H%M%S)" "$NGINX_CONFIG"
    exit 1
fi

echo ""
echo "✨ Done! The timeout and CORS issues should now be fixed."
