#!/bin/bash
# Stop the Stripe agent service
PLIST="$HOME/Library/LaunchAgents/com.marcbyers.stripe-agent.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST"
    echo "Stripe agent stopped."
else
    echo "Agent not installed. Nothing to stop."
fi
