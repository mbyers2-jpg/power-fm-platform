#!/bin/bash
PIDFILE="$HOME/Agents/n8n/n8n.pid"
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    kill $PID 2>/dev/null
    rm "$PIDFILE"
    echo "n8n stopped."
else
    pkill -f "n8n start" 2>/dev/null
    echo "n8n stopped."
fi
