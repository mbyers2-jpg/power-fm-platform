#!/bin/bash
# Stop the icecast agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.icecast-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Icecast agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
