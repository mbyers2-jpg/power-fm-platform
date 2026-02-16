#!/usr/bin/env python3
"""
Power FM Multi-Station Manager

Manages multiple Power FM station streams, each with its own port,
market-specific station IDs, and branding.

Usage:
    venv/bin/python stations.py                    # List all stations
    venv/bin/python stations.py --start all        # Start all stations
    venv/bin/python stations.py --start la         # Start Power 106 LA
    venv/bin/python stations.py --start la,nyc     # Start multiple
    venv/bin/python stations.py --stop all         # Stop all stations
    venv/bin/python stations.py --status           # Show running stations
"""

import os
import sys
import json
import signal
import logging
import argparse
import subprocess
import time
from datetime import datetime

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
ELEVENLABS_OUTPUT = os.path.expanduser('~/Agents/elevenlabs-agent/output')
PLAYLISTS_DIR = os.path.expanduser('~/Agents/platform-hub/playlists')
PID_DIR = os.path.join(AGENT_DIR, 'pids')
LOG_DIR = os.path.join(AGENT_DIR, 'logs')

os.makedirs(PID_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'stations.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('stations')

# Station definitions — each gets its own port and market-specific branding
STATIONS = {
    'national': {
        'name': 'Power FM',
        'port': 8000,
        'market': 'National',
        'station_id_pattern': 'Youre_listening_to_Power_FM_2026',
        'fallback_patterns': ['Power_FM_The_culture'],
        'description': 'Power FM — The Culture. The Music. The Movement.',
    },
    'la': {
        'name': 'Power 106 LA',
        'port': 8001,
        'market': 'Los Angeles',
        'station_id_pattern': 'Power_106_LA',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power 106 Los Angeles',
    },
    'nyc': {
        'name': 'Power 105.1 NYC',
        'port': 8002,
        'market': 'New York',
        'station_id_pattern': 'Power_1051_New_York',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power 105.1 New York City',
    },
    'chicago': {
        'name': 'Power 92 Chicago',
        'port': 8003,
        'market': 'Chicago',
        'station_id_pattern': 'Power_92_Chicago',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power 92 Chicago',
    },
    'miami': {
        'name': 'Power 96 Miami',
        'port': 8004,
        'market': 'Miami',
        'station_id_pattern': 'Power_96_Miami',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power 96 Miami',
    },
    'atlanta': {
        'name': 'Power 107.5 Atlanta',
        'port': 8005,
        'market': 'Atlanta',
        'station_id_pattern': 'Power_1075_Atlanta',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power 107.5 Atlanta',
    },
    'houston': {
        'name': 'Power 104 Houston',
        'port': 8006,
        'market': 'Houston',
        'station_id_pattern': 'Power_104_Houston',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power 104 Houston',
    },
    'london': {
        'name': 'Power FM London',
        'port': 8007,
        'market': 'London',
        'station_id_pattern': 'Power_FM_London',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power FM London',
    },
    'lagos': {
        'name': 'Power FM Lagos',
        'port': 8008,
        'market': 'Lagos',
        'station_id_pattern': 'Power_FM_Lagos',
        'fallback_patterns': ['Power_FM'],
        'description': 'Power FM Lagos',
    },
}


def load_custom_stations():
    """Load custom stations from config/custom_stations.json."""
    config_path = os.path.join(AGENT_DIR, 'config', 'custom_stations.json')
    if not os.path.isfile(config_path):
        return {}
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# Merge custom stations into the STATIONS dict at module load time
STATIONS.update(load_custom_stations())


def get_station_ids(station):
    """Find ElevenLabs audio files matching this station's ID pattern."""
    ids = []
    if not os.path.isdir(ELEVENLABS_OUTPUT):
        return ids

    pattern = station['station_id_pattern']
    fallbacks = station.get('fallback_patterns', [])

    for fname in os.listdir(ELEVENLABS_OUTPUT):
        if not fname.endswith('.mp3'):
            continue
        if pattern in fname:
            ids.append(os.path.join(ELEVENLABS_OUTPUT, fname))

    # If no market-specific IDs, use fallbacks
    if not ids:
        for fb in fallbacks:
            for fname in os.listdir(ELEVENLABS_OUTPUT):
                if fname.endswith('.mp3') and fb in fname:
                    ids.append(os.path.join(ELEVENLABS_OUTPUT, fname))
            if ids:
                break

    return ids


def get_market_playlist(station_key):
    """Find the most recent market-specific playlist for a station."""
    if not os.path.isdir(PLAYLISTS_DIR):
        return None

    # Try market-specific playlist first (e.g. power_fm_la_2026-02-16.m3u)
    prefix = f'power_fm_{station_key}_'
    market_playlists = [
        os.path.join(PLAYLISTS_DIR, f)
        for f in os.listdir(PLAYLISTS_DIR)
        if f.startswith(prefix) and f.endswith('.m3u')
    ]
    if market_playlists:
        return sorted(market_playlists)[-1]

    # Fall back to hourly playlist
    hourly = [
        os.path.join(PLAYLISTS_DIR, f)
        for f in os.listdir(PLAYLISTS_DIR)
        if f.startswith('power_fm_hourly_') and f.endswith('.m3u')
    ]
    if hourly:
        return sorted(hourly)[-1]

    # Fall back to top25
    top25 = [
        os.path.join(PLAYLISTS_DIR, f)
        for f in os.listdir(PLAYLISTS_DIR)
        if f.startswith('power_fm_top25_') and f.endswith('.m3u')
    ]
    if top25:
        return sorted(top25)[-1]

    return None


def get_pid_file(station_key):
    return os.path.join(PID_DIR, f'{station_key}.pid')


def is_running(station_key):
    """Check if a station process is running."""
    pid_file = get_pid_file(station_key)
    if not os.path.isfile(pid_file):
        return False

    with open(pid_file, 'r') as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, 0)  # Signal 0 = check if alive
        return True
    except (OSError, ProcessLookupError):
        os.remove(pid_file)
        return False


