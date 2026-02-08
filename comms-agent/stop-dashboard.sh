#!/bin/bash
# Stop the Comms & Email Agent Dashboard
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/dashboard.pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping dashboard (PID $PID)..."
        kill "$PID"
        sleep 1
        # Force kill if still running
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID"
        fi
        echo "Dashboard stopped."
    else
        echo "Dashboard not running (stale PID file)."
    fi
    rm -f "$PIDFILE"
else
    echo "No PID file found. Dashboard may not be running."
    # Try to find and kill by port
    PIDS=$(lsof -ti :5557 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "Found process(es) on port 5557: $PIDS"
        echo "$PIDS" | xargs kill 2>/dev/null
        echo "Killed."
    fi
fi
