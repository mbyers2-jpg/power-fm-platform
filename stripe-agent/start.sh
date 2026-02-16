#!/bin/bash
# Start the Stripe agent as a persistent background service
AGENT_DIR="$HOME/Agents/stripe-agent"
PLIST_NAME="com.marcbyers.stripe-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Stripe Agent ==="

if [ ! -f "$AGENT_DIR/config/stripe_config.json" ]; then
    echo "ERROR: stripe_config.json not found!"
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
echo "Stripe agent started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep stripe"
echo "  View logs:     tail -f ~/Agents/stripe-agent/logs/agent.log"
echo "  Stop agent:    ./stop.sh"
echo "  View report:   cat ~/Agents/stripe-agent/reports/revenue_$(date +%Y-%m-%d).md"
