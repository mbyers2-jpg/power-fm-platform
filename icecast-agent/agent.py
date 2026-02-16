#!/usr/bin/env python3
"""
Icecast Agent — Power FM Transmitter Network Monitor
Monitors and manages streaming servers that feed FM transmitters.
Tracks listener counts, mount points, source connections, and stream health.

Usage:
    venv/bin/python agent.py                # Show status of all servers
    venv/bin/python agent.py --status       # Show status of all servers
    venv/bin/python agent.py --listeners    # Show listener counts
    venv/bin/python agent.py --health       # Run health check
    venv/bin/python agent.py --add-server   # Add a server interactively
    venv/bin/python agent.py --report       # Generate transmitter network report
    venv/bin/python agent.py --stream       # Start Power FM stream server (port 8000)
    venv/bin/python agent.py --stations     # Show all station status
    venv/bin/python agent.py --start-all    # Start all 9 market stations
    venv/bin/python agent.py --daemon       # Run continuously (poll every 60s)
"""

import sys
import os
import signal
import time
import json
import logging
import argparse
from datetime import datetime, timedelta

from database import (
    get_connection, get_all_servers, get_server, update_server_status,
    upsert_server, upsert_mount_point, get_mount_points,
    record_listeners, get_total_listeners, get_listener_history,
    record_health, get_latest_health,
    create_alert, resolve_alerts_by_type, get_active_alerts, get_recent_alerts,
    get_network_stats, get_agent_state, set_agent_state,
    record_source_connection,
)
from api_client import IcecastClient, CONFIG_PATH

# --- Configuration ---
POLL_INTERVAL = 60  # seconds — streaming servers need frequent checks
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')
CONFIG_DIR = os.path.join(os.path.dirname(__file__), 'config')

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('icecast-agent')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


# --- Core Functions ---

