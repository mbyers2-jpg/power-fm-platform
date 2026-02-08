#!/bin/bash
# Stop the Deal Tracker Dashboard
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/dashboard.pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping dashboard (PID $PID)..."
        kill "$PID"
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID"
        fi
        echo "Dashboard stopped."
    else
        echo "Dashboard not running (stale PID file)."
    fi
    rm -f "$PIDFILE"
else
    echo "No PID file found. Checking for running processes..."
    PIDS=$(pgrep -f "dashboard.py.*5556" 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "Killing dashboard processes: $PIDS"
        kill $PIDS
    else
        echo "Dashboard not running."
    fi
fi
