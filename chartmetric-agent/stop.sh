#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.chartmetric-agent.plist"
if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Chartmetric agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
