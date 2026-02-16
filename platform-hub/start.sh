#!/bin/bash
# Start the Platform Hub as a persistent background service
AGENT_DIR="$HOME/Agents/platform-hub"
PLIST_NAME="com.marcbyers.platform-hub.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Power FM Platform Hub ==="

if [ ! -d "$AGENT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$AGENT_DIR/venv"
    "$AGENT_DIR/venv/bin/pip" install requests
fi

cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Platform Hub started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep platform-hub"
echo "  View logs:     tail -f ~/Agents/platform-hub/logs/agent.log"
echo "  Stop agent:    ./stop.sh"
echo "  Dashboard:     venv/bin/python agent.py --dashboard"
echo "  View report:   cat ~/Agents/platform-hub/reports/platform_$(date +%Y-%m-%d).md"
