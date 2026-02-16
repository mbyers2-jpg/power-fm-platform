#!/usr/bin/env python3
"""
Power FM Stream Health Monitor

Pings all 9 Power FM stations every 30 seconds, detects degraded or down
stations, and auto-restarts any that crash. Tracks restart counts to avoid
restart loops (max 3 restarts per station per 5-minute window).

Usage:
    venv/bin/python health_monitor.py --check     # Run one health check and exit
    venv/bin/python health_monitor.py --daemon     # Run continuously (every 30s)
    venv/bin/python health_monitor.py --status     # Show health summary (no restarts)
"""

import os
import sys
import signal
import logging
import argparse
import time
import json
from datetime import datetime, timedelta

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

from stations import STATIONS, start_station, is_running

# --- Configuration ---
CHECK_INTERVAL = 30  # seconds between health checks
HTTP_TIMEOUT = 3     # seconds for HTTP status check
MAX_RESTARTS = 3     # max restarts per station in the restart window
RESTART_WINDOW = 300 # seconds (5 minutes)

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(AGENT_DIR, 'logs')
PID_DIR = os.path.join(AGENT_DIR, 'pids')

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PID_DIR, exist_ok=True)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'health_monitor.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('health-monitor')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Stopping health monitor...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

# --- Restart Tracking ---
# Per-station list of restart timestamps: { 'la': [datetime, datetime, ...], ... }
restart_history = {key: [] for key in STATIONS}


def prune_restart_history(station_key):
    """Remove restart records older than the restart window."""
    cutoff = datetime.now() - timedelta(seconds=RESTART_WINDOW)
    restart_history[station_key] = [
        ts for ts in restart_history[station_key] if ts > cutoff
    ]


def can_restart(station_key):
    """Check if a station can be restarted (hasn't exceeded max restarts in window)."""
    prune_restart_history(station_key)
    return len(restart_history[station_key]) < MAX_RESTARTS


def record_restart(station_key):
    """Record that a restart was attempted."""
    restart_history[station_key].append(datetime.now())


def get_restart_count(station_key):
    """Get the number of restarts in the current window."""
    prune_restart_history(station_key)
    return len(restart_history[station_key])


# --- Health Check Functions ---

def http_check(port):
    """
    Try to GET http://localhost:{port}/status.json with a 3-second timeout.
    Returns (success: bool, status_code: int or None, error: str or None).
    """
    url = f'http://localhost:{port}/status.json'
    try:
        req = urllib.request.Request(url, method='GET')
        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)
        status_code = resp.getcode()
        resp.close()
        if 200 <= status_code < 400:
            return True, status_code, None
        else:
            return False, status_code, f"HTTP {status_code}"
    except urllib.error.HTTPError as e:
        return False, e.code, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, None, str(e.reason)
    except Exception as e:
        return False, None, str(e)


def check_station(station_key, allow_restart=True):
    """
    Check a single station's health.

    Returns a dict:
        {
            'key': str,
            'name': str,
            'port': int,
            'pid_alive': bool,
            'http_ok': bool,
            'http_status': int or None,
            'http_error': str or None,
            'status': 'healthy' | 'degraded' | 'down',
            'action': str or None,
            'restart_count': int,
        }
    """
    station = STATIONS[station_key]
    port = station['port']
    name = station['name']

    result = {
        'key': station_key,
        'name': name,
        'port': port,
        'pid_alive': False,
        'http_ok': False,
        'http_status': None,
        'http_error': None,
        'status': 'down',
        'action': None,
        'restart_count': get_restart_count(station_key),
    }

    # Step 1: PID-based check
    pid_alive = is_running(station_key)
    result['pid_alive'] = pid_alive

    # Step 2: HTTP check
    http_ok, http_status, http_error = http_check(port)
    result['http_ok'] = http_ok
    result['http_status'] = http_status
    result['http_error'] = http_error

    # Determine status
    if pid_alive and http_ok:
        result['status'] = 'healthy'
    elif pid_alive and not http_ok:
        result['status'] = 'degraded'
        log.warning(f"{name} ({station_key}): PID alive but HTTP failed — {http_error}")
    else:
        result['status'] = 'down'

        if allow_restart:
            if can_restart(station_key):
                log.warning(f"{name} ({station_key}): DOWN — attempting auto-restart...")
                record_restart(station_key)
                result['restart_count'] = get_restart_count(station_key)

                success = start_station(station_key)
                if success:
                    result['action'] = 'restarted'
                    log.info(f"{name} ({station_key}): restart initiated successfully")
                else:
                    result['action'] = 'restart_failed'
                    log.error(f"{name} ({station_key}): restart FAILED")
            else:
                result['action'] = 'restart_suppressed'
                log.error(
                    f"{name} ({station_key}): DOWN but restart suppressed "
                    f"({MAX_RESTARTS} restarts in last {RESTART_WINDOW}s)"
                )
        else:
            result['action'] = 'no_restart_requested'

    return result