def start_station(station_key):
    """Start a single station stream."""
    if station_key not in STATIONS:
        log.error(f"Unknown station: {station_key}")
        return False

    if is_running(station_key):
        log.info(f"{station_key}: already running")
        return True

    station = STATIONS[station_key]
    playlist = get_market_playlist(station_key)

    if not playlist:
        log.error(f"{station_key}: no playlist available")
        return False

    log.info(f"Starting {station['name']} on port {station['port']}...")

    # Launch stream_server.py as a subprocess
    server_script = os.path.join(AGENT_DIR, 'stream_server.py')
    venv_python = os.path.join(AGENT_DIR, 'venv', 'bin', 'python')
    log_file = os.path.join(LOG_DIR, f'station_{station_key}.log')

    with open(log_file, 'a') as lf:
        proc = subprocess.Popen(
            [venv_python, server_script,
             '--port', str(station['port']),
             '--name', station['name'],
             '--playlist', playlist],
            stdout=lf,
            stderr=lf,
            start_new_session=True,
        )

    # Save PID
    with open(get_pid_file(station_key), 'w') as f:
        f.write(str(proc.pid))

    log.info(f"  {station['name']} started (PID {proc.pid}, port {station['port']})")
    return True


def stop_station(station_key):
    """Stop a single station stream."""
    pid_file = get_pid_file(station_key)
    if not os.path.isfile(pid_file):
        return False

    with open(pid_file, 'r') as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, signal.SIGTERM)
        log.info(f"Stopped {station_key} (PID {pid})")
    except (OSError, ProcessLookupError):
        log.info(f"{station_key} was not running")

    os.remove(pid_file)
    return True


def start_all():
    """Start all stations."""
    started = 0
    for key in STATIONS:
        if start_station(key):
            started += 1
        time.sleep(0.5)  # Stagger startups
    return started


def stop_all():
    """Stop all stations."""
    stopped = 0
    for key in STATIONS:
        if stop_station(key):
            stopped += 1
    return stopped


def show_status():
    """Show status of all stations."""
    print("\n  POWER FM STATION NETWORK")
    print("  " + "=" * 70)
    print(f"  {'Station':<25} {'Market':<15} {'Port':<8} {'Status':<10} {'Stream URL'}")
    print("  " + "-" * 70)

    running_count = 0
    for key, station in STATIONS.items():
        status = "LIVE" if is_running(key) else "OFF"
        if status == "LIVE":
            running_count += 1

        status_display = f"\033[92m● {status}\033[0m" if status == "LIVE" else f"\033[90m○ {status}\033[0m"
        url = f"http://localhost:{station['port']}/stream"

        print(f"  {station['name']:<25} {station['market']:<15} {station['port']:<8} {status_display:<20} {url}")

    print("  " + "-" * 70)
    print(f"  {running_count}/{len(STATIONS)} stations live")

    if running_count > 0:
        print(f"\n  Status pages:")
        for key, station in STATIONS.items():
            if is_running(key):
                print(f"    {station['name']}: http://localhost:{station['port']}/status")

    print()


def show_station_ids():
    """Show which station IDs are available for each market."""
    print("\n  STATION ID INVENTORY")
    print("  " + "=" * 60)

    for key, station in STATIONS.items():
        ids = get_station_ids(station)
        count = len(ids)
        status = f"\033[92m{count} files\033[0m" if count > 0 else f"\033[91m0 files\033[0m"
        print(f"  {station['name']:<25} {status}")
        for f in ids:
            print(f"    → {os.path.basename(f)[:70]}")

    print()


def main():
    parser = argparse.ArgumentParser(description='Power FM Multi-Station Manager')
    parser.add_argument('--start', type=str, metavar='STATIONS',
                        help='Start stations (comma-separated keys, or "all")')
    parser.add_argument('--stop', type=str, metavar='STATIONS',
                        help='Stop stations (comma-separated keys, or "all")')
    parser.add_argument('--status', action='store_true',
                        help='Show station status')
    parser.add_argument('--ids', action='store_true',
                        help='Show station ID inventory')
    parser.add_argument('--list', action='store_true',
                        help='List all configured stations')
    args = parser.parse_args()

    if args.start:
        if args.start == 'all':
            count = start_all()
            print(f"\n  Started {count}/{len(STATIONS)} stations\n")
        else:
            keys = [k.strip() for k in args.start.split(',')]
            for k in keys:
                start_station(k)

    elif args.stop:
        if args.stop == 'all':
            count = stop_all()
            print(f"\n  Stopped {count} stations\n")
        else:
            keys = [k.strip() for k in args.stop.split(',')]
            for k in keys:
                stop_station(k)

    elif args.ids:
        show_station_ids()

    elif args.status or args.list or len(sys.argv) == 1:
        show_status()


if __name__ == '__main__':
    main()
