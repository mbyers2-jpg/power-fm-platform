"""
Monitor Agent — Reporter
Health report generation in markdown and JSON formats.
"""

import os
import sys
import json
import logging
from datetime import datetime

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import get_all_agents, get_latest_health, get_open_incidents

REPORT_DIR = os.path.join(AGENT_DIR, 'reports')

log = logging.getLogger('monitor-agent')


def generate_report(conn, results=None, disk_ok=None, free_gb=None):
    """Generate a markdown health report and save to reports/."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'health_{today}.md')

    agents = get_all_agents(conn)
    incidents = get_open_incidents(conn)

    lines = []
    lines.append(f"# PTC Agent Health Report")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Summary counts
    healthy = degraded = down = unknown = 0
    for agent in agents:
        status = agent['status'] or 'unknown'
        if status == 'healthy':
            healthy += 1
        elif status == 'degraded':
            degraded += 1
        elif status == 'down':
            down += 1
        else:
            unknown += 1

    lines.append("## Summary")
    lines.append(f"- **Total agents:** {len(agents)}")
    lines.append(f"- **Healthy:** {healthy}")
    lines.append(f"- **Degraded:** {degraded}")
    lines.append(f"- **Down:** {down}")
    lines.append(f"- **Unknown:** {unknown}")
    if free_gb is not None:
        lines.append(f"- **Disk space:** {free_gb:.1f}GB free {'(LOW!)' if not disk_ok else ''}")
    lines.append("")

    # Agent status table
    lines.append("## Agent Status")
    lines.append("")
    lines.append("| Agent | PID | Python | HTTP | DB | Logs | Status |")
    lines.append("|-------|-----|--------|------|----|------|--------|")

    for agent in agents:
        name = agent['name']
        health = get_latest_health(conn, name)
        if health:
            pid = 'UP' if health['pid_alive'] else ('DOWN' if health['pid_alive'] is not None else '-')
            python = 'OK' if health['python_ok'] else ('FAIL' if health['python_ok'] is not None else '-')
            http = 'OK' if health['http_ok'] else ('FAIL' if health['http_ok'] is not None else '-')
            db = 'OK' if health['db_ok'] else ('FAIL' if health['db_ok'] is not None else '-')
            logs = 'OK' if health['log_fresh'] else ('STALE' if health['log_fresh'] is not None else '-')
            status = health['overall_status'].upper()
        else:
            pid = python = http = db = logs = '-'
            status = 'NO DATA'

        lines.append(f"| {name} | {pid} | {python} | {http} | {db} | {logs} | {status} |")

    lines.append("")

    # Open incidents
    if incidents:
        lines.append("## Open Incidents")
        lines.append("")
        lines.append("| Agent | Type | Started | Description |")
        lines.append("|-------|------|---------|-------------|")
        for inc in incidents:
            lines.append(
                f"| {inc['agent_name']} | {inc['incident_type']} | "
                f"{inc['started_at'][:16]} | {inc['description'] or '-'} |"
            )
        lines.append("")
    else:
        lines.append("## Open Incidents")
        lines.append("")
        lines.append("No open incidents.")
        lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Health report generated: {report_path}")
    return report_path


def generate_json_report(conn, results=None, disk_ok=None, free_gb=None):
    """Generate a JSON health report."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'health_{today}.json')

    agents = get_all_agents(conn)
    incidents = get_open_incidents(conn)

    data = {
        'generated': datetime.now().isoformat(),
        'disk_free_gb': round(free_gb, 1) if free_gb else None,
        'disk_ok': disk_ok,
        'agents': [],
        'incidents': []
    }

    for agent in agents:
        name = agent['name']
        health = get_latest_health(conn, name)
        entry = {
            'name': name,
            'status': agent['status'],
            'http_port': agent['http_port'],
        }
        if health:
            entry.update({
                'last_check': health['check_time'],
                'pid_alive': bool(health['pid_alive']) if health['pid_alive'] is not None else None,
                'python_ok': bool(health['python_ok']) if health['python_ok'] is not None else None,
                'http_ok': bool(health['http_ok']) if health['http_ok'] is not None else None,
                'http_status': health['http_status'],
                'db_ok': bool(health['db_ok']) if health['db_ok'] is not None else None,
                'log_fresh': bool(health['log_fresh']) if health['log_fresh'] is not None else None,
                'overall_status': health['overall_status'],
            })
        data['agents'].append(entry)

    for inc in incidents:
        data['incidents'].append({
            'agent': inc['agent_name'],
            'type': inc['incident_type'],
            'started': inc['started_at'],
            'description': inc['description'],
        })

    with open(report_path, 'w') as f:
        json.dump(data, f, indent=2)

    log.info(f"JSON report generated: {report_path}")
    return report_path


def format_incidents(incidents):
    """Format open incidents for CLI display."""
    if not incidents:
        return "\n  No open incidents.\n"

    lines = ["", "  OPEN INCIDENTS", "  " + "-" * 80]
    lines.append(f"  {'Agent':<28} {'Type':<16} {'Started':<20} {'Description'}")
    lines.append("  " + "-" * 80)

    for inc in incidents:
        lines.append(
            f"  {inc['agent_name']:<28} {inc['incident_type']:<16} "
            f"{inc['started_at'][:19]:<20} {inc['description'] or '-'}"
        )

    lines.append(f"\n  Total: {len(incidents)} open incident(s)\n")
    return '\n'.join(lines)
