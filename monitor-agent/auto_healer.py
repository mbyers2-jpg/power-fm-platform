"""
Monitor Agent — Auto Healer
Restart logic with safeguards and restart window tracking.
Max 3 restarts per agent per 5-minute window.
"""

import os
import sys
import subprocess
import logging

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import get_restarts_in_window, record_restart, resolve_incident

HOME = os.path.expanduser('~')
AGENTS_DIR = os.path.join(HOME, 'Agents')
MAX_RESTARTS = 3
RESTART_WINDOW = 300  # 5 minutes

log = logging.getLogger('monitor-agent')


def can_restart(conn, agent_name):
    """Check if we're allowed to restart this agent (within rate limit)."""
    recent = get_restarts_in_window(conn, agent_name, RESTART_WINDOW)
    return recent < MAX_RESTARTS, recent


def restart_via_start_sh(agent_name):
    """Restart an agent by running its start.sh script."""
    start_script = os.path.join(AGENTS_DIR, agent_name, 'start.sh')
    if not os.path.exists(start_script):
        return False, f"start.sh not found at {start_script}"

    try:
        result = subprocess.run(
            ['bash', start_script],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.join(AGENTS_DIR, agent_name)
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, result.stderr.strip() or f"Exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "start.sh timed out after 30s"
    except Exception as e:
        return False, str(e)


def restart_via_launchctl(agent_name):
    """Restart an agent by unloading/loading its launchd plist."""
    plist_path = os.path.join(HOME, 'Library', 'LaunchAgents',
                              f'com.marcbyers.{agent_name}.plist')
    if not os.path.exists(plist_path):
        return False, f"Plist not found at {plist_path}"

    try:
        # Unload first
        subprocess.run(
            ['launchctl', 'unload', plist_path],
            capture_output=True, text=True, timeout=10
        )
        # Load
        result = subprocess.run(
            ['launchctl', 'load', plist_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, "Reloaded via launchctl"
        else:
            return False, result.stderr.strip() or f"Exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "launchctl timed out"
    except Exception as e:
        return False, str(e)


def attempt_restart(conn, agent_name, reason=None):
    """Attempt to restart an agent with safeguards.

    Returns (attempted, success, message).
    """
    allowed, recent_count = can_restart(conn, agent_name)
    if not allowed:
        msg = (f"Restart suppressed for {agent_name}: "
               f"{recent_count}/{MAX_RESTARTS} restarts in last {RESTART_WINDOW}s")
        log.warning(msg)
        return False, False, msg

    log.info(f"Attempting restart for {agent_name} "
             f"({recent_count + 1}/{MAX_RESTARTS}) reason: {reason}")

    # Try start.sh first (it handles plist copy + launchctl)
    success, msg = restart_via_start_sh(agent_name)
    method = 'start_sh'

    if not success:
        # Fallback to direct launchctl
        log.info(f"start.sh failed for {agent_name}, trying launchctl directly...")
        success, msg = restart_via_launchctl(agent_name)
        method = 'launchctl'

    # Record the attempt
    record_restart(conn, agent_name, method, success, None if success else msg)

    if success:
        log.info(f"Successfully restarted {agent_name} via {method}")
        resolve_incident(conn, agent_name, 'down')
    else:
        log.error(f"Failed to restart {agent_name}: {msg}")

    return True, success, msg


def heal_results(conn, results):
    """Process health check results and attempt auto-healing.

    Returns list of (agent_name, attempted, success, message) tuples.
    """
    actions = []

    for result in results:
        if not result.needs_restart:
            continue

        name = result.agent_name

        # Never auto-restart the monitor-agent itself
        if name == 'monitor-agent':
            log.info("Skipping self-restart for monitor-agent")
            continue

        # Don't auto-fix wrong Python binary — flag for manual review
        if result.python_ok is False:
            log.critical(f"CRITICAL: {name} has wrong Python binary — needs manual fix!")
            actions.append((name, False, False, "Wrong Python binary — manual fix required"))
            continue

        attempted, success, msg = attempt_restart(conn, name, result.restart_reason)
        actions.append((name, attempted, success, msg))

    return actions


def format_heal_actions(actions):
    """Format healing actions for display."""
    if not actions:
        return ""

    lines = ["", "  AUTO-HEAL ACTIONS:", "  " + "-" * 60]
    for name, attempted, success, msg in actions:
        if not attempted:
            status = "\033[93mSKIPPED\033[0m"
        elif success:
            status = "\033[92mRESTARTED\033[0m"
        else:
            status = "\033[91mFAILED\033[0m"
        lines.append(f"  {name:<28} {status}  {msg}")
    lines.append("")
    return '\n'.join(lines)
