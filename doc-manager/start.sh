#!/bin/bash
AGENT_DIR="$HOME/Agents/doc-manager"
PLIST_NAME="com.marcbyers.doc-manager.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Document Manager Agent ==="
cp "$AGENT_DIR/$PLIST_NAME" "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Document manager service started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep doc-manager"
echo "  View logs:     tail -f ~/Agents/doc-manager/logs/doc-manager.log"
echo "  Stop agent:    launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  Dry run:       cd ~/Agents/doc-manager && venv/bin/python agent.py --dry-run"
