# truedy.sendorahq.com – Backend domain setup

Backend API is served at **https://truedy.sendorahq.com**. DNS A record should point to your Hetzner server.

## 1. On the server (Hetzner)

From the repo on the server (e.g. after `git pull` or syncing `z-backend/`):

```bash
cd /path/to/Truedy\ Main/z-backend
sudo bash scripts/setup-sendorahq-domain.sh
```

Optional: set email for Let’s Encrypt:

```bash
export CERTBOT_EMAIL=your@email.com
sudo -E bash scripts/setup-sendorahq-domain.sh
```

The script will:

- Add a temporary HTTP-only Nginx block for `truedy.sendorahq.com`
- Run `certbot --nginx -d truedy.sendorahq.com` to obtain the SSL cert
- Deploy the full Nginx config (`nginx-trudy-sendorahq.conf`) with SSL and CORS
- Reload Nginx and test `https://truedy.sendorahq.com/health`

## 2. Backend env (optional)

On the server, if you use webhooks or file URLs, set:

```bash
WEBHOOK_BASE_URL=https://truedy.sendorahq.com
FILE_SERVER_URL=https://truedy.sendorahq.com
```

(Defaults in code already use `https://truedy.sendorahq.com` where needed.)

## 3. Frontend

- **Default:** The app uses `https://truedy.sendorahq.com/api/v1` in production.
- **Override:** In Vercel (or `.env.production`), set:
  - `NEXT_PUBLIC_API_URL=https://truedy.sendorahq.com/api/v1`

Redeploy the frontend after changing env.

## 4. Verify

- Open: https://truedy.sendorahq.com/health  
- From repo: `curl -sI https://truedy.sendorahq.com/health`  
- CORS: `bash z-backend/verify-cors.sh` (edit `BACKEND_URL` to `https://truedy.sendorahq.com` if testing remotely).
