#!/bin/bash
# Stop the social media agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.social-media-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Social media agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
