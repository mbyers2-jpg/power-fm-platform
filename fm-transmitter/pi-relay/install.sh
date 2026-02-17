#!/bin/bash
# Power FM Pi Relay — Bootstrap installer for Raspberry Pi
# Run on a fresh Raspberry Pi:
#   curl -sSL <url>/install.sh | bash
# Or locally:
#   chmod +x install.sh && ./install.sh
#
# Prerequisites: Raspberry Pi OS (Lite or Full), internet connection

set -e

RELAY_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="power-fm-relay"

echo "=== Power FM Pi Relay — Installer ==="
echo ""
echo "Relay directory: $RELAY_DIR"
echo ""

# --- System packages ---
echo "Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y \
    python3 python3-venv python3-pip \
    ffmpeg \
    git

# --- Python virtual environment ---
echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "$RELAY_DIR/venv" ]; then
    python3 -m venv "$RELAY_DIR/venv"
fi

"$RELAY_DIR/venv/bin/pip" install --upgrade pip
"$RELAY_DIR/venv/bin/pip" install requests psutil

# --- Create log directory ---
mkdir -p "$RELAY_DIR/logs"

# --- rpitx (optional — for GPIO FM transmission) ---
echo ""
read -p "Install rpitx for GPIO FM transmission? (y/N): " INSTALL_RPITX
if [ "$INSTALL_RPITX" = "y" ] || [ "$INSTALL_RPITX" = "Y" ]; then
    echo "Installing rpitx..."
    if [ ! -d "/opt/rpitx" ]; then
        cd /opt
        sudo git clone https://github.com/F5OEO/rpitx.git
        cd rpitx
        sudo ./install.sh
    else
        echo "rpitx already installed at /opt/rpitx"
    fi
    cd "$RELAY_DIR"
fi

# --- Config check ---
if [ ! -f "$RELAY_DIR/config.json" ]; then
    echo ""
    echo "WARNING: No config.json found!"
    echo "Copy and edit the template:"
    echo "  cp config.json.example config.json"
    echo "  nano config.json"
    echo ""
    echo "Required fields: node_id, stream_url, fm_frequency, hub_url"
fi

# --- systemd service ---
echo ""
echo "Installing systemd service..."
sudo cp "$RELAY_DIR/power-fm-relay.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Commands:"
echo "  Start:    sudo systemctl start $SERVICE_NAME"
echo "  Stop:     sudo systemctl stop $SERVICE_NAME"
echo "  Status:   sudo systemctl status $SERVICE_NAME"
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  Edit:     nano $RELAY_DIR/config.json"
echo ""
echo "To start now:"
echo "  sudo systemctl start $SERVICE_NAME"
