#!/bin/bash
# Stop the Spotify agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.spotify-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Spotify agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
