#!/bin/bash
# Start the icecast agent as a persistent background service
# Usage: ./start.sh

AGENT_DIR="$HOME/Agents/icecast-agent"
PLIST_NAME="com.marcbyers.icecast-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Power FM Icecast Agent ==="

# Check for config file
if [ ! -f "$AGENT_DIR/config/icecast_servers.json" ]; then
    echo "WARNING: config/icecast_servers.json not found."
    echo "The agent will start but won't monitor anything until servers are configured."
    echo "Run: venv/bin/python agent.py --add-server"
    echo "Or see: $AGENT_DIR/SETUP.md"
    echo ""
fi

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
echo "Icecast agent service started."
echo ""
echo "Commands:"
echo "  Check status:   launchctl list | grep icecast-agent"
echo "  View logs:      tail -f ~/Agents/icecast-agent/logs/agent.log"
echo "  Stop agent:     ./stop.sh"
echo "  Server status:  venv/bin/python agent.py --status"
echo "  Health check:   venv/bin/python agent.py --health"
echo "  View report:    cat ~/Agents/icecast-agent/reports/transmitter_network_$(date +%Y-%m-%d).md"
