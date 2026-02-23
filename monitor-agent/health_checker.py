"""
Monitor Agent — Health Checker
All 6 check types: process alive, correct Python binary, HTTP endpoint,
database accessible, log freshness, disk space.
"""

import os
import sys
import plistlib
import sqlite3
import subprocess
import shutil
import logging
import time
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import (
    get_all_agents, record_health_check, update_agent_status,
    get_consecutive_failures, open_incident, resolve_incident
)

HOME = os.path.expanduser('~')
AGENTS_DIR = os.path.join(HOME, 'Agents')
HTTP_TIMEOUT = 3
DISK_WARN_GB = 5
LOG_STALE_MULTIPLIER = 2

log = logging.getLogger('monitor-agent')


class HealthResult:
    """Result of a health check for a single agent."""
    def __init__(self, agent_name):
        self.agent_name = agent_name
        self.pid_alive = None      # True/False/None
        self.pid = None            # int or None
        self.python_ok = None      # True/False/None
        self.python_detail = None  # string if bad
        self.http_ok = None        # True/False/None
        self.http_status = None    # int or None
        self.db_ok = None          # True/False/None
        self.log_fresh = None      # True/False/None
        self.overall_status = 'unknown'
        self.details = []
        self.needs_restart = False
        self.restart_reason = None

    def _bool_to_int(self, val):
        if val is None:
            return None
        return 1 if val else 0

    @property
    def pid_alive_int(self):
        return self._bool_to_int(self.pid_alive)

    @property
    def python_ok_int(self):
        return self._bool_to_int(self.python_ok)

    @property
    def http_ok_int(self):
        return self._bool_to_int(self.http_ok)

    @property
    def db_ok_int(self):
        return self._bool_to_int(self.db_ok)

    @property
    def log_fresh_int(self):
        return self._bool_to_int(self.log_fresh)


def _check_pid_files(name):
    """Check PID files for an agent. Returns (alive, pid) or (False, None)."""
    pid_file = os.path.join(AGENTS_DIR, name, f'{name}.pid')
    alt_pid = os.path.join(AGENTS_DIR, name, 'dashboard.pid')
    for pf in [pid_file, alt_pid]:
        if os.path.exists(pf):
            try:
                with open(pf, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)
                return True, pid
            except (ValueError, OSError, ProcessLookupError):
                pass
    return False, None


