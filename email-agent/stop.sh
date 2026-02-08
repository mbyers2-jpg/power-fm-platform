#!/bin/bash
# Stop the email agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.email-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Email agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
