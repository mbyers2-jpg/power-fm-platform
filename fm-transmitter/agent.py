#!/usr/bin/env python3
"""
FM Transmitter Fleet Manager — Agent
Manages a fleet of Raspberry Pi FM relay nodes that pull Icecast streams
and rebroadcast on local FM frequencies.

Usage:
    venv/bin/python agent.py --scan            # Check all node health
    venv/bin/python agent.py --list-nodes      # List registered nodes
    venv/bin/python agent.py --report          # Generate fleet report
    venv/bin/python agent.py --add-node ...    # Register a new node
    venv/bin/python agent.py --remove-node ID  # Remove a node
    venv/bin/python agent.py --daemon          # Run continuously
"""

import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime, timedelta

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import (
    get_connection, get_all_nodes, get_node, upsert_node, remove_node,
    record_heartbeat, get_latest_heartbeat, get_fleet_stats,
    create_alert, resolve_alerts_by_type, get_active_alerts, get_recent_alerts,
    get_agent_state, set_agent_state,
)
from api_client import check_fleet_health, check_node_health, load_config

# --- Configuration ---
POLL_INTERVAL = 60  # seconds
LOG_DIR = os.path.join(AGENT_DIR, 'logs')
REPORT_DIR = os.path.join(AGENT_DIR, 'reports')

# --- Logging ---
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('fm-transmitter')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def _format_ago(iso_timestamp):
    """Format an ISO timestamp as 'X min ago'."""
    if not iso_timestamp:
        return 'never'
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        delta = datetime.utcnow() - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return 'just now'
        elif minutes < 60:
            return f"{minutes} min ago"
        elif minutes < 1440:
            return f"{minutes // 60} hr ago"
        else:
            return f"{minutes // 1440} days ago"
    except (ValueError, TypeError):
        return 'unknown'


def scan_nodes(conn):
    """Check health of all nodes and generate alerts."""
    config = load_config()
    timeout = config.get('heartbeat_timeout_seconds', 180)
    nodes = get_all_nodes(conn)

    if not nodes:
        log.info("No nodes registered.")
        return []

    results = []
    for node in nodes:
        health = check_node_health(conn, node['node_id'])
        results.append(health)

        node_id = node['node_id']

        # Check for offline nodes
        hb = get_latest_heartbeat(conn, node_id)
        if hb:
            try:
                hb_time = datetime.fromisoformat(hb['timestamp'])
                age = (datetime.utcnow() - hb_time).total_seconds()

                if age > timeout:
                    from database import update_node_status
                    update_node_status(conn, node_id, 'offline')
                    create_alert(conn, 'node_offline', 'critical',
                                 f"Node {node['name']} ({node_id}) offline — no heartbeat for {int(age)}s",
                                 node_id=node_id)
                else:
                    resolve_alerts_by_type(conn, 'node_offline', node_id=node_id)

                # Stream disconnected alert
                if not hb['stream_connected']:
                    create_alert(conn, 'stream_disconnected', 'warning',
                                 f"Node {node['name']} ({node_id}) — stream disconnected",
                                 node_id=node_id)
                else:
                    resolve_alerts_by_type(conn, 'stream_disconnected', node_id=node_id)

                # Overheating alert
                if hb['cpu_temp'] is not None and hb['cpu_temp'] >= config.get('alert_thresholds', {}).get('cpu_temp_critical', 80.0):
                    create_alert(conn, 'overheating', 'critical',
                                 f"Node {node['name']} ({node_id}) — CPU temp {hb['cpu_temp']}°C",
                                 node_id=node_id)
                else:
                    resolve_alerts_by_type(conn, 'overheating', node_id=node_id)

            except (ValueError, TypeError):
                pass
        else:
            # No heartbeats at all — if node was expected to be online
            if node['status'] not in ('offline', 'new'):
                create_alert(conn, 'node_offline', 'warning',
                             f"Node {node['name']} ({node_id}) — no heartbeats received",
                             node_id=node_id)

    set_agent_state(conn, 'last_scan_timestamp', datetime.utcnow().isoformat())
    log.info(f"Scanned {len(results)} nodes.")
    return results


