#!/bin/bash
# Start the Spotify agent as a persistent background service
# Usage: ./start.sh

AGENT_DIR="$HOME/Agents/spotify-agent"
PLIST_NAME="com.marcbyers.spotify-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Power FM Spotify Agent ==="

# Check for config
if [ ! -f "$AGENT_DIR/config/spotify_config.json" ]; then
    echo "ERROR: spotify_config.json not found!"
    echo ""
    echo "Create the config file with your Spotify Developer credentials:"
    echo ""
    echo "  mkdir -p $AGENT_DIR/config"
    echo '  cat > $AGENT_DIR/config/spotify_config.json << EOF'
    echo '  {'
    echo '    "client_id": "YOUR_CLIENT_ID",'
    echo '    "client_secret": "YOUR_CLIENT_SECRET"'
    echo '  }'
    echo '  EOF'
    echo ""
    echo "Follow the setup guide: $AGENT_DIR/SETUP.md"
    exit 1
fi

# Create venv if needed
if [ ! -d "$AGENT_DIR/venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$AGENT_DIR/venv"
    echo "Installing dependencies..."
    "$AGENT_DIR/venv/bin/pip" install --quiet requests
    echo "Dependencies installed."
fi

# Ensure requests is installed
"$AGENT_DIR/venv/bin/pip" install --quiet requests 2>/dev/null

# Create required directories
mkdir -p "$AGENT_DIR/data"
mkdir -p "$AGENT_DIR/logs"
mkdir -p "$AGENT_DIR/reports"

# Install launchd plist
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

# Load the service
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Spotify agent service started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep spotify-agent"
echo "  View logs:     tail -f ~/Agents/spotify-agent/logs/agent.log"
echo "  Stop agent:    ./stop.sh"
echo "  View report:   cat ~/Agents/spotify-agent/reports/spotify_$(date +%Y-%m-%d).md"