def run_check(allow_restart=True):
    """
    Run a health check on all stations. Returns a list of result dicts.
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log.info(f"Health check started at {timestamp}")

    results = []
    for key in STATIONS:
        result = check_station(key, allow_restart=allow_restart)
        results.append(result)

    # Log summary
    healthy = sum(1 for r in results if r['status'] == 'healthy')
    degraded = sum(1 for r in results if r['status'] == 'degraded')
    down = sum(1 for r in results if r['status'] == 'down')
    restarted = sum(1 for r in results if r['action'] == 'restarted')

    log.info(
        f"Health check complete: {healthy} healthy, {degraded} degraded, "
        f"{down} down, {restarted} restarted"
    )

    return results


def print_summary(results):
    """Print a formatted summary table of all station health."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    print()
    print("  POWER FM STREAM HEALTH MONITOR")
    print(f"  {timestamp}")
    print("  " + "=" * 90)
    print(
        f"  {'Station':<25} {'Port':<7} {'PID':<6} {'HTTP':<6} "
        f"{'Status':<12} {'Restarts':<10} {'Action'}"
    )
    print("  " + "-" * 90)

    for r in results:
        pid_str = "UP" if r['pid_alive'] else "DOWN"
        http_str = "OK" if r['http_ok'] else "FAIL"

        # Color-coded status
        status = r['status'].upper()
        if status == 'HEALTHY':
            status_display = f"\033[92m{status}\033[0m"
        elif status == 'DEGRADED':
            status_display = f"\033[93m{status}\033[0m"
        else:
            status_display = f"\033[91m{status}\033[0m"

        # PID coloring
        if r['pid_alive']:
            pid_display = f"\033[92m{pid_str}\033[0m"
        else:
            pid_display = f"\033[91m{pid_str}\033[0m"

        # HTTP coloring
        if r['http_ok']:
            http_display = f"\033[92m{http_str}\033[0m"
        else:
            http_display = f"\033[91m{http_str}\033[0m"

        # Restart count with warning color if high
        restart_count = r['restart_count']
        if restart_count >= MAX_RESTARTS:
            restart_display = f"\033[91m{restart_count}/{MAX_RESTARTS}\033[0m"
        elif restart_count > 0:
            restart_display = f"\033[93m{restart_count}/{MAX_RESTARTS}\033[0m"
        else:
            restart_display = f"{restart_count}/{MAX_RESTARTS}"

        action = r['action'] or '-'

        # Pad for ANSI color codes (they add invisible chars)
        print(
            f"  {r['name']:<25} {r['port']:<7} {pid_display:<15} {http_display:<15} "
            f"{status_display:<21} {restart_display:<19} {action}"
        )

    print("  " + "-" * 90)

    # Summary line
    healthy = sum(1 for r in results if r['status'] == 'healthy')
    degraded = sum(1 for r in results if r['status'] == 'degraded')
    down = sum(1 for r in results if r['status'] == 'down')
    total = len(results)

    summary_parts = [f"{healthy}/{total} healthy"]
    if degraded > 0:
        summary_parts.append(f"\033[93m{degraded} degraded\033[0m")
    if down > 0:
        summary_parts.append(f"\033[91m{down} down\033[0m")

    print(f"  {' | '.join(summary_parts)}")

    # Show HTTP errors for degraded/down stations
    problem_stations = [r for r in results if r['status'] != 'healthy' and r['http_error']]
    if problem_stations:
        print()
        print("  Issues:")
        for r in problem_stations:
            print(f"    {r['name']}: {r['http_error']}")

    print()


# --- CLI Commands ---

def cmd_check():
    """Run one health check with auto-restart and exit."""
    results = run_check(allow_restart=True)
    print_summary(results)

    # Return exit code based on health
    down = sum(1 for r in results if r['status'] == 'down')
    degraded = sum(1 for r in results if r['status'] == 'degraded')
    if down > 0:
        return 2
    elif degraded > 0:
        return 1
    return 0


def cmd_status():
    """Show health summary without restarting anything."""
    results = run_check(allow_restart=False)
    print_summary(results)

    down = sum(1 for r in results if r['status'] == 'down')
    degraded = sum(1 for r in results if r['status'] == 'degraded')
    if down > 0:
        return 2
    elif degraded > 0:
        return 1
    return 0


def cmd_daemon():
    """Run health checks continuously every 30 seconds."""
    log.info(f"Health monitor daemon starting (interval: {CHECK_INTERVAL}s)")
    log.info(f"Restart policy: max {MAX_RESTARTS} restarts per station per {RESTART_WINDOW}s window")
    log.info(f"Monitoring {len(STATIONS)} stations")

    cycle = 0
    while running:
        cycle += 1
        log.info(f"--- Health check cycle {cycle} ---")

        results = run_check(allow_restart=True)
        print_summary(results)

        # Wait for next cycle, checking shutdown flag each second
        for _ in range(CHECK_INTERVAL):
            if not running:
                break
            time.sleep(1)

    log.info("Health monitor daemon stopped.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Power FM Stream Health Monitor'
    )
    parser.add_argument('--check', action='store_true',
                        help='Run one health check (with auto-restart) and exit')
    parser.add_argument('--daemon', action='store_true',
                        help='Run continuously (every 30 seconds)')
    parser.add_argument('--status', action='store_true',
                        help='Show health summary without restarting anything')
    args = parser.parse_args()

    if args.daemon:
        exit_code = cmd_daemon()
    elif args.status:
        exit_code = cmd_status()
    elif args.check:
        exit_code = cmd_check()
    else:
        # Default: run one check
        exit_code = cmd_check()

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
