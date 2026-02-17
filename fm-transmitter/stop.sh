#!/bin/bash
# Stop the FM Transmitter fleet manager service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.fm-transmitter.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "FM Transmitter fleet manager stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
