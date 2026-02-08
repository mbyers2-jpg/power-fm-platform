#!/bin/bash
# Start the Deal Tracker Dashboard on port 5556
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/dashboard.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Dashboard already running (PID $(cat "$PIDFILE"))"
    echo "  http://localhost:5556"
    exit 0
fi

echo "Starting Deal Tracker Dashboard..."
cd "$DIR"
nohup "$DIR/venv/bin/python" "$DIR/dashboard.py" > "$DIR/logs/dashboard.log" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"

# Wait and verify
sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "Dashboard running (PID $PID)"
    echo "  http://localhost:5556"
else
    echo "Failed to start dashboard. Check logs/dashboard.log"
    rm -f "$PIDFILE"
    exit 1
fi
