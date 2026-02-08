#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.research-agent.plist"
[ -f "$PLIST" ] && launchctl unload "$PLIST" && echo "Research agent stopped." || echo "Not installed."
