#!/bin/bash
# Start the social media agent as a persistent background service
# Usage: ./start.sh

AGENT_DIR="$HOME/Agents/social-media-agent"
PLIST_NAME="com.marcbyers.social-media-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Social Media Agent ==="

# Check for venv
if [ ! -f "$AGENT_DIR/venv/bin/python" ]; then
    echo "ERROR: venv not found. Setting up..."
    python3 -m venv "$AGENT_DIR/venv"
    "$AGENT_DIR/venv/bin/pip" install -r "$AGENT_DIR/requirements.txt"
fi

# Install launchd plist
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

# Load the service
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Social media agent service started."
echo ""
echo "Commands:"
echo "  Check status:   launchctl list | grep social-media-agent"
echo "  View logs:      tail -f ~/Agents/social-media-agent/logs/agent.log"
echo "  Stop agent:     ./stop.sh"
echo "  View schedule:  cd $AGENT_DIR && venv/bin/python agent.py --schedule"
echo "  View report:    cd $AGENT_DIR && venv/bin/python agent.py --report"
