#!/bin/bash
# Stop the YouTube agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.youtube-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "YouTube agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
