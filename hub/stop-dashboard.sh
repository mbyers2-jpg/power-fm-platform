#!/bin/bash
# Stop the Agent Hub
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/dashboard.pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping hub (PID $PID)..."
        kill "$PID"
        sleep 1
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
        echo "Hub stopped."
    else
        echo "Hub not running (stale PID)."
    fi
    rm -f "$PIDFILE"
else
    echo "No PID file. Hub not running."
fi
