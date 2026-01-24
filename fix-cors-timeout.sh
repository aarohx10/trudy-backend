#!/bin/bash
# Simple one-command fix for CORS and timeout issues
set -e

echo "🔧 Fixing nginx timeout and CORS..."

# Find nginx config
CONFIG="/etc/nginx/sites-available/trudy-backend"
[ ! -f "$CONFIG" ] && CONFIG="/etc/nginx/sites-enabled/trudy-backend"
[ ! -f "$CONFIG" ] && CONFIG=$(find /etc/nginx -name "*trudy*" -o -name "*backend*" 2>/dev/null | head -1)

if [ ! -f "$CONFIG" ]; then
    echo "❌ Nginx config not found. Creating basic config..."
    CONFIG="/etc/nginx/sites-available/trudy-backend"
    cat > "$CONFIG" << 'EOF'
server {
    listen 80;
    server_name truedy.closi.tech;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Credentials "true" always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD" always;
        add_header Access-Control-Allow-Headers "*" always;
        add_header Access-Control-Max-Age "86400" always;
        
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin" always;
            add_header Access-Control-Allow-Credentials "true" always;
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD" always;
            add_header Access-Control-Allow-Headers "*" always;
            add_header Access-Control-Max-Age "86400" always;
            add_header Content-Length 0;
            add_header Content-Type text/plain;
            return 204;
        }
    }
}
EOF
    ln -sf "$CONFIG" /etc/nginx/sites-enabled/trudy-backend 2>/dev/null || true
else
    # Backup
    cp "$CONFIG" "${CONFIG}.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Add timeouts if missing
    grep -q "proxy_read_timeout" "$CONFIG" || sed -i '/proxy_cache_bypass/a\        proxy_read_timeout 300s;\n        proxy_connect_timeout 300s;\n        proxy_send_timeout 300s;' "$CONFIG"
    
    # Add CORS if missing
    grep -q "Access-Control-Allow-Origin" "$CONFIG" || sed -i '/proxy_cache_bypass/a\        add_header Access-Control-Allow-Origin "$http_origin" always;\n        add_header Access-Control-Allow-Credentials "true" always;\n        add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD" always;\n        add_header Access-Control-Allow-Headers "*" always;\n        add_header Access-Control-Max-Age "86400" always;' "$CONFIG"
fi

# Test and reload
nginx -t && systemctl reload nginx && echo "✅ Nginx fixed!" || echo "❌ Nginx config error"
