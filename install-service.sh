#!/bin/bash
# Install systemd service for Trudy Backend

set -e

SERVICE_NAME="trudy-backend"
SERVICE_FILE="trudy-backend.service"
INSTALL_PATH="/etc/systemd/system/${SERVICE_FILE}"
DEPLOY_PATH="/opt/trudy-backend"

echo "Installing Trudy Backend systemd service..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
    echo "Error: $SERVICE_FILE not found in current directory"
    exit 1
fi

# Copy service file
echo "Copying service file to $INSTALL_PATH..."
cp "$SERVICE_FILE" "$INSTALL_PATH"

# Set proper permissions
chmod 644 "$INSTALL_PATH"

# Reload systemd daemon
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable service to start on boot
echo "Enabling service to start on boot..."
systemctl enable "$SERVICE_NAME"

# Check if service is already running
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Service is already running. Restarting..."
    systemctl restart "$SERVICE_NAME"
else
    echo "Starting service..."
    systemctl start "$SERVICE_NAME"
fi

# Show status
echo ""
echo "Service installed and started successfully!"
echo "Status:"
systemctl status "$SERVICE_NAME" --no-pager -l

echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE_NAME    # Check status"
echo "  sudo systemctl restart $SERVICE_NAME   # Restart service"
echo "  sudo systemctl stop $SERVICE_NAME       # Stop service"
echo "  sudo journalctl -u $SERVICE_NAME -f    # View logs (follow mode)"
