#!/usr/bin/env bash
# Setup truedy.sendorahq.com on Hetzner: SSL cert + Nginx.
# Run on server as root. DNS A record for truedy.sendorahq.com must point to this server.
# Usage: sudo bash setup-sendorahq-domain.sh

set -e
DOMAIN="truedy.sendorahq.com"
SITES_AVAILABLE="/etc/nginx/sites-available"
SITES_ENABLED="/etc/nginx/sites-enabled"
CONFIG_NAME="trudy-sendorahq"

echo "=== Setting up $DOMAIN ==="

# 1) Minimal HTTP-only block so certbot can run (cert paths don't exist yet)
echo "Creating temporary HTTP-only block for certbot..."
cat > "$SITES_AVAILABLE/$CONFIG_NAME" << 'NGINX_HTTP'
server {
    listen 80;
    server_name truedy.sendorahq.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX_HTTP

[ -L "$SITES_ENABLED/$CONFIG_NAME" ] || ln -sf "$SITES_AVAILABLE/$CONFIG_NAME" "$SITES_ENABLED/$CONFIG_NAME"
nginx -t && systemctl reload nginx
echo "Nginx reloaded (HTTP only)."

# 2) Get SSL cert (non-interactive: set CERTBOT_EMAIL or use -m)
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@sendorahq.com}"
if certbot certificates 2>/dev/null | grep -q "Certificate Name: $DOMAIN"; then
    echo "Certificate for $DOMAIN already exists."
else
    echo "Running certbot for $DOMAIN..."
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" || true
fi

# 3) Deploy full config (SSL + CORS + error pages)
# Script is expected to run from repo root or z-backend; find nginx-trudy-sendorahq.conf
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CONF="$SCRIPT_DIR/../nginx-trudy-sendorahq.conf"
if [ ! -f "$REPO_CONF" ]; then
    REPO_CONF="$(dirname "$SCRIPT_DIR")/nginx-trudy-sendorahq.conf"
fi
if [ ! -f "$REPO_CONF" ]; then
    echo "Warning: nginx-trudy-sendorahq.conf not found. Using existing cert with minimal config."
else
    cp "$REPO_CONF" "$SITES_AVAILABLE/$CONFIG_NAME"
    nginx -t && systemctl reload nginx
    echo "Full Nginx config (SSL + CORS) deployed and reloaded."
fi

# 4) Test
echo "Testing https://$DOMAIN/health ..."
HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" "https://$DOMAIN/health" --max-time 10 || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "SUCCESS: https://$DOMAIN/health returned 200."
else
    echo "WARNING: https://$DOMAIN/health returned $HTTP_CODE (check backend on :8000 and firewall)."
fi

echo "=== Done. Set NEXT_PUBLIC_API_URL=https://$DOMAIN/api/v1 in frontend (and WEBHOOK_BASE_URL on server if needed). ==="
