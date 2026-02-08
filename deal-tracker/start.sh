#!/bin/bash
AGENT_DIR="$HOME/Agents/deal-tracker"
PLIST_NAME="com.marcbyers.deal-tracker.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Deal Tracker Agent ==="
cp "$AGENT_DIR/$PLIST_NAME" "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Deal tracker service started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep deal-tracker"
echo "  View logs:     tail -f ~/Agents/deal-tracker/logs/deal-tracker.log"
echo "  Stop agent:    launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  View report:   cat ~/Agents/deal-tracker/reports/pipeline_\$(date +%Y-%m-%d).md"
