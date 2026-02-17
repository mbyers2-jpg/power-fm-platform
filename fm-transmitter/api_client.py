"""
FM Transmitter Fleet Manager — API Client
Reads local fm_transmitter.db and provides fleet status methods.
Also handles heartbeat processing from Pi relay nodes.
"""

import json
import os
from datetime import datetime, timedelta
from database import get_connection, get_all_nodes, get_node, get_latest_heartbeat, get_fleet_stats

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'fm_transmitter.json')

# Default config
DEFAULT_CONFIG = {
    "heartbeat_timeout_seconds": 180,
    "heartbeat_warning_seconds": 120,
    "max_cpu_temp": 80.0,
    "max_cpu_usage": 90.0,
    "poll_interval": 60,
    "alert_thresholds": {
        "cpu_temp_warning": 70.0,
        "cpu_temp_critical": 80.0,
        "memory_warning": 85.0,
        "buffer_health_min": 0.5
    }
}


def load_config():
    """Load fleet configuration from JSON file."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        # Merge with defaults for any missing keys
        merged = DEFAULT_CONFIG.copy()
        merged.update(config)
        return merged
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save fleet configuration to JSON file."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


def check_node_health(conn, node_id):
    """Check health of a specific node. Returns status dict."""
    config = load_config()
    node = get_node(conn, node_id)
    if not node:
        return {'status': 'unknown', 'error': f'Node {node_id} not found'}

    hb = get_latest_heartbeat(conn, node_id)
    result = {
        'node_id': node_id,
        'name': node['name'],
        'market': node['market'],
        'fm_frequency': node['fm_frequency'],
        'transmitter_type': node['transmitter_type'],
        'status': node['status'],
        'ip_address': node['ip_address'],
        'issues': [],
    }

    if not hb:
        result['status'] = 'no_data'
        result['issues'].append('No heartbeats received')
        return result

    # Check heartbeat freshness
    try:
        hb_time = datetime.fromisoformat(hb['timestamp'])
        age_seconds = (datetime.utcnow() - hb_time).total_seconds()
        result['last_heartbeat_age'] = age_seconds

        if age_seconds > config['heartbeat_timeout_seconds']:
            result['status'] = 'offline'
            result['issues'].append(f'No heartbeat for {int(age_seconds)}s (timeout: {config["heartbeat_timeout_seconds"]}s)')
        elif age_seconds > config['heartbeat_warning_seconds']:
            result['status'] = 'degraded'
            result['issues'].append(f'Heartbeat delayed: {int(age_seconds)}s')
    except (ValueError, TypeError):
        result['issues'].append('Invalid heartbeat timestamp')

    # Check stream connectivity
    if not hb['stream_connected']:
        result['issues'].append('Stream disconnected')
        if result['status'] == 'online':
            result['status'] = 'degraded'

    # Check FM transmission
    if not hb['fm_transmitting']:
        result['issues'].append('FM not transmitting')

    # Check CPU temperature
    thresholds = config.get('alert_thresholds', {})
    if hb['cpu_temp'] is not None:
        if hb['cpu_temp'] >= thresholds.get('cpu_temp_critical', 80.0):
            result['issues'].append(f'CPU temp critical: {hb["cpu_temp"]}°C')
        elif hb['cpu_temp'] >= thresholds.get('cpu_temp_warning', 70.0):
            result['issues'].append(f'CPU temp warning: {hb["cpu_temp"]}°C')

    # Check memory usage
    if hb['memory_usage'] is not None:
        if hb['memory_usage'] >= thresholds.get('memory_warning', 85.0):
            result['issues'].append(f'Memory usage high: {hb["memory_usage"]:.1f}%')

    # Check buffer health
    if hb['buffer_health'] is not None:
        if hb['buffer_health'] < thresholds.get('buffer_health_min', 0.5):
            result['issues'].append(f'Buffer health low: {hb["buffer_health"]:.2f}')

    # Attach latest heartbeat data
    result['heartbeat'] = {
        'timestamp': hb['timestamp'],
        'stream_connected': bool(hb['stream_connected']),
        'fm_transmitting': bool(hb['fm_transmitting']),
        'cpu_temp': hb['cpu_temp'],
        'cpu_usage': hb['cpu_usage'],
        'memory_usage': hb['memory_usage'],
        'uptime_seconds': hb['uptime_seconds'],
        'buffer_health': hb['buffer_health'],
        'audio_level': hb['audio_level'],
    }

    return result


def check_fleet_health(conn):
    """Check health of all nodes in the fleet."""
    nodes = get_all_nodes(conn)
    results = []
    for node in nodes:
        health = check_node_health(conn, node['node_id'])
        results.append(health)
    return results


def get_fleet_summary(conn):
    """Get a high-level fleet summary combining stats and health."""
    stats = get_fleet_stats(conn)
    fleet_health = check_fleet_health(conn)

    issues = []
    for node in fleet_health:
        for issue in node.get('issues', []):
            issues.append(f"{node['name']}: {issue}")

    return {
        'stats': stats,
        'nodes': fleet_health,
        'total_issues': len(issues),
        'issues': issues,
    }
