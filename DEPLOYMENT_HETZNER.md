# Hetzner VPS Deployment Guide

This guide provides step-by-step instructions for deploying the Trudy Backend to a Hetzner VPS.

## Prerequisites

- Hetzner VPS instance (recommended: 4GB RAM, 2 vCPUs minimum)
- Root or sudo access to the server
- Domain name configured to point to your Hetzner server IP
- SSL certificate (Let's Encrypt recommended)
- Git installed on the server

## Step 1: Initial Server Setup

### 1.1 Update System

```bash
sudo apt update && sudo apt upgrade -y
```

### 1.2 Install Required Software

```bash
# Python 3.11+
sudo apt install -y python3 python3-pip python3-venv git curl

# Nginx (for reverse proxy)
sudo apt install -y nginx

# Certbot (for SSL certificates)
sudo apt install -y certbot python3-certbot-nginx
```

### 1.3 Configure Firewall

```bash
# Allow SSH, HTTP, HTTPS
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## Step 2: Storage Setup

### 2.1 Mount Storage Volume

If you have a separate storage volume:

```bash
# Format volume (if new)
sudo mkfs.ext4 /dev/sdb

# Create mount point
sudo mkdir -p /mnt/storage

# Mount volume
sudo mount /dev/sdb /mnt/storage

# Add to /etc/fstab for persistent mounting
echo '/dev/sdb /mnt/storage ext4 defaults 0 2' | sudo tee -a /etc/fstab
```

### 2.2 Set Storage Permissions

```bash
sudo chown root:root /mnt/storage
sudo chmod 755 /mnt/storage
```

## Step 3: Clone and Setup Repository

### 3.1 Clone Repository

```bash
cd /opt
sudo git clone https://github.com/your-org/trudy-backend.git trudy-backend
cd trudy-backend/z-backend
```

### 3.2 Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.3 Configure Environment Variables

```bash
# Copy example environment file
cp env.hetzner.example .env

# Edit .env file with your actual values
nano .env
```

Required environment variables:
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `SUPABASE_SERVICE_KEY`
- `CLERK_SECRET_KEY`
- `CLERK_WEBHOOK_SECRET`
- `ENCRYPTION_KEY` (generate with: `python -c "import secrets; print(secrets.token_hex(32))"`)
- `ULTRAVOX_API_KEY`
- `STRIPE_SECRET_KEY`
- `FILE_STORAGE_PATH=/mnt/storage`
- `FILE_SERVER_URL=https://api.truedy.ai`

## Step 4: Install Systemd Service

### 4.1 Install Service

```bash
sudo bash install-service.sh
```

### 4.2 Verify Service Status

```bash
sudo systemctl status trudy-backend
```

## Step 5: Configure Nginx Reverse Proxy

### 5.1 Create Nginx Configuration

```bash
sudo nano /etc/nginx/sites-available/trudy-backend
```

Add the following configuration:

```nginx
server {
    listen 80;
    server_name api.truedy.ai;

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
    }
}
```

### 5.2 Enable Site

```bash
sudo ln -s /etc/nginx/sites-available/trudy-backend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 5.3 Setup SSL Certificate

```bash
sudo certbot --nginx -d api.truedy.ai
```

## Step 6: Configure GitHub Actions (Optional)

### 6.1 Add GitHub Secrets

In your GitHub repository, go to Settings → Secrets and add:

- `HETZNER_HOST`: Your server IP or domain
- `HETZNER_USER`: SSH user (usually `root`)
- `HETZNER_SSH_KEY`: Private SSH key for deployment
- `HETZNER_DEPLOY_PATH`: `/opt/trudy-backend/z-backend`
- `HETZNER_DOMAIN`: `https://api.truedy.ai` (optional)

### 6.2 Generate SSH Key for Deployment

```bash
# On your local machine
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/hetzner_deploy

# Copy public key to server
ssh-copy-id -i ~/.ssh/hetzner_deploy.pub root@your-server-ip

# Add private key to GitHub Secrets
cat ~/.ssh/hetzner_deploy
```

## Step 7: Verify Deployment

### 7.1 Check Service Status

```bash
sudo systemctl status trudy-backend
```

### 7.2 Check Health Endpoint

```bash
curl https://api.truedy.ai/internal/health
```

### 7.3 View Logs

```bash
# View recent logs
sudo journalctl -u trudy-backend -n 50

# Follow logs in real-time
sudo journalctl -u trudy-backend -f
```

## Step 8: Maintenance Commands

### 8.1 Restart Service

```bash
sudo systemctl restart trudy-backend
```

### 8.2 View Service Logs

```bash
sudo journalctl -u trudy-backend -f
```

### 8.3 Manual Deployment

```bash
cd /opt/trudy-backend/z-backend
git pull
bash deploy.sh
```

## Troubleshooting

### Service Won't Start

1. Check logs: `sudo journalctl -u trudy-backend -n 100`
2. Verify environment variables: `sudo cat /opt/trudy-backend/z-backend/.env`
3. Check storage permissions: `ls -la /mnt/storage`
4. Verify Python virtual environment: `source /opt/trudy-backend/z-backend/venv/bin/activate && python --version`

### Health Check Fails

1. Verify service is running: `sudo systemctl status trudy-backend`
2. Check if port 8000 is accessible: `curl http://localhost:8000/internal/health`
3. Check Nginx configuration: `sudo nginx -t`
4. Verify firewall rules: `sudo ufw status`

### Storage Issues

1. Verify mount: `df -h | grep storage`
2. Check permissions: `ls -la /mnt/storage`
3. Verify directory exists: `sudo mkdir -p /mnt/storage`

### SSL Certificate Issues

1. Renew certificate: `sudo certbot renew`
2. Check certificate status: `sudo certbot certificates`
3. Verify Nginx SSL config: `sudo nginx -t`

## Security Considerations

1. **Firewall**: Only allow necessary ports (22, 80, 443)
2. **SSH**: Disable password authentication, use SSH keys only
3. **Environment Variables**: Never commit `.env` file to Git
4. **Service User**: Consider running service as non-root user (requires additional configuration)
5. **Backups**: Regularly backup database and storage volume
6. **Updates**: Keep system and dependencies updated

## Backup Strategy

### Database Backup

```bash
# Backup Supabase database (use Supabase dashboard or CLI)
```

### Storage Backup

```bash
# Backup storage directory
sudo tar -czf /backup/storage-$(date +%Y%m%d).tar.gz /mnt/storage
```

## Monitoring

### System Resources

```bash
# CPU and Memory
htop

# Disk Usage
df -h
du -sh /mnt/storage
```

### Application Logs

```bash
# Service logs
sudo journalctl -u trudy-backend -f

# Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## Next Steps

- Set up monitoring (e.g., Prometheus, Grafana)
- Configure log aggregation
- Set up automated backups
- Configure alerting for service failures
