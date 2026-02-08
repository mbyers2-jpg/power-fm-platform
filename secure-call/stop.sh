#!/bin/bash
# Ribbon — Stop all services

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "Stopping Ribbon..."

# Stop Flask
if [ -f dashboard.pid ]; then
    PID=$(cat dashboard.pid)
    if kill -0 $PID 2>/dev/null; then
        kill $PID
        echo "  Dashboard stopped (PID: $PID)"
    fi
    rm -f dashboard.pid
fi

# Stop SFU
if [ -f sfu.pid ]; then
    PID=$(cat sfu.pid)
    if kill -0 $PID 2>/dev/null; then
        kill $PID
        echo "  SFU stopped (PID: $PID)"
    fi
    rm -f sfu.pid
fi

# Clean up SFU socket
rm -f sfu/mediasoup.sock

# Stop TURN if running
if [ -f turn/turnserver.pid ]; then
    PID=$(cat turn/turnserver.pid)
    if kill -0 $PID 2>/dev/null; then
        kill $PID
        echo "  TURN server stopped (PID: $PID)"
    fi
    rm -f turn/turnserver.pid
fi

echo "Ribbon stopped."
