#!/bin/bash
# Stop coturn TURN server

DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$DIR/turnserver.pid" ]; then
    PID=$(cat "$DIR/turnserver.pid")
    if kill -0 $PID 2>/dev/null; then
        kill $PID
        echo "TURN server stopped (PID: $PID)"
    fi
    rm -f "$DIR/turnserver.pid"
fi
