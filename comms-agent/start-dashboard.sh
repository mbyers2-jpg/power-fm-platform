#!/bin/bash
# Start the Comms & Email Agent Dashboard on port 5557
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PIDFILE="$SCRIPT_DIR/dashboard.pid"
LOGFILE="$SCRIPT_DIR/logs/dashboard.log"

mkdir -p "$SCRIPT_DIR/logs"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Dashboard already running (PID $PID)"
        echo "  http://localhost:5557"
        exit 0
    else
        rm -f "$PIDFILE"
    fi
fi

echo "Starting Comms & Email Dashboard on port 5557..."
nohup "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/dashboard.py" >> "$LOGFILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
sleep 1

if kill -0 "$PID" 2>/dev/null; then
    echo "Dashboard started (PID $PID)"
    echo "  http://localhost:5557"
else
    echo "Failed to start dashboard. Check $LOGFILE"
    rm -f "$PIDFILE"
    exit 1
fi