def poll_servers(client, conn):
    """
    Poll all configured servers, update database with current state,
    record listener counts, check health, and generate alerts.
    """
    if not client.has_servers():
        log.warning("No servers configured. Add servers to config/icecast_servers.json")
        return 0

    results = client.get_all_status()
    servers_online = 0

    for server_config, stats, health in results:
        name = server_config.get('name', server_config.get('host', 'unknown'))
        host = server_config.get('host', 'unknown')
        port = server_config.get('port', 8000)
        server_type = server_config.get('type', 'icecast')

        # Determine server status
        if health['reachable']:
            status = 'online'
            servers_online += 1
        else:
            status = 'offline'

        # Upsert the server record
        admin_url = f"http://{host}:{port}/admin/"
        version = health.get('version')
        server_id = upsert_server(
            conn, name, host, port,
            server_type=server_type,
            admin_url=admin_url,
            version=version,
            status=status
        )

        # Handle server offline alert
        if status == 'offline':
            create_alert(
                conn, 'server_offline', 'critical',
                f"Server {name} ({host}:{port}) is unreachable",
                server_id=server_id
            )
            log.warning(f"Server OFFLINE: {name} ({host}:{port})")
            continue
        else:
            # Resolve any previous offline alerts for this server
            resolve_alerts_by_type(conn, 'server_offline', server_id=server_id)

        # Process mount points
        if stats and stats.get('mounts'):
            for mount_data in stats['mounts']:
                mount_name = mount_data['mount_name']
                listeners_current = mount_data.get('listeners_current', 0)
                listeners_peak = mount_data.get('listeners_peak', 0)

                mount_id = upsert_mount_point(
                    conn, server_id, mount_name,
                    stream_title=mount_data.get('stream_title'),
                    genre=mount_data.get('genre'),
                    bitrate=mount_data.get('bitrate'),
                    sample_rate=mount_data.get('sample_rate'),
                    channels=mount_data.get('channels'),
                    content_type=mount_data.get('content_type'),
                    listeners_current=listeners_current,
                    listeners_peak=listeners_peak,
                    connected_since=mount_data.get('connected_since'),
                    status='active'
                )

                # Record listener count snapshot
                record_listeners(conn, mount_id, listeners_current, listeners_peak)

                # Record health data
                latency = stats.get('latency_ms', 0)
                bitrate = mount_data.get('bitrate')
                record_health(
                    conn, mount_id,
                    is_live=True,
                    bitrate_actual=bitrate,
                    latency_ms=latency,
                    errors=None
                )

                # Resolve any previous mount-down alerts
                resolve_alerts_by_type(conn, 'mount_down', server_id=server_id, mount_id=mount_id)

                # Check for low bitrate
                min_bitrate = client.thresholds.get('min_bitrate', 64)
                if bitrate is not None and bitrate < min_bitrate:
                    create_alert(
                        conn, 'low_bitrate', 'warning',
                        f"Mount {mount_name} on {name}: bitrate {bitrate}kbps below minimum {min_bitrate}kbps",
                        server_id=server_id, mount_id=mount_id
                    )
                else:
                    resolve_alerts_by_type(conn, 'low_bitrate', server_id=server_id, mount_id=mount_id)

                # Record source connection info if available
                source_ip = mount_data.get('source_ip')
                user_agent = mount_data.get('user_agent')
                if source_ip:
                    record_source_connection(
                        conn, server_id, mount_name,
                        source_ip=source_ip,
                        user_agent=user_agent or '',
                        status='active'
                    )

                log.info(f"  Mount {mount_name}: {listeners_current} listeners, {bitrate}kbps")

        # Check latency threshold
        latency = stats.get('latency_ms', 0) if stats else 0
        max_latency = client.thresholds.get('max_latency_ms', 5000)
        if latency > max_latency:
            create_alert(
                conn, 'high_latency', 'warning',
                f"Server {name}: response time {latency}ms exceeds {max_latency}ms threshold",
                server_id=server_id
            )
        else:
            resolve_alerts_by_type(conn, 'high_latency', server_id=server_id)

        # Log health errors
        for error in health.get('errors', []):
            log.warning(f"  Health issue on {name}: {error}")

    # Mark mount points as inactive if their server was online but mount wasn't reported
    # (This handles mounts that have been removed from the server)
    all_mounts = get_mount_points(conn)
    for mount in all_mounts:
        if mount['status'] == 'active':
            # Check if this mount was updated in this poll cycle
            updated = mount['updated_at']
            if updated:
                try:
                    update_time = datetime.fromisoformat(updated)
                    # If mount wasn't updated in last 2 poll intervals, mark inactive
                    if (datetime.utcnow() - update_time).total_seconds() > POLL_INTERVAL * 2:
                        from database import get_connection as _gc
                        conn.execute(
                            "UPDATE mount_points SET status = 'inactive', updated_at = ? WHERE id = ?",
                            (datetime.utcnow().isoformat(), mount['id'])
                        )
                        conn.commit()
                        create_alert(
                            conn, 'mount_down', 'warning',
                            f"Mount {mount['mount_name']} on {mount['server_name']} is no longer active",
                            server_id=mount['server_id'], mount_id=mount['id']
                        )
                except (ValueError, TypeError):
                    pass

    set_agent_state(conn, 'last_poll_timestamp', datetime.utcnow().isoformat())
    log.info(f"Poll complete: {servers_online}/{len(results)} servers online")
    return servers_online


def show_status(conn, client):
    """Display current status of all servers and mount points."""
    if not client.has_servers():
        print("\nNo servers configured.")
        print("Add servers to config/icecast_servers.json to get started.")
        print("See SETUP.md for instructions.\n")
        return

    print("\n=== Power FM Transmitter Network Status ===\n")

    # Poll fresh data
    poll_servers(client, conn)

    servers = get_all_servers(conn)
    if not servers:
        print("No server data in database. Run a poll cycle first.\n")
        return

    # Server table
    print(f"{'Server':<25} {'Host':<30} {'Type':<10} {'Status':<10} {'Version':<25}")
    print("-" * 100)
    for s in servers:
        print(f"{s['name']:<25} {s['host']}:{s['port']:<24} {s['server_type']:<10} {s['status']:<10} {s['version'] or 'N/A':<25}")

    print()

    # Mount points
    mounts = get_mount_points(conn)
    if mounts:
        print(f"{'Mount':<25} {'Server':<20} {'Bitrate':<10} {'Listeners':<12} {'Peak':<8} {'Status':<10}")
        print("-" * 85)
        for m in mounts:
            bitrate = f"{m['bitrate']}kbps" if m['bitrate'] else 'N/A'
            print(f"{m['mount_name']:<25} {m['server_name']:<20} {bitrate:<10} {m['listeners_current']:<12} {m['listeners_peak']:<8} {m['status']:<10}")

    # Summary
    total, peak = get_total_listeners(conn)
    active_mounts = sum(1 for m in mounts if m['status'] == 'active')
    print(f"\nTotal listeners: {total} | Peak: {peak} | Active mounts: {active_mounts}/{len(mounts)}")

    # Alerts
    alerts = get_active_alerts(conn)
    if alerts:
        print(f"\nActive Alerts ({len(alerts)}):")
        for a in alerts:
            severity_tag = a['severity'].upper()
            server_name = a['server_name'] or 'N/A'
            print(f"  [{severity_tag}] {server_name}: {a['message']}")

    print()


