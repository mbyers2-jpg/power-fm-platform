#!/bin/bash
# Stop the Monitor Agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.monitor-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Monitor Agent stopped."
else
    echo "Monitor Agent not installed. Nothing to stop."
fi
