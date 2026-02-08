#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.doc-manager.plist"
if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Document manager stopped."
else
    echo "Not installed."
fi
