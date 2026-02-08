#!/bin/bash
# Start the email agent as a persistent background service
# Usage: ./start.sh

AGENT_DIR="$HOME/Agents/email-agent"
PLIST_NAME="com.marcbyers.email-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Marc Byers Email Agent ==="

# Check for credentials
if [ ! -f "$AGENT_DIR/config/credentials.json" ]; then
    echo "ERROR: credentials.json not found!"
    echo "Follow the setup guide: $AGENT_DIR/SETUP.md"
    exit 1
fi

# Check for token (first-time auth)
if [ ! -f "$AGENT_DIR/config/token.json" ]; then
    echo "First run — opening browser for Gmail authentication..."
    cd "$AGENT_DIR"
    venv/bin/python auth.py
    if [ $? -ne 0 ]; then
        echo "Authentication failed. Please try again."
        exit 1
    fi
fi

# Install launchd plist
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

# Load the service
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Email agent service started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep email-agent"
echo "  View logs:     tail -f ~/Agents/email-agent/logs/agent.log"
echo "  Stop agent:    launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  View briefing: cat ~/Agents/email-agent/briefings/briefing_$(date +%Y-%m-%d).md"
