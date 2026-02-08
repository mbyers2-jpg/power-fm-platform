#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/dashboard.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Dashboard stopped (PID $PID)"
    else
        echo "Dashboard not running (stale PID)"
    fi
    rm -f "$PID_FILE"
else
    echo "No dashboard PID file found"
fi