def _check_launchctl(name):
    """Check launchctl list for an agent. Returns (alive, pid) or (False, None)."""
    try:
        result = subprocess.run(
            ['/bin/launchctl', 'list'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f'com.marcbyers.{name}' in line:
                parts = line.split()
                pid_str = parts[0] if parts else '-'
                if pid_str != '-':
                    try:
                        pid = int(pid_str)
                        os.kill(pid, 0)
                        return True, pid
                    except (ValueError, OSError):
                        return False, None
                else:
                    return False, None
        return False, None
    except (subprocess.TimeoutExpired, Exception) as e:
        log.warning(f"launchctl check failed for {name}: {e}")
        return False, None


def _check_pgrep(name):
    """Fallback: use pgrep to find agent process by command line pattern.
    Catches agents launched with full path in args."""
    agent_dir = os.path.join(AGENTS_DIR, name)
    try:
        # Look for python processes with full path to this agent's agent.py
        result = subprocess.run(
            ['/usr/bin/pgrep', '-f', f'{agent_dir}/agent.py'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().splitlines()
            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    os.kill(pid, 0)
                    return True, pid
                except (ValueError, OSError):
                    continue
        return False, None
    except (subprocess.TimeoutExpired, Exception):
        return False, None


# Cache for PID-to-CWD mapping, rebuilt once per check cycle
_daemon_pid_map = {}  # {agent_dir_name: pid}
_daemon_pid_map_time = 0


def _build_daemon_pid_map():
    """Build a map of agent_name -> PID by finding all 'agent.py --daemon' processes
    and resolving their CWD via lsof. Called once per check cycle."""
    global _daemon_pid_map, _daemon_pid_map_time

    # Only rebuild if stale (>30s old)
    if time.time() - _daemon_pid_map_time < 30:
        return _daemon_pid_map

    _daemon_pid_map = {}
    try:
        # Find all PIDs running "agent.py --daemon"
        result = subprocess.run(
            ['/usr/bin/pgrep', '-f', 'agent.py --daemon'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            _daemon_pid_map_time = time.time()
            return _daemon_pid_map

        pids = [p.strip() for p in result.stdout.strip().splitlines() if p.strip()]
        if not pids:
            _daemon_pid_map_time = time.time()
            return _daemon_pid_map

        # Use lsof to get CWD for all these PIDs in one call
        pid_str = ','.join(pids)
        result = subprocess.run(
            ['/usr/sbin/lsof', '-a', '-d', 'cwd', '-p', pid_str, '-Fn'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            current_pid = None
            for line in result.stdout.splitlines():
                if line.startswith('p'):
                    try:
                        current_pid = int(line[1:])
                    except ValueError:
                        current_pid = None
                elif line.startswith('n') and current_pid:
                    cwd = line[1:]
                    # Extract agent name from CWD like /Users/marcbyers/Agents/sync-briefs-agent
                    if '/Agents/' in cwd:
                        agent_name = cwd.split('/Agents/')[-1].rstrip('/')
                        if agent_name and '/' not in agent_name:
                            _daemon_pid_map[agent_name] = current_pid

    except (subprocess.TimeoutExpired, Exception) as e:
        log.debug(f"Failed to build daemon PID map: {e}")

    _daemon_pid_map_time = time.time()
    return _daemon_pid_map


def _check_cwd_map(name):
    """Check the CWD-based daemon PID map for agents launched with relative paths.
    This catches agents where start.sh does 'cd $AGENT_DIR && python agent.py --daemon &'."""
    pid_map = _build_daemon_pid_map()
    pid = pid_map.get(name)
    if pid:
        try:
            os.kill(pid, 0)
            return True, pid
        except (OSError, ProcessLookupError):
            pass
    return False, None


def check_process_alive(agent):
    """Check if the agent's process is running.

    Uses a 3-tier detection strategy:
      1. launchctl list (for launchd-managed agents)
      2. PID file check (for background-process agents)
      3. pgrep fallback (catches agents regardless of management type)
    """
    name = agent['name']
    agent_type = agent['agent_type']

    # Primary check based on agent type
    if agent_type == 'pid_file':
        alive, pid = _check_pid_files(name)
        if alive:
            return True, pid
    else:
        alive, pid = _check_launchctl(name)
        if alive:
            return True, pid

    # Cross-check: launchd agents might also have PID files, and vice versa
    if agent_type != 'pid_file':
        alive, pid = _check_pid_files(name)
        if alive:
            return True, pid
    else:
        alive, pid = _check_launchctl(name)
        if alive:
            return True, pid

    # Fallback: pgrep for full-path invocations
    alive, pid = _check_pgrep(name)
    if alive:
        return True, pid

    # Final fallback: CWD-based detection for relative-path invocations
    # (agents launched with 'cd $DIR && python agent.py --daemon &')
    alive, pid = _check_cwd_map(name)
    if alive:
        return True, pid

    return False, None


def check_python_binary(agent):
    """Verify the agent's plist points to its venv Python, not system Python."""
    plist_path = agent['plist_path']
    venv_path = agent['venv_path']

    if not plist_path or not os.path.exists(plist_path):
        return None, None  # No plist to check

    try:
        with open(plist_path, 'rb') as f:
            plist = plistlib.load(f)

        prog_args = plist.get('ProgramArguments', [])
        if not prog_args:
            return None, "No ProgramArguments in plist"

        actual_python = prog_args[0]
        expected_venv = venv_path

        # Check if it's pointing to the correct venv python
        if expected_venv and os.path.normpath(actual_python) == os.path.normpath(expected_venv):
            return True, None

        # Check if it's a system python (the exact bug that triggered this agent)
        system_pythons = ['/usr/bin/python', '/usr/bin/python3',
                          '/usr/local/bin/python', '/usr/local/bin/python3']
        if actual_python in system_pythons:
            return False, f"Using system Python: {actual_python} (expected: {expected_venv})"

        # It's some other python — could be another venv or homebrew
        if 'venv/bin/python' in actual_python:
            return True, None  # It's a venv python, probably fine

        return False, f"Unexpected Python: {actual_python} (expected: {expected_venv})"

    except Exception as e:
        return None, f"Error reading plist: {e}"


def check_http_endpoint(agent):
    """Check if the agent's HTTP endpoint is responding."""
    port = agent['http_port']
    if not port:
        return None, None  # No HTTP endpoint

    url = f'http://localhost:{port}/'
    try:
        req = Request(url, method='GET')
        resp = urlopen(req, timeout=HTTP_TIMEOUT)
        status = resp.getcode()
        return True, status
    except URLError as e:
        reason = str(e.reason) if hasattr(e, 'reason') else str(e)
        return False, None
    except Exception as e:
        return False, None


def check_database(agent):
    """Attempt read-only open of the agent's SQLite DB."""
    db_path = agent['db_path']
    if not db_path or not os.path.exists(db_path):
        if db_path and os.path.exists(os.path.dirname(db_path)):
            return None  # data dir exists but no DB yet (hasn't run)
        return None  # No DB configured

    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=3)
        # Quick integrity check — just try to read
        conn.execute("SELECT 1")
        conn.close()
        return True
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower():
            return None  # Locked is transient, not a failure
        return False
    except Exception:
        return False


def check_log_freshness(agent):
    """Check if the agent's log file has been written to recently."""
    log_path = agent['log_path']
    poll_interval = agent['poll_interval_sec'] or 60

    if not log_path or not os.path.exists(log_path):
        return None  # No log file to check

    try:
        mtime = os.path.getmtime(log_path)
        age_seconds = time.time() - mtime
        threshold = poll_interval * LOG_STALE_MULTIPLIER

        # Cap minimum threshold at 5 minutes to avoid false positives
        threshold = max(threshold, 300)

        return age_seconds <= threshold
    except Exception:
        return None


def check_disk_space():
    """Check if disk has sufficient free space."""
    try:
        usage = shutil.disk_usage('/')
        free_gb = usage.free / (1024 ** 3)
        return free_gb >= DISK_WARN_GB, free_gb
    except Exception:
        return None, None


def check_agent(agent, conn):
    """Run all health checks for a single agent. Returns HealthResult."""
    name = agent['name']
    result = HealthResult(name)

    # 1. Process alive
    alive, pid = check_process_alive(agent)
    result.pid_alive = alive
    result.pid = pid

    # 2. Correct Python binary
    python_ok, python_detail = check_python_binary(agent)
    result.python_ok = python_ok
    result.python_detail = python_detail
    if python_ok is False:
        result.details.append(f"PYTHON: {python_detail}")

    # 3. HTTP endpoint
    http_ok, http_status = check_http_endpoint(agent)
    result.http_ok = http_ok
    result.http_status = http_status

    # 4. Database accessible
    db_ok = check_database(agent)
    result.db_ok = db_ok

    # 5. Log freshness
    log_fresh = check_log_freshness(agent)
    result.log_fresh = log_fresh

    # --- Determine overall status ---
    if result.pid_alive is False:
        result.overall_status = 'down'
        result.needs_restart = True
        result.restart_reason = 'process_down'
        result.details.append("Process not running")
    elif result.python_ok is False:
        result.overall_status = 'degraded'
        result.details.append("Wrong Python binary — needs manual fix")
    elif result.http_ok is False and result.pid_alive:
        # HTTP failing but main process alive — this is degraded, not down.
        # Many agents have separate dashboard processes; restarting the main
        # daemon won't fix a dashboard that isn't running or has a port conflict.
        result.overall_status = 'degraded'
        result.details.append("HTTP not responding (dashboard may need manual restart)")
    elif result.http_ok is False and not result.pid_alive:
        result.overall_status = 'down'
        result.needs_restart = True
        result.restart_reason = 'process_down'
        result.details.append("Process and HTTP both down")
    elif result.db_ok is False:
        result.overall_status = 'degraded'
        result.details.append("Database inaccessible")
    elif result.log_fresh is False:
        result.overall_status = 'degraded'
        result.details.append("Stale logs")
    elif result.pid_alive:
        result.overall_status = 'healthy'
    else:
        result.overall_status = 'unknown'

    # --- Record health check ---
    record_health_check(
        conn, name,
        result.pid_alive_int, result.python_ok_int,
        result.http_ok_int, result.http_status,
        result.db_ok_int, result.log_fresh_int,
        result.overall_status,
        '; '.join(result.details) if result.details else None
    )

    # --- Update agent status ---
    update_agent_status(conn, name, result.overall_status)

    # --- Manage incidents ---
    if result.pid_alive is False:
        open_incident(conn, name, 'down', 'Process not running')
    elif result.pid_alive:
        resolve_incident(conn, name, 'down')

    if result.python_ok is False:
        open_incident(conn, name, 'wrong_binary', python_detail or 'Wrong Python binary')

    if result.http_ok is False and agent['http_port']:
        open_incident(conn, name, 'degraded', 'HTTP endpoint not responding')
    elif result.http_ok and agent['http_port']:
        resolve_incident(conn, name, 'degraded')

    if result.db_ok is False:
        open_incident(conn, name, 'db_locked', 'Database inaccessible')
    elif result.db_ok:
        resolve_incident(conn, name, 'db_locked')

    if result.log_fresh is False:
        open_incident(conn, name, 'stale_log', 'Log file stale')
    elif result.log_fresh:
        resolve_incident(conn, name, 'stale_log')

    return result


def check_all_agents(conn):
    """Run health checks for all registered agents. Returns list of HealthResult."""
    agents = get_all_agents(conn)
    results = []

    # Also check disk space (system-wide, not per-agent)
    disk_ok, free_gb = check_disk_space()
    if disk_ok is False:
        open_incident(conn, 'system', 'disk_low',
                      f'Disk space low: {free_gb:.1f}GB free')
        log.warning(f"DISK LOW: {free_gb:.1f}GB free (threshold: {DISK_WARN_GB}GB)")
    elif disk_ok:
        resolve_incident(conn, 'system', 'disk_low')

    for agent in agents:
        try:
            result = check_agent(agent, conn)
            results.append(result)
        except Exception as e:
            log.error(f"Error checking {agent['name']}: {e}")
            # Create a minimal result
            r = HealthResult(agent['name'])
            r.overall_status = 'unknown'
            r.details.append(f"Check error: {e}")
            results.append(r)

    return results, disk_ok, free_gb


def format_status_table(results, disk_ok=None, free_gb=None):
    """Format health results as a colored status table."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = []
    lines.append("")
    lines.append("  PTC AGENT HEALTH MONITOR")
    lines.append(f"  {now}")
    lines.append("  " + "=" * 100)

    header = (f"  {'Agent':<28} {'PID':<7} {'Python':<8} {'HTTP':<7} "
              f"{'DB':<7} {'Logs':<7} {'Status':<12} {'Action'}")
    lines.append(header)
    lines.append("  " + "-" * 100)

    # Status indicators
    def _s(val, ok_str='OK', fail_str='FAIL', na_str='-'):
        if val is None:
            return na_str
        return ok_str if val else fail_str

    def _pid(result):
        if result.pid_alive is None:
            return '-'
        if result.pid_alive:
            return f'UP'
        return 'DOWN'

    def _status_color(status):
        colors = {
            'healthy': '\033[92m',   # green
            'degraded': '\033[93m',  # yellow
            'down': '\033[91m',      # red
            'unknown': '\033[90m',   # gray
        }
        reset = '\033[0m'
        color = colors.get(status, '')
        return f"{color}{status.upper():<12}{reset}"

    healthy = degraded = down = 0
    for r in results:
        pid_str = _pid(r)
        python_str = _s(r.python_ok)
        http_str = _s(r.http_ok)
        db_str = _s(r.db_ok)
        log_str = _s(r.log_fresh, 'OK', 'STALE')
        status_str = _status_color(r.overall_status)
        action = r.restart_reason or '; '.join(r.details[:1]) if r.details else '-'

        line = (f"  {r.agent_name:<28} {pid_str:<7} {python_str:<8} {http_str:<7} "
                f"{db_str:<7} {log_str:<7} {status_str} {action}")
        lines.append(line)

        if r.overall_status == 'healthy':
            healthy += 1
        elif r.overall_status == 'degraded':
            degraded += 1
        elif r.overall_status == 'down':
            down += 1

    lines.append("  " + "-" * 100)
    summary = f"  {healthy}/{len(results)} healthy"
    if degraded:
        summary += f" | {degraded} degraded"
    if down:
        summary += f" | {down} down"
    if disk_ok is not None and free_gb is not None:
        disk_str = f" | Disk: {free_gb:.1f}GB free"
        if not disk_ok:
            disk_str += " \033[91m(LOW!)\033[0m"
        summary += disk_str
    lines.append(summary)
    lines.append("")

    return '\n'.join(lines)
