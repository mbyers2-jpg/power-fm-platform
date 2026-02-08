#!/bin/bash
PIDFILE="$HOME/Agents/song-tracker/song-tracker.pid"
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    kill $PID 2>/dev/null
    rm "$PIDFILE"
    echo "Song tracker stopped."
else
    pkill -f "song-tracker.*agent.py" 2>/dev/null
    echo "Song tracker stopped."
fi