def list_nodes(conn):
    """Display all registered nodes."""
    nodes = get_all_nodes(conn)

    if not nodes:
        print("\nNo relay nodes registered.")
        print("Add one with: venv/bin/python agent.py --add-node --node-id <id> --name <name> --market <market> --frequency <freq>")
        return

    print(f"\n=== FM Relay Fleet — {len(nodes)} Node(s) ===\n")
    print(f"{'Node ID':<14} {'Name':<20} {'Market':<12} {'Freq':<8} {'Type':<12} {'Status':<10} {'Last HB'}")
    print("-" * 95)
    for n in nodes:
        freq = f"{n['fm_frequency']}" if n['fm_frequency'] else '-'
        last_hb = _format_ago(n['last_heartbeat'])
        print(f"  {n['node_id']:<12} {n['name']:<20} {n['market']:<12} {freq:<8} {n['transmitter_type']:<12} {n['status']:<10} {last_hb}")

    # Show active alerts
    alerts = get_active_alerts(conn)
    if alerts:
        print(f"\n--- Active Alerts ({len(alerts)}) ---")
        for a in alerts:
            node_name = a['node_name'] or 'fleet'
            print(f"  [{a['severity'].upper()}] {node_name}: {a['message']}")
    print()


def add_node_interactive(conn, args):
    """Register a new relay node."""
    node_id = args.node_id
    name = args.name or node_id
    market = args.market or 'national'
    frequency = args.frequency
    stream_url = args.stream_url
    transmitter_type = args.transmitter_type or 'simulated'

    if not node_id:
        print("Error: --node-id is required.")
        return

    existing = get_node(conn, node_id)
    if existing:
        print(f"Node '{node_id}' already exists. Updating...")

    upsert_node(conn, node_id, name, market=market, stream_url=stream_url,
                fm_frequency=frequency, transmitter_type=transmitter_type)

    print(f"Node registered: {node_id} ({name})")
    print(f"  Market: {market}")
    if frequency:
        print(f"  FM Frequency: {frequency} MHz")
    if stream_url:
        print(f"  Stream URL: {stream_url}")
    print(f"  Transmitter: {transmitter_type}")
    print(f"  Status: offline (waiting for first heartbeat)")


def remove_node_cmd(conn, node_id):
    """Remove a relay node."""
    node = get_node(conn, node_id)
    if not node:
        print(f"Node '{node_id}' not found.")
        return
    remove_node(conn, node_id)
    print(f"Removed node: {node_id} ({node['name']})")


def generate_report(conn):
    """Generate fleet status report."""
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'fm_fleet_{today}.md')

    stats = get_fleet_stats(conn)
    nodes = get_all_nodes(conn)
    alerts = get_active_alerts(conn)
    recent_alerts = get_recent_alerts(conn, limit=20)

    lines = [
        f"# FM Transmitter Fleet Report — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Fleet Overview",
        f"- Total nodes: **{stats['total_nodes']}**",
        f"- Online: **{stats['nodes_online']}** | Degraded: **{stats['nodes_degraded']}** | Offline: **{stats['nodes_offline']}**",
        f"- Active FM transmitters: **{stats['nodes_transmitting']}**",
        f"- Markets covered: **{stats['markets']}**",
        f"- Active alerts: **{stats['active_alerts']}** ({stats['critical_alerts']} critical)",
        "",
    ]

    if nodes:
        lines.extend([
            "## Node Status",
            "| Node ID | Name | Market | Frequency | Type | Status | Last Heartbeat |",
            "|---------|------|--------|-----------|------|--------|----------------|",
        ])
        for n in nodes:
            freq = f"{n['fm_frequency']} MHz" if n['fm_frequency'] else '-'
            last_hb = _format_ago(n['last_heartbeat'])
            status_icon = 'OK' if n['status'] == 'online' else ('!!' if n['status'] == 'degraded' else 'XX')
            lines.append(f"| {n['node_id']} | {n['name']} | {n['market']} | {freq} | {n['transmitter_type']} | {status_icon} | {last_hb} |")
        lines.append("")

    # Node health details
    for n in nodes:
        health = check_node_health(conn, n['node_id'])
        if health.get('heartbeat'):
            hb = health['heartbeat']
            lines.extend([
                f"### {n['name']} ({n['node_id']})",
                f"- Stream connected: {'Yes' if hb['stream_connected'] else 'No'}",
                f"- FM transmitting: {'Yes' if hb['fm_transmitting'] else 'No'}",
                f"- CPU temp: {hb['cpu_temp']}°C" if hb['cpu_temp'] is not None else "- CPU temp: N/A",
                f"- CPU usage: {hb['cpu_usage']}%" if hb['cpu_usage'] is not None else "- CPU usage: N/A",
                f"- Memory: {hb['memory_usage']}%" if hb['memory_usage'] is not None else "- Memory: N/A",
                f"- Uptime: {hb['uptime_seconds'] // 3600}h {(hb['uptime_seconds'] % 3600) // 60}m" if hb['uptime_seconds'] else "- Uptime: N/A",
                f"- Buffer health: {hb['buffer_health']:.2f}" if hb['buffer_health'] is not None else "- Buffer health: N/A",
                "",
            ])
            if health.get('issues'):
                lines.append(f"**Issues:**")
                for issue in health['issues']:
                    lines.append(f"- {issue}")
                lines.append("")

    if alerts:
        lines.extend(["## Active Alerts", ""])
        for a in alerts:
            node_name = a['node_name'] or 'fleet'
            lines.append(f"- **[{a['severity'].upper()}]** {node_name}: {a['message']} (since {a['created_at']})")
        lines.append("")

    if recent_alerts:
        lines.extend([
            "## Recent Alert History",
            "| Time | Severity | Node | Type | Message | Resolved |",
            "|------|----------|------|------|---------|----------|",
        ])
        for a in recent_alerts:
            node_name = a['node_name'] or 'fleet'
            resolved = a['resolved_at'] if a['resolved'] else 'No'
            lines.append(f"| {a['created_at']} | {a['severity']} | {node_name} | {a['alert_type']} | {a['message'][:50]} | {resolved} |")
        lines.append("")

    last_scan = get_agent_state(conn, 'last_scan_timestamp') or 'Never'
    lines.extend(["---", f"Last fleet scan: {last_scan}"])

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Fleet report generated: {report_path}")
    return report_path


