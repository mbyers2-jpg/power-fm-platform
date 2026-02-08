#!/bin/bash
AGENT_DIR="$HOME/Agents/research-agent"
PLIST_NAME="com.marcbyers.research-agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Research Agent ==="
cp "$AGENT_DIR/$PLIST_NAME" "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Research agent service started."
echo ""
echo "Commands:"
echo "  View logs:     tail -f ~/Agents/research-agent/logs/research-agent.log"
echo "  Research person:  cd ~/Agents/research-agent && venv/bin/python agent.py --person 'Name'"
echo "  Research company: venv/bin/python agent.py --company 'Name'"
echo "  OSINT on Marc:    venv/bin/python agent.py --profile-marc"
echo "  Stop:          launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
