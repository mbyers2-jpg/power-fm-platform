#!/bin/bash
# Start the YouTube agent as a persistent background service
AGENT_DIR="$HOME/Agents/youtube-agent"
PLIST_NAME="com.marcbyers.youtube-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== YouTube Agent ==="

if [ ! -f "$AGENT_DIR/config/youtube_config.json" ]; then
    echo "ERROR: youtube_config.json not found!"
    echo "Follow the setup guide: $AGENT_DIR/SETUP.md"
    exit 1
fi

if [ ! -d "$AGENT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$AGENT_DIR/venv"
    "$AGENT_DIR/venv/bin/pip" install requests
fi

cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "YouTube agent started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep youtube"
echo "  View logs:     tail -f ~/Agents/youtube-agent/logs/agent.log"
echo "  Stop agent:    ./stop.sh"
echo "  View report:   cat ~/Agents/youtube-agent/reports/youtube_$(date +%Y-%m-%d).md"
