#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.comms-agent.plist"
[ -f "$PLIST" ] && launchctl unload "$PLIST" && echo "Comms agent stopped." || echo "Not installed."
