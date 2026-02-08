#!/bin/bash
# Run all agents in background from Terminal (bypasses macOS permission issues)
# These run as long as Terminal is open. For true 24/7, grant Full Disk Access.
# See: ~/Agents/PERMISSIONS.md

AGENTS_DIR="$HOME/Agents"

echo "=== Starting All Agents from Terminal ==="
echo "(These run as background processes tied to this Terminal session)"
echo ""

# Doc Manager
cd "$AGENTS_DIR/doc-manager"
venv/bin/python agent.py --daemon &
DOC_PID=$!
echo "  Doc Manager:    PID $DOC_PID"

# Deal Tracker
cd "$AGENTS_DIR/deal-tracker"
venv/bin/python agent.py --daemon &
DEAL_PID=$!
echo "  Deal Tracker:   PID $DEAL_PID"

# Comms Agent
cd "$AGENTS_DIR/comms-agent"
venv/bin/python agent.py --daemon &
COMMS_PID=$!
echo "  Comms Agent:    PID $COMMS_PID"

# Research Agent
cd "$AGENTS_DIR/research-agent"
venv/bin/python agent.py --daemon &
RESEARCH_PID=$!
echo "  Research Agent: PID $RESEARCH_PID"

# n8n Workflow Automation
bash "$AGENTS_DIR/n8n/start.sh"
N8N_PID=$(cat "$AGENTS_DIR/n8n/n8n.pid" 2>/dev/null)
echo "  n8n:            PID $N8N_PID — http://localhost:5678"

echo ""
echo "All agents running. Press Ctrl+C to stop all."
echo "Logs: ~/Agents/*/logs/"
echo ""

# Wait and handle Ctrl+C
trap "echo 'Stopping all agents...'; kill $DOC_PID $DEAL_PID $COMMS_PID $RESEARCH_PID $N8N_PID 2>/dev/null; wait; echo 'Done.'; exit 0" INT TERM

wait
