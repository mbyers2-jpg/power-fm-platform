#!/bin/bash
# Start the ElevenLabs agent as a persistent background service
# Usage: ./start.sh

AGENT_DIR="$HOME/Agents/elevenlabs-agent"
PLIST_NAME="com.marcbyers.elevenlabs-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== ElevenLabs Voice Generation Agent ==="

# Check for config
if [ ! -f "$AGENT_DIR/config/elevenlabs_config.json" ]; then
    echo "ERROR: elevenlabs_config.json not found!"
    echo ""
    echo "Create it with your API key:"
    echo "  mkdir -p $AGENT_DIR/config"
    echo "  echo '{\"api_key\": \"your-key-here\"}' > $AGENT_DIR/config/elevenlabs_config.json"
    echo ""
    echo "Get your key at: https://elevenlabs.io/app/settings/api-keys"
    echo "See full setup guide: $AGENT_DIR/SETUP.md"
    exit 1
fi

# Create venv if needed
if [ ! -d "$AGENT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$AGENT_DIR/venv"
    echo "Installing dependencies..."
    "$AGENT_DIR/venv/bin/pip" install --upgrade pip
    "$AGENT_DIR/venv/bin/pip" install requests
    echo "Dependencies installed."
fi

# Ensure requests is installed
"$AGENT_DIR/venv/bin/pip" show requests > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "Installing requests..."
    "$AGENT_DIR/venv/bin/pip" install requests
fi

# Create required directories
mkdir -p "$AGENT_DIR/logs"
mkdir -p "$AGENT_DIR/output"
mkdir -p "$AGENT_DIR/reports"
mkdir -p "$AGENT_DIR/data"

# Install launchd plist
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

# Load the service
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "ElevenLabs agent service started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep elevenlabs-agent"
echo "  View logs:     tail -f ~/Agents/elevenlabs-agent/logs/agent.log"
echo "  Stop agent:    ./stop.sh"
echo "  View report:   cat ~/Agents/elevenlabs-agent/reports/localization_$(date +%Y-%m-%d).md"