def run_daemon(conn):
    """Continuous polling loop."""
    config = load_config()
    poll_interval = config.get('poll_interval', POLL_INTERVAL)
    log.info(f"FM Transmitter fleet manager starting in daemon mode (poll every {poll_interval}s)")

    # Initial scan
    scan_nodes(conn)
    generate_report(conn)

    cycle = 0
    while running:
        log.info(f"Sleeping {poll_interval}s until next scan...")
        for _ in range(poll_interval):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        scan_nodes(conn)
        cycle += 1

        # Generate report every 30 cycles (~30 min at 60s interval)
        if cycle % 30 == 0:
            generate_report(conn)
            set_agent_state(conn, 'last_report_timestamp', datetime.utcnow().isoformat())

    log.info("FM Transmitter fleet manager stopped.")


def main():
    parser = argparse.ArgumentParser(description='FM Transmitter Fleet Manager')
    parser.add_argument('--scan', action='store_true', help='Check all node health')
    parser.add_argument('--list-nodes', action='store_true', help='List registered nodes')
    parser.add_argument('--report', action='store_true', help='Generate fleet report')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')

    # Node management
    parser.add_argument('--add-node', action='store_true', help='Register a new relay node')
    parser.add_argument('--remove-node', type=str, metavar='NODE_ID', help='Remove a relay node')
    parser.add_argument('--node-id', type=str, help='Node ID for add-node')
    parser.add_argument('--name', type=str, help='Node name for add-node')
    parser.add_argument('--market', type=str, help='Market for add-node')
    parser.add_argument('--frequency', type=float, help='FM frequency for add-node')
    parser.add_argument('--stream-url', type=str, help='Icecast stream URL for add-node')
    parser.add_argument('--transmitter-type', type=str, choices=['simulated', 'rpitx', 'si4713'],
                        help='Transmitter backend type')

    args = parser.parse_args()

    log.info("Initializing FM Transmitter Fleet Manager...")
    conn = get_connection()

    if args.scan:
        results = scan_nodes(conn)
        for r in results:
            status = r['status']
            issues = ', '.join(r.get('issues', [])) or 'healthy'
            print(f"  {r['name']}: {status} — {issues}")
    elif args.list_nodes:
        list_nodes(conn)
    elif args.report:
        report = generate_report(conn)
        print(f"Report saved to: {report}")
    elif args.add_node:
        add_node_interactive(conn, args)
    elif args.remove_node:
        remove_node_cmd(conn, args.remove_node)
    elif args.daemon:
        run_daemon(conn)
    else:
        # Default: list nodes + scan
        list_nodes(conn)
        results = scan_nodes(conn)
        if results:
            print("--- Scan Results ---")
            for r in results:
                status = r['status']
                issues = ', '.join(r.get('issues', [])) or 'healthy'
                print(f"  {r['name']}: {status} — {issues}")

    conn.close()


if __name__ == '__main__':
    main()
