#!/bin/bash
# Start coturn TURN server

DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v turnserver &> /dev/null; then
    echo "coturn not installed. Install with: brew install coturn"
    exit 1
fi

turnserver -c "$DIR/turnserver.conf" &
echo $! > "$DIR/turnserver.pid"
echo "TURN server started (PID: $(cat "$DIR/turnserver.pid"))"
