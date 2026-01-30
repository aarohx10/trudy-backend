#!/bin/bash
# Fix SSL for truedy.closi.tech on Hetzner (Nginx + Let's Encrypt).
# Run ON THE SERVER as root (or with sudo):
#   sudo bash server-fix-ssl.sh
# Or after copying to server: scp z-backend/scripts/server-fix-ssl.sh root@YOUR_SERVER_IP:/tmp && ssh root@YOUR_SERVER_IP 'bash /tmp/server-fix-ssl.sh'

set -e
DOMAIN=truedy.closi.tech
NGINX_SITE=truedy-backend
SITES_AVAILABLE="/etc/nginx/sites-available"
SITES_ENABLED="/etc/nginx/sites-enabled"

echo "[1/6] Checking root..."
if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

echo "[2/6] Installing certbot + nginx plugin if needed..."
apt-get update -qq
apt-get install -y -qq certbot python3-certbot-nginx >/dev/null 2>&1 || true

echo "[3/6] Obtaining/renewing certificate for $DOMAIN..."
# Use your email for expiry notices; or set CERTBOT_EMAIL before running.
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@closi.tech}"
certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "$CERTBOT_EMAIL" --no-eff-email || {
  echo "Certbot failed. If port 80 is in use or DNS is wrong, fix that first."
  exit 1
}

echo "[4/6] Checking certificate files..."
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
for f in "$CERT_DIR/fullchain.pem" "$CERT_DIR/privkey.pem"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing: $f"
    exit 1
  fi
done
echo "  Cert OK: $CERT_DIR"

echo "[5/6] Ensuring Nginx SSL config and reloading..."
# If you deploy nginx config from repo, copy it; otherwise ensure default exists.
if [[ -f "$SITES_AVAILABLE/$NGINX_SITE" ]]; then
  nginx -t && systemctl reload nginx
else
  # Minimal SSL server block if no site file yet
  mkdir -p /etc/letsencrypt
  [[ -f /etc/letsencrypt/options-ssl-nginx.conf ]] || true
  [[ -f /etc/letsencrypt/ssl-dhparams.pem ]] || openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048 2>/dev/null || true
  cat > "$SITES_AVAILABLE/$NGINX_SITE" << 'NGINX_SSL'
server { listen 80; server_name truedy.closi.tech; return 301 https://$host$request_uri; }
server {
  listen 443 ssl;
  server_name truedy.closi.tech;
  ssl_certificate /etc/letsencrypt/live/truedy.closi.tech/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/truedy.closi.tech/privkey.pem;
  include /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
  location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; }
}
NGINX_SSL
  ln -sf "$SITES_AVAILABLE/$NGINX_SITE" "$SITES_ENABLED/" 2>/dev/null || true
  nginx -t && systemctl reload nginx
fi
echo "  Nginx reloaded."

echo "[6/6] Testing https://$DOMAIN/health..."
curl -sSI "https://$DOMAIN/health" --connect-timeout 5 -o /dev/null -w "  HTTP %{http_code}\n" || echo "  Curl failed (check DNS or try from your PC)."

echo "Done. Open https://$DOMAIN/health in your browser to confirm."
