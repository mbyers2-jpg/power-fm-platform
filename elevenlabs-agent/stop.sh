#!/bin/bash
# Stop the ElevenLabs agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.elevenlabs-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "ElevenLabs agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
