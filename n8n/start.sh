#!/bin/bash
# Start n8n workflow automation server
# Dashboard: http://localhost:5678

AGENT_DIR="$HOME/Agents/n8n"
export N8N_USER_FOLDER="$AGENT_DIR/data"
export N8N_PORT=5678
export N8N_DIAGNOSTICS_ENABLED=false
export N8N_HIRING_BANNER_ENABLED=false
export GENERIC_TIMEZONE="America/Los_Angeles"

mkdir -p "$AGENT_DIR/data"

echo "=== n8n Workflow Automation ==="
echo "Starting on http://localhost:5678"
echo ""

cd "$AGENT_DIR"
node_modules/.bin/n8n start &
N8N_PID=$!
echo $N8N_PID > "$AGENT_DIR/n8n.pid"

sleep 3
echo "n8n running (PID $N8N_PID)"
echo ""
echo "Commands:"
echo "  Open dashboard:  open http://localhost:5678"
echo "  Stop:            ~/Agents/n8n/stop.sh"
echo "  Logs:            tail -f ~/Agents/n8n/data/.n8n/logs/n8n.log"
