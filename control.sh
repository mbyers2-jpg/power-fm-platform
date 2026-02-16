#!/bin/bash
# Master control script for all Marc Byers agents
# Usage:
#   ./control.sh start     — Start all agents
#   ./control.sh stop      — Stop all agents
#   ./control.sh status    — Check agent status
#   ./control.sh logs      — Tail all agent logs
#   ./control.sh report    — Show latest reports

AGENTS_DIR="$HOME/Agents"
ACTION="${1:-status}"
ALL_AGENTS="email-agent deal-tracker doc-manager comms-agent research-agent song-tracker social-media-agent n8n secure-call chartmetric-agent elevenlabs-agent youtube-agent icecast-agent spotify-agent stripe-agent platform-hub"

case "$ACTION" in
    start)
        echo "=== Starting All Agents ==="
        echo ""
        bash "$AGENTS_DIR/doc-manager/start.sh"
        echo ""
        bash "$AGENTS_DIR/deal-tracker/start.sh"
        echo ""
        bash "$AGENTS_DIR/comms-agent/start.sh"
        echo ""
        bash "$AGENTS_DIR/research-agent/start.sh"
        echo ""
        if [ -f "$AGENTS_DIR/email-agent/config/credentials.json" ]; then
            bash "$AGENTS_DIR/email-agent/start.sh"
        else
            echo "[Email Agent] Skipped — credentials.json not set up yet"
            echo "  Follow: ~/Agents/email-agent/SETUP.md"
        fi
        echo ""
        bash "$AGENTS_DIR/song-tracker/start.sh"
        echo ""
        bash "$AGENTS_DIR/social-media-agent/start.sh"
        echo ""
        bash "$AGENTS_DIR/n8n/start.sh"
        echo ""
        bash "$AGENTS_DIR/secure-call/start.sh"
        echo ""
        # Power FM Platform Agents
        for pfm_agent in chartmetric-agent elevenlabs-agent youtube-agent icecast-agent spotify-agent stripe-agent; do
            if ls "$AGENTS_DIR/$pfm_agent/config/"*.json >/dev/null 2>&1; then
                bash "$AGENTS_DIR/$pfm_agent/start.sh"
            else
                echo "[$pfm_agent] Skipped — config not set up yet"
                echo "  Follow: ~/Agents/$pfm_agent/SETUP.md"
            fi
            echo ""
        done
        # Platform Hub (no config needed — reads from other agent DBs)
        bash "$AGENTS_DIR/platform-hub/start.sh"
        echo ""
        echo "All agents started."
        ;;

    stop)
        echo "=== Stopping All Agents ==="
        for agent in $ALL_AGENTS; do
            bash "$AGENTS_DIR/$agent/stop.sh" 2>/dev/null
        done
        echo "All agents stopped."
        ;;

    status)
        echo "=== Agent Status ==="
        echo ""
        for name in $ALL_AGENTS; do
            if [ "$name" = "n8n" ]; then
                if [ -f "$AGENTS_DIR/n8n/n8n.pid" ] && kill -0 $(cat "$AGENTS_DIR/n8n/n8n.pid") 2>/dev/null; then
                    echo "  n8n: RUNNING (PID $(cat "$AGENTS_DIR/n8n/n8n.pid")) — http://localhost:5678"
                else
                    echo "  n8n: STOPPED"
                fi
            elif [ "$name" = "secure-call" ]; then
                if [ -f "$AGENTS_DIR/secure-call/dashboard.pid" ] && kill -0 $(cat "$AGENTS_DIR/secure-call/dashboard.pid") 2>/dev/null; then
                    echo "  secure-call (Ribbon): RUNNING (PID $(cat "$AGENTS_DIR/secure-call/dashboard.pid")) — http://localhost:5558"
                else
                    echo "  secure-call (Ribbon): STOPPED"
                fi
            else
                pid=$(launchctl list 2>/dev/null | grep "com.marcbyers.$name" | awk '{print $1}')
                if [ -n "$pid" ] && [ "$pid" != "-" ]; then
                    echo "  $name: RUNNING (PID $pid)"
                else
                    echo "  $name: STOPPED"
                fi
            fi
        done
        echo ""
        ;;

    logs)
        echo "=== Tailing All Agent Logs (Ctrl+C to stop) ==="
        tail -f \
            "$AGENTS_DIR/email-agent/logs/agent.log" \
            "$AGENTS_DIR/deal-tracker/logs/deal-tracker.log" \
            "$AGENTS_DIR/doc-manager/logs/doc-manager.log" \
            "$AGENTS_DIR/comms-agent/logs/comms-agent.log" \
            "$AGENTS_DIR/research-agent/logs/research-agent.log" \
            "$AGENTS_DIR/social-media-agent/logs/agent.log" \
            "$AGENTS_DIR/secure-call/logs/dashboard.log" \
            "$AGENTS_DIR/secure-call/logs/sfu.log" \
            "$AGENTS_DIR/chartmetric-agent/logs/agent.log" \
            "$AGENTS_DIR/elevenlabs-agent/logs/agent.log" \
            "$AGENTS_DIR/youtube-agent/logs/agent.log" \
            "$AGENTS_DIR/icecast-agent/logs/agent.log" \
            "$AGENTS_DIR/spotify-agent/logs/agent.log" \
            "$AGENTS_DIR/stripe-agent/logs/agent.log" \
            "$AGENTS_DIR/platform-hub/logs/agent.log" \
            2>/dev/null
        ;;

    report)
        echo "=== Latest Reports ==="
        echo ""

        echo "--- Deal Pipeline ---"
        latest=$(ls -t "$AGENTS_DIR/deal-tracker/reports/"pipeline_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No pipeline report found."
        echo ""

        echo "--- Email Briefing ---"
        latest=$(ls -t "$AGENTS_DIR/email-agent/briefings/"briefing_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "Email agent not set up yet."
        echo ""

        echo "--- Comms Report ---"
        latest=$(ls -t "$AGENTS_DIR/comms-agent/data/"comms_report_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No comms report yet."
        echo ""

        echo "--- Intelligence Report ---"
        latest=$(ls -t "$AGENTS_DIR/research-agent/reports/"intel_report_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No intel report yet."
        echo ""

        echo "--- Song Catalog ---"
        latest=$(ls -t "$AGENTS_DIR/song-tracker/reports/"catalog_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No song catalog report yet."
        echo ""

        echo "--- Social Media ---"
        latest=$(ls -t "$AGENTS_DIR/social-media-agent/reports/"engagement_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No social media report yet."
        echo ""

        echo "--- Power Charts (Chartmetric) ---"
        latest=$(ls -t "$AGENTS_DIR/chartmetric-agent/reports/"charts_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No charts report yet."
        echo ""

        echo "--- Localization (ElevenLabs) ---"
        latest=$(ls -t "$AGENTS_DIR/elevenlabs-agent/reports/"localization_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No localization report yet."
        echo ""

        echo "--- YouTube ---"
        latest=$(ls -t "$AGENTS_DIR/youtube-agent/reports/"youtube_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No YouTube report yet."
        echo ""

        echo "--- Transmitter Network (Icecast) ---"
        latest=$(ls -t "$AGENTS_DIR/icecast-agent/reports/"transmitter_network_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No transmitter network report yet."
        echo ""

        echo "--- Spotify ---"
        latest=$(ls -t "$AGENTS_DIR/spotify-agent/reports/"spotify_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No Spotify report yet."
        echo ""

        echo "--- Revenue (Stripe) ---"
        latest=$(ls -t "$AGENTS_DIR/stripe-agent/reports/"revenue_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No revenue report yet."
        echo ""

        echo "--- Platform Dashboard ---"
        latest=$(ls -t "$AGENTS_DIR/platform-hub/reports/"platform_*.md 2>/dev/null | head -1)
        [ -n "$latest" ] && cat "$latest" || echo "No platform dashboard yet."
        ;;

    *)
        echo "Usage: ./control.sh {start|stop|status|logs|report}"
        echo ""
        echo "Agents: $ALL_AGENTS"
        ;;
esac
