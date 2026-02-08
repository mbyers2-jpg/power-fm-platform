#!/bin/bash
# Start the Agent Hub on port 5550
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/dashboard.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Hub already running (PID $(cat "$PIDFILE"))"
    echo "  http://localhost:5550"
    exit 0
fi

# Use song-tracker's venv since Flask is already installed there
PYTHON="/Users/marcbyers/Agents/song-tracker/venv/bin/python"

echo "Starting Agent Hub..."
cd "$DIR"
nohup "$PYTHON" "$DIR/dashboard.py" > "$DIR/logs/dashboard.log" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"

sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "Hub running (PID $PID)"
    echo "  http://localhost:5550"
else
    echo "Failed to start hub. Check logs/dashboard.log"
    rm -f "$PIDFILE"
    exit 1
fi