def show_listeners(conn, client):
    """Display current listener counts across all servers."""
    if not client.has_servers():
        print("\nNo servers configured. Add servers to config/icecast_servers.json\n")
        return

    print("\n=== Listener Counts ===\n")

    # Poll fresh data
    poll_servers(client, conn)

    mounts = get_mount_points(conn)
    if not mounts:
        print("No mount point data available.\n")
        return

    total = 0
    peak_total = 0

    print(f"{'Mount':<25} {'Server':<20} {'Current':<10} {'Peak':<10} {'Title':<30}")
    print("-" * 95)

    for m in mounts:
        if m['status'] != 'active':
            continue
        current = m['listeners_current'] or 0
        peak = m['listeners_peak'] or 0
        total += current
        peak_total += peak
        title = (m['stream_title'] or '')[:30]
        print(f"{m['mount_name']:<25} {m['server_name']:<20} {current:<10} {peak:<10} {title:<30}")

    print("-" * 95)
    print(f"{'TOTAL':<45} {total:<10} {peak_total:<10}")
    print()


def run_health_check(conn, client):
    """Run health checks on all configured servers."""
    if not client.has_servers():
        print("\nNo servers configured. Add servers to config/icecast_servers.json\n")
        return

    print("\n=== Health Check ===\n")

    for server in client.servers:
        health = client.check_health(server)
        name = health['name']
        status_str = "OK" if health['reachable'] else "UNREACHABLE"

        print(f"Server: {name} ({health['server']})")
        print(f"  Type:      {health['type']}")
        print(f"  Status:    {status_str}")
        print(f"  Latency:   {health['latency_ms']}ms")
        print(f"  Version:   {health['version'] or 'N/A'}")
        print(f"  Mounts:    {health['mount_count']}")
        print(f"  Listeners: {health['total_listeners']}")

        if health['errors']:
            print(f"  Issues:")
            for err in health['errors']:
                print(f"    - {err}")
        else:
            print(f"  Issues:    None")

        print()


def add_server_interactive(conn, args):
    """Add a server via interactive prompts or CLI flags."""
    # Check if flags were provided
    if args.host:
        host = args.host
        port = args.port or 8000
        name = args.name or host
        admin_user = args.user or 'admin'
        admin_password = args.password or ''
        server_type = args.type or 'icecast'
    else:
        # Interactive mode
        print("\n=== Add Streaming Server ===\n")
        name = input("Server name (e.g., Power FM Primary): ").strip()
        if not name:
            print("Server name is required.")
            return

        host = input("Hostname or IP (e.g., stream.powerfm.com): ").strip()
        if not host:
            print("Host is required.")
            return

        port_str = input("Port [8000]: ").strip()
        port = int(port_str) if port_str else 8000

        server_type = input("Type (icecast/shoutcast) [icecast]: ").strip() or 'icecast'
        admin_user = input("Admin username [admin]: ").strip() or 'admin'
        admin_password = input("Admin password: ").strip()

    # Load existing config or create new
    config_path = CONFIG_PATH
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        config = {
            "servers": [],
            "poll_interval": 60,
            "alert_thresholds": {
                "min_listeners": 0,
                "max_latency_ms": 5000,
                "min_bitrate": 64
            }
        }

    # Check for duplicate
    for s in config['servers']:
        if s.get('host') == host and s.get('port') == port:
            print(f"Server {host}:{port} already exists in config.")
            return

    new_server = {
        "name": name,
        "host": host,
        "port": port,
        "admin_user": admin_user,
        "admin_password": admin_password,
        "type": server_type
    }

    config['servers'].append(new_server)

    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)

    # Also add to database
    admin_url = f"http://{host}:{port}/admin/"
    upsert_server(conn, name, host, port, server_type=server_type, admin_url=admin_url)

    print(f"\nServer added: {name} ({host}:{port})")
    print(f"Config saved to: {config_path}")
    print(f"Run 'venv/bin/python agent.py --health' to test connectivity.\n")


