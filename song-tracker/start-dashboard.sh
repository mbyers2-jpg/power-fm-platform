#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/dashboard.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Dashboard already running (PID $(cat "$PID_FILE"))"
    echo "  http://localhost:5555"
    exit 0
fi

echo "Starting Song Tracker Dashboard..."
cd "$DIR"
nohup "$DIR/venv/bin/python" "$DIR/dashboard.py" > "$DIR/logs/dashboard.log" 2>&1 &
echo $! > "$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Dashboard running (PID $(cat "$PID_FILE"))"
    echo "  http://localhost:5555"
else
    echo "Failed to start dashboard. Check logs/dashboard.log"
    exit 1
fi
