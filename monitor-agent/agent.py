#!/usr/bin/env python3
"""
PTC Monitor Agent — System-Wide Health Monitoring & Auto-Healing

Continuously monitors all 27+ agents for health, detects failures,
and auto-restarts downed services with safeguards.

Usage:
    venv/bin/python agent.py --check          # One health check cycle
    venv/bin/python agent.py --status         # Status only (no auto-heal)
    venv/bin/python agent.py --daemon         # Run continuously (60s interval)
    venv/bin/python agent.py --report         # Generate health report
    venv/bin/python agent.py --seed           # Seed agent registry
    venv/bin/python agent.py --incidents      # Show open incidents
"""

import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

# --- Configuration ---
POLL_INTERVAL = 60  # seconds
LOG_DIR = os.path.join(AGENT_DIR, 'logs')
REPORT_DIR = os.path.join(AGENT_DIR, 'reports')

# --- Logging ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('monitor-agent')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def run_check(conn, auto_heal=True):
    """Run a single health check cycle.

    Returns (results, actions, disk_ok, free_gb).
    """
    from health_checker import check_all_agents, format_status_table
    from auto_healer import heal_results, format_heal_actions
    from database import set_state

    results, disk_ok, free_gb = check_all_agents(conn)

    # Print status table
    table = format_status_table(results, disk_ok, free_gb)
    print(table)

    # Auto-heal if enabled
    actions = []
    if auto_heal:
        actions = heal_results(conn, results)
        if actions:
            action_str = format_heal_actions(actions)
            print(action_str)

    # Update state
    set_state(conn, 'last_check', datetime.utcnow().isoformat())
    cycle = int(set_state_get(conn, 'cycle_count', '0')) + 1
    set_state(conn, 'cycle_count', str(cycle))

    return results, actions, disk_ok, free_gb


def set_state_get(conn, key, default='0'):
    """Helper to get state value with default."""
    from database import get_state
    val = get_state(conn, key, default)
    return val if val else default


def run_daemon(conn):
    """Continuous monitoring loop."""
    log.info(f"Monitor agent starting in daemon mode (poll every {POLL_INTERVAL}s)")

    # Initial cycle
    try:
        run_check(conn, auto_heal=True)
    except Exception as e:
        log.error(f"Initial check cycle failed: {e}")

    while running:
        log.info(f"Sleeping {POLL_INTERVAL}s until next check...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        try:
            run_check(conn, auto_heal=True)
        except Exception as e:
            log.error(f"Check cycle failed: {e}")

    log.info("Monitor agent stopped.")


def main():
    parser = argparse.ArgumentParser(
        description='PTC Monitor Agent — System-Wide Health Monitoring & Auto-Healing'
    )
    parser.add_argument('--check', action='store_true',
                        help='Run one health check cycle with auto-healing')
    parser.add_argument('--status', action='store_true',
                        help='Status only — no auto-healing')
    parser.add_argument('--daemon', action='store_true',
                        help='Run continuously (60s interval)')
    parser.add_argument('--report', action='store_true',
                        help='Generate health report (markdown + JSON)')
    parser.add_argument('--seed', action='store_true',
                        help='Seed agent registry with all known agents')
    parser.add_argument('--incidents', action='store_true',
                        help='Show open incidents')
    args = parser.parse_args()

    log.info("Initializing monitor agent...")

    from database import get_connection, seed_agents, get_open_incidents

    conn = get_connection()

    if args.seed:
        count = seed_agents(conn)
        print(f"\nSeeded {count} agents in registry.\n")

    elif args.check:
        run_check(conn, auto_heal=True)

    elif args.status:
        run_check(conn, auto_heal=False)

    elif args.daemon:
        run_daemon(conn)

    elif args.report:
        from health_checker import check_all_agents
        from reporter import generate_report, generate_json_report

        results, disk_ok, free_gb = check_all_agents(conn)
        md_path = generate_report(conn, results, disk_ok, free_gb)
        json_path = generate_json_report(conn, results, disk_ok, free_gb)
        print(f"\nMarkdown report: {md_path}")
        print(f"JSON report:     {json_path}\n")

    elif args.incidents:
        from reporter import format_incidents
        incidents = get_open_incidents(conn)
        print(format_incidents(incidents))

    else:
        # Default: run a status check (no heal)
        run_check(conn, auto_heal=False)

    conn.close()


if __name__ == '__main__':
    main()
