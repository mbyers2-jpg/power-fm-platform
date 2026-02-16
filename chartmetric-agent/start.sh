#!/bin/bash
AGENT_DIR="$HOME/Agents/chartmetric-agent"
PLIST_NAME="com.marcbyers.chartmetric-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
echo "=== Chartmetric Agent ==="
if [ ! -f "$AGENT_DIR/config/chartmetric_config.json" ]; then
    echo "ERROR: chartmetric_config.json not found!"
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
echo "Chartmetric agent started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep chartmetric"
echo "  View logs:     tail -f ~/Agents/chartmetric-agent/logs/agent.log"
echo "  Stop agent:    ./stop.sh"
echo "  View report:   cat ~/Agents/chartmetric-agent/reports/charts_$(date +%Y-%m-%d).md"
