#!/bin/bash
AGENT_DIR="$HOME/Agents/comms-agent"
PLIST_NAME="com.marcbyers.comms-agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Comms Agent ==="

# Check for email-agent credentials (shared)
if [ ! -f "$HOME/Agents/email-agent/config/credentials.json" ] && [ ! -f "$AGENT_DIR/config/credentials.json" ]; then
    echo "WARNING: Gmail credentials not found."
    echo "Set up email-agent first: ~/Agents/email-agent/SETUP.md"
    echo "Comms agent will work with local data only until Gmail is connected."
fi

cp "$AGENT_DIR/$PLIST_NAME" "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Comms agent service started."
echo ""
echo "Commands:"
echo "  View logs:     tail -f ~/Agents/comms-agent/logs/comms-agent.log"
echo "  View drafts:   ls ~/Agents/comms-agent/drafts/"
echo "  Generate drafts: cd ~/Agents/comms-agent && venv/bin/python agent.py --drafts"
echo "  Stop:          launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
