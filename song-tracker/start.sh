#!/bin/bash
AGENT_DIR="$HOME/Agents/song-tracker"
cd "$AGENT_DIR"

echo "=== Song Tracker ==="
venv/bin/python agent.py --daemon > logs/stdout.log 2> logs/stderr.log &
PID=$!
echo $PID > "$AGENT_DIR/song-tracker.pid"
echo "Song tracker running (PID $PID)"
echo ""
echo "Commands:"
echo "  Scan now:       cd ~/Agents/song-tracker && venv/bin/python agent.py --scan"
echo "  Full report:    venv/bin/python agent.py --report"
echo "  Song report:    venv/bin/python agent.py --song <ID>"
echo "  List catalog:   venv/bin/python agent.py --list"
echo "  Import data:    venv/bin/python agent.py --import-file <file.csv> --import-type spotify"
echo "  Catalog value:  venv/bin/python agent.py --catalog-value"
echo "  Stop:           ~/Agents/song-tracker/stop.sh"
