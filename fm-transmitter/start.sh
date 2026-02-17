#!/bin/bash
# Start the FM Transmitter fleet manager as a persistent background service
# Usage: ./start.sh

AGENT_DIR="$HOME/Agents/fm-transmitter"
PLIST_NAME="com.marcbyers.fm-transmitter.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Power FM Transmitter Fleet Manager ==="

# Create venv if needed
if [ ! -d "$AGENT_DIR/venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$AGENT_DIR/venv"
    echo "Installing dependencies..."
    "$AGENT_DIR/venv/bin/pip" install --upgrade pip
    "$AGENT_DIR/venv/bin/pip" install requests
    echo "Setup complete."
    echo ""
fi

# Ensure requests is installed
"$AGENT_DIR/venv/bin/pip" show requests > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "Installing requests..."
    "$AGENT_DIR/venv/bin/pip" install requests
fi

# Create required directories
mkdir -p "$AGENT_DIR/logs"
mkdir -p "$AGENT_DIR/reports"
mkdir -p "$AGENT_DIR/config"
mkdir -p "$AGENT_DIR/data"

# Install launchd plist
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

# Load the service
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "FM Transmitter fleet manager service started."
echo ""
echo "Commands:"
echo "  Check status:   launchctl list | grep fm-transmitter"
echo "  View logs:      tail -f ~/Agents/fm-transmitter/logs/agent.log"
echo "  Stop agent:     ./stop.sh"
echo "  List nodes:     venv/bin/python agent.py --list-nodes"
echo "  Scan health:    venv/bin/python agent.py --scan"
echo "  View report:    cat ~/Agents/fm-transmitter/reports/fm_fleet_$(date +%Y-%m-%d).md"
