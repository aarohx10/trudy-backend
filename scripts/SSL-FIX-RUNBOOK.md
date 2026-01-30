# Fix SSL for truedy.closi.tech (run on server)

I can't SSH into your server from here. Run these steps **on your machine**; you'll SSH in and run the fix script on the server.

---

## Option A: Copy script to server, then run (recommended)

**1. From your Windows machine** (PowerShell or Git Bash), copy the script and Nginx config to the server. Replace `YOUR_SERVER_IP` with your Hetzner server IP (e.g. `5.78.66.173`):

```powershell
scp "d:\Users\Admin\Downloads\Truedy Main\z-backend\scripts\server-fix-ssl.sh" root@YOUR_SERVER_IP:/tmp/
scp "d:\Users\Admin\Downloads\Truedy Main\z-backend\nginx-trudy-backend.conf" root@YOUR_SERVER_IP:/tmp/
```

**2. SSH into the server:**

```powershell
ssh root@YOUR_SERVER_IP
```

**3. On the server**, install the full Nginx config (with CORS etc.), then run the SSL fix:

```bash
cp /tmp/nginx-trudy-backend.conf /etc/nginx/sites-available/truedy-backend
ln -sf /etc/nginx/sites-available/truedy-backend /etc/nginx/sites-enabled/
chmod +x /tmp/server-fix-ssl.sh
sudo bash /tmp/server-fix-ssl.sh
```

**4. Optional – set email for Let's Encrypt expiry notices:**

```bash
export CERTBOT_EMAIL=your@email.com
sudo -E bash /tmp/server-fix-ssl.sh
```

---

## Option B: One-liner (paste script from repo)

**1. SSH in:**

```powershell
ssh root@YOUR_SERVER_IP
```

**2. On the server**, download and run (replace `RAW_GITHUB_URL` if you have the script in a repo, or skip and use Option A):

```bash
# If you don't use Option A, create the script manually:
cat > /tmp/server-fix-ssl.sh << 'SCRIPT_END'
# ... paste contents of z-backend/scripts/server-fix-ssl.sh here ...
SCRIPT_END
chmod +x /tmp/server-fix-ssl.sh
sudo bash /tmp/server-fix-ssl.sh
```

Easier: use Option A and `scp` the script from your PC.

---

## After the script runs

- Open **https://truedy.closi.tech/health** in your browser. You should see a successful response (e.g. 200) and no SSL error.
- If it still fails:
  - **DNS:** On your PC run `nslookup truedy.closi.tech` and confirm the IP is your Hetzner server.
  - **Firewall:** On the server run `sudo ufw status` and ensure ports 80 and 443 are allowed (`sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw reload`).

---

## If certbot fails (e.g. "port 80 already in use")

- Ensure Nginx is running and listening on 80: `sudo ss -tlnp | grep :80`
- Stop any other service using 80, then run the script again.
- If DNS for `truedy.closi.tech` does not yet point to this server, fix the A record first and wait a few minutes before re-running certbot.