def generate_report(conn, client):
    """Generate a transmitter network status report."""
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'transmitter_network_{today}.md')

    # Poll fresh data if we have servers
    if client.has_servers():
        poll_servers(client, conn)

    stats = get_network_stats(conn)
    servers = get_all_servers(conn)
    mounts = get_mount_points(conn)
    health_data = get_latest_health(conn)
    alerts = get_recent_alerts(conn, limit=30)
    active_alerts = get_active_alerts(conn)

    lines = [
        f"# Transmitter Network Status — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # Server Status
    lines.append("## Server Status")
    if servers:
        lines.append("| Server | Host | Type | Status | Version | Mounts |")
        lines.append("|--------|------|------|--------|---------|--------|")
        for s in servers:
            mount_count = sum(1 for m in mounts if m['server_id'] == s['id'])
            version = s['version'] or 'N/A'
            lines.append(
                f"| {s['name']} | {s['host']}:{s['port']} | {s['server_type']} | {s['status']} | {version} | {mount_count} |"
            )
    else:
        lines.append("No servers registered.")
    lines.append("")

    # Mount Points
    lines.append("## Mount Points")
    if mounts:
        lines.append("| Mount | Server | Bitrate | Listeners | Peak | Status |")
        lines.append("|-------|--------|---------|-----------|------|--------|")
        for m in mounts:
            bitrate = f"{m['bitrate']}kbps" if m['bitrate'] else 'N/A'
            lines.append(
                f"| {m['mount_name']} | {m['server_name']} | {bitrate} | {m['listeners_current']} | {m['listeners_peak']} | {m['status']} |"
            )
    else:
        lines.append("No mount points registered.")
    lines.append("")

    # Listener Summary
    total_listeners = stats.get('total_listeners', 0)
    today_peak = stats.get('today_peak_listeners', 0)
    active_mounts = stats.get('active_mounts', 0)
    total_mounts = stats.get('total_mounts', 0)

    lines.append("## Listener Summary")
    lines.append(f"- Total listeners: {total_listeners}")
    lines.append(f"- Peak listeners (today): {today_peak}")
    lines.append(f"- Active mount points: {active_mounts} / {total_mounts}")
    lines.append("")

    # Stream Health
    lines.append("## Stream Health")
    if health_data:
        lines.append("| Mount | Latency | Bitrate | Buffer | Status |")
        lines.append("|-------|---------|---------|--------|--------|")
        for h in health_data:
            latency = f"{h['latency_ms']}ms" if h['latency_ms'] is not None else 'N/A'
            bitrate = f"{h['bitrate_actual']}kbps" if h['bitrate_actual'] is not None else 'N/A'
            buffer_sz = f"{h['buffer_size']}" if h['buffer_size'] is not None else 'N/A'
            status_str = "LIVE" if h['is_live'] else "DOWN"
            errors = h['errors'] or ''
            if errors:
                status_str = f"{status_str} ({errors})"
            lines.append(
                f"| {h['mount_name']} | {latency} | {bitrate} | {buffer_sz} | {status_str} |"
            )
    else:
        lines.append("No health data recorded yet.")
    lines.append("")

    # Alerts
    lines.append("## Alerts")
    if alerts:
        lines.append("| Time | Server | Severity | Message | Resolved |")
        lines.append("|------|--------|----------|---------|----------|")
        for a in alerts:
            time_str = a['created_at'][:16] if a['created_at'] else 'N/A'
            server_name = a['server_name'] or 'N/A'
            resolved_str = "Yes" if a['resolved'] else "No"
            if a['resolved'] and a['resolved_at']:
                resolved_str = f"Yes ({a['resolved_at'][:16]})"
            lines.append(
                f"| {time_str} | {server_name} | {a['severity']} | {a['message']} | {resolved_str} |"
            )
    else:
        lines.append("No alerts recorded.")
    lines.append("")

    # Uptime
    servers_online = stats.get('servers_online', 0)
    total_servers = stats.get('total_servers', 0)
    if total_servers > 0:
        uptime_pct = round((servers_online / total_servers) * 100, 1)
    else:
        uptime_pct = 0

    last_poll = get_agent_state(conn, 'last_poll_timestamp') or 'Never'
    if last_poll != 'Never':
        last_poll = last_poll[:16]

    lines.append("## Uptime")
    lines.append(f"- Servers online: {servers_online} / {total_servers}")
    lines.append(f"- Average uptime: {uptime_pct}%")
    lines.append(f"- Last health check: {last_poll}")
    lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Report generated: {report_path}")
    return report_path


def run_daemon(conn, client):
    """Continuous polling loop for monitoring streaming servers."""
    poll_interval = client.poll_interval or POLL_INTERVAL
    log.info(f"Icecast agent starting in daemon mode (polling every {poll_interval}s)")

    if not client.has_servers():
        log.warning("No servers configured. Daemon will wait for config/icecast_servers.json")
        log.warning("Add servers and they will be picked up on next cycle.")

    # Initial poll
    if client.has_servers():
        poll_servers(client, conn)
        generate_report(conn, client)

    cycle_count = 0

    while running:
        log.info(f"Sleeping {poll_interval}s until next poll...")
        for _ in range(poll_interval):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        # Reload config each cycle to pick up new servers
        client.reload_config()

        if client.has_servers():
            poll_servers(client, conn)

        cycle_count += 1

        # Generate report every 30 minutes (every 30 cycles at 60s interval)
        if cycle_count % 30 == 0:
            generate_report(conn, client)

    log.info("Icecast agent stopped.")


def main():
    parser = argparse.ArgumentParser(
        description='Power FM Icecast Agent — Transmitter Network Monitor'
    )
    parser.add_argument('--status', action='store_true',
                        help='Show status of all servers and mount points')
    parser.add_argument('--listeners', action='store_true',
                        help='Show current listener counts across all servers')
    parser.add_argument('--health', action='store_true',
                        help='Run health check on all servers')
    parser.add_argument('--add-server', action='store_true',
                        help='Add a new streaming server')
    parser.add_argument('--report', action='store_true',
                        help='Generate transmitter_network report')
    parser.add_argument('--stream', action='store_true',
                        help='Start the Power FM stream server (port 8000)')
    parser.add_argument('--playlist', type=str, default=None,
                        help='M3U playlist path (for --stream)')
    parser.add_argument('--stations', action='store_true',
                        help='Show multi-station network status')
    parser.add_argument('--start-all', action='store_true', dest='start_all',
                        help='Start all 9 market stations')
    parser.add_argument('--stop-all', action='store_true', dest='stop_all',
                        help='Stop all market stations')
    parser.add_argument('--daemon', action='store_true',
                        help='Run continuously (poll every 60 seconds)')

    # Flags for --add-server non-interactive mode
    parser.add_argument('--name', type=str, help='Server name (for --add-server)')
    parser.add_argument('--host', type=str, help='Server hostname (for --add-server)')
    parser.add_argument('--port', type=int, help='Server port (for --add-server)')
    parser.add_argument('--user', type=str, help='Admin username (for --add-server)')
    parser.add_argument('--password', type=str, help='Admin password (for --add-server)')
    parser.add_argument('--type', type=str, choices=['icecast', 'shoutcast'],
                        help='Server type (for --add-server)')

    args = parser.parse_args()

    log.info("Initializing icecast agent...")
    conn = get_connection()
    client = IcecastClient()

    if args.add_server:
        add_server_interactive(conn, args)
    elif args.status:
        show_status(conn, client)
    elif args.listeners:
        show_listeners(conn, client)
    elif args.health:
        run_health_check(conn, client)
        # Also run stream health monitor check on all 9 Power FM stations
        from health_monitor import run_check, print_summary
        results = run_check(allow_restart=True)
        print_summary(results)
    elif args.report:
        report_path = generate_report(conn, client)
        print(f"\nReport saved to: {report_path}")
        stats = get_network_stats(conn)
        print(f"Servers: {stats['servers_online']}/{stats['total_servers']} online")
        print(f"Mount points: {stats['active_mounts']}/{stats['total_mounts']} active")
        print(f"Total listeners: {stats['total_listeners']}")
        alerts_count = stats['active_alerts']
        critical_count = stats['critical_alerts']
        if alerts_count:
            print(f"Active alerts: {alerts_count} ({critical_count} critical)")
    elif args.stream:
        conn.close()
        from stream_server import start_server
        playlist = args.playlist
        port = args.port or 8000
        name = args.name or 'Power FM'
        start_server(playlist_path=playlist or '', port=port, station_name=name)
        return
    elif args.stations:
        conn.close()
        from stations import show_status
        show_status()
        return
    elif args.start_all:
        conn.close()
        from stations import start_all
        count = start_all()
        print(f"\n  Started {count}/9 stations\n")
        from stations import show_status
        show_status()
        return
    elif args.stop_all:
        conn.close()
        from stations import stop_all
        count = stop_all()
        print(f"\n  Stopped {count} stations\n")
        return
    elif args.daemon:
        run_daemon(conn, client)
    else:
        # Default: show status
        show_status(conn, client)

    conn.close()


if __name__ == '__main__':
    main()
