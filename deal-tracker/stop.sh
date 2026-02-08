#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.deal-tracker.plist"
if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Deal tracker stopped."
else
    echo "Not installed."
fi
