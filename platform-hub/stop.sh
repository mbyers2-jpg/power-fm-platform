#!/bin/bash
# Stop the Platform Hub service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.platform-hub.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Platform Hub stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
