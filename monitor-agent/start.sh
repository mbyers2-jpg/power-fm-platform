#!/bin/bash
# Start the Monitor Agent as a persistent background service
AGENT_DIR="$HOME/Agents/monitor-agent"
PLIST_NAME="com.marcbyers.monitor-agent.plist"
PLIST_SRC="$AGENT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Monitor Agent ==="

# Create venv if missing
if [ ! -d "$AGENT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$AGENT_DIR/venv"
fi

# Ensure directories exist
mkdir -p "$AGENT_DIR/data" "$AGENT_DIR/logs" "$AGENT_DIR/reports"

# Seed agent registry if DB doesn't exist
if [ ! -f "$AGENT_DIR/data/monitor.db" ]; then
    echo "Seeding agent registry..."
    "$AGENT_DIR/venv/bin/python" "$AGENT_DIR/agent.py" --seed
fi

# Copy plist to LaunchAgents
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed launch agent to: $PLIST_DST"

# Load/reload with launchctl
launchctl unload "$PLIST_DST" 2>/dev/null
launchctl load "$PLIST_DST"
echo "Monitor Agent started."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep monitor-agent"
echo "  View logs:     tail -f ~/Agents/monitor-agent/logs/agent.log"
echo "  Stop agent:    ./stop.sh"
echo "  Health check:  venv/bin/python agent.py --check"
echo "  View report:   cat ~/Agents/monitor-agent/reports/health_\$(date +%Y-%m-%d).md"
