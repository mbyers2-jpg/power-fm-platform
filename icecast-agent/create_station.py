#!/usr/bin/env python3
"""
Power FM White-Label Station Builder

Spin up a new Power FM market station with one command.
Generates station config, ElevenLabs station IDs, market playlist,
and starts the stream.

Usage:
    venv/bin/python create_station.py --city "Dallas" --freq "103.5" --port 8009
    venv/bin/python create_station.py --city "Toronto" --freq "99.1" --port 8010 --tagline "The six. The sound."
    venv/bin/python create_station.py --list    # Show all stations including custom ones
    venv/bin/python create_station.py --remove dallas   # Remove a custom station
"""

import os
import sys
import re
import json
import socket
import logging
import argparse
import subprocess
import time
from datetime import datetime

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(AGENT_DIR, 'config')
CUSTOM_STATIONS_FILE = os.path.join(CONFIG_DIR, 'custom_stations.json')
ELEVENLABS_AGENT_DIR = os.path.expanduser('~/Agents/elevenlabs-agent')
ELEVENLABS_OUTPUT = os.path.join(ELEVENLABS_AGENT_DIR, 'output')
PLAYLISTS_DIR = os.path.expanduser('~/Agents/platform-hub/playlists')
PID_DIR = os.path.join(AGENT_DIR, 'pids')
LOG_DIR = os.path.join(AGENT_DIR, 'logs')

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(PID_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'create_station.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('create-station')

# Voices to use for station ID generation (pick 2 per station)
STATION_ID_VOICES = ['Brian', 'Adam', 'Charlie', 'Eric', 'Daniel', 'George']


def load_custom_stations():
    """Load custom stations from config/custom_stations.json."""
    if not os.path.isfile(CUSTOM_STATIONS_FILE):
        return {}
    try:
        with open(CUSTOM_STATIONS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.warning(f"Failed to load custom stations: {e}")
        return {}


def save_custom_stations(stations):
    """Save custom stations to config/custom_stations.json."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CUSTOM_STATIONS_FILE, 'w') as f:
        json.dump(stations, f, indent=4)
    log.info(f"Saved {len(stations)} custom station(s) to {CUSTOM_STATIONS_FILE}")


def generate_station_key(city):
    """Generate a station key from city name: lowercase, no spaces, alphanumeric only."""
    key = re.sub(r'[^a-zA-Z0-9]', '', city.lower())
    return key


def generate_station_id_pattern(name):
    """
    Generate the station ID pattern used for matching ElevenLabs audio files.
    E.g., "Power 103.5 Dallas" -> "Power_1035_Dallas"
    """
    # Remove periods from frequency numbers
    cleaned = re.sub(r'(\d+)\.(\d+)', r'\1\2', name)
    # Replace spaces with underscores
    pattern = re.sub(r'\s+', '_', cleaned.strip())
    # Remove anything that isn't alphanumeric or underscore
    pattern = re.sub(r'[^a-zA-Z0-9_]', '', pattern)
    return pattern


def is_port_in_use(port):
    """Check if a TCP port is currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('', port))
            return False
        except OSError:
            return True


def get_hardcoded_stations():
    """Load the STATIONS dict from stations.py without triggering its custom station merge."""
    # We import the hardcoded stations by reading the known keys
    stations = {
        'national': {'name': 'Power FM', 'port': 8000, 'market': 'National'},
        'la': {'name': 'Power 106 LA', 'port': 8001, 'market': 'Los Angeles'},
        'nyc': {'name': 'Power 105.1 NYC', 'port': 8002, 'market': 'New York'},
        'chicago': {'name': 'Power 92 Chicago', 'port': 8003, 'market': 'Chicago'},
        'miami': {'name': 'Power 96 Miami', 'port': 8004, 'market': 'Miami'},
        'atlanta': {'name': 'Power 107.5 Atlanta', 'port': 8005, 'market': 'Atlanta'},
        'houston': {'name': 'Power 104 Houston', 'port': 8006, 'market': 'Houston'},
        'london': {'name': 'Power FM London', 'port': 8007, 'market': 'London'},
        'lagos': {'name': 'Power FM Lagos', 'port': 8008, 'market': 'Lagos'},
    }
    return stations


def get_all_stations():
    """Get all stations: hardcoded + custom."""
    stations = get_hardcoded_stations()
    custom = load_custom_stations()
    stations.update(custom)
    return stations


def check_conflicts(station_key, port):
    """Check for station key and port conflicts. Returns list of error messages."""
    errors = []
    all_stations = get_all_stations()

    # Check if key already exists
    if station_key in all_stations:
        existing = all_stations[station_key]
        errors.append(
            f"Station key '{station_key}' already exists: "
            f"{existing['name']} on port {existing['port']}"
        )

    # Check if port is already assigned to another station
    for key, station in all_stations.items():
        if station['port'] == port:
            errors.append(
                f"Port {port} already assigned to '{key}' ({station['name']})"
            )
            break

    # Check if port is actively in use by another process
    if is_port_in_use(port):
        errors.append(f"Port {port} is currently in use by another process")

    return errors


def generate_station_ids(station_name, station_id_pattern, tagline=None):
    """
    Generate 2 ElevenLabs station ID audio files using different voices.

    Returns list of generated file paths, or empty list if ElevenLabs is unavailable.
    """
    generated_files = []

    # Add elevenlabs-agent to sys.path for imports
    if ELEVENLABS_AGENT_DIR not in sys.path:
        sys.path.insert(0, ELEVENLABS_AGENT_DIR)

    try:
        from api_client import ElevenLabsClient, ElevenLabsConfigError
        from database import get_connection as el_get_connection
    except ImportError as e:
        log.warning(f"Cannot import ElevenLabs modules: {e}")
        print(f"\n  WARNING: ElevenLabs agent not available ({e})")
        print("  Skipping station ID audio generation.")
        print("  You can generate them later with the elevenlabs-agent.")
        return generated_files

    # Initialize client
    try:
        client = ElevenLabsClient()
    except ElevenLabsConfigError as e:
        log.warning(f"ElevenLabs not configured: {e}")
        print(f"\n  WARNING: ElevenLabs API not configured.")
        print("  Skipping station ID audio generation.")
        print("  Set up ~/Agents/elevenlabs-agent/config/elevenlabs_config.json first.")
        return generated_files
    except Exception as e:
        log.warning(f"ElevenLabs client error: {e}")
        print(f"\n  WARNING: ElevenLabs client error: {e}")
        print("  Skipping station ID audio generation.")
        return generated_files

    # Get database connection
    try:
        conn = el_get_connection()
    except Exception as e:
        log.warning(f"ElevenLabs database error: {e}")
        print(f"\n  WARNING: ElevenLabs database error: {e}")
        print("  Skipping station ID audio generation.")
        return generated_files

    # Import generation functions from elevenlabs agent
    try:
        # We need resolve_voice and the generation machinery
        sys.path.insert(0, ELEVENLABS_AGENT_DIR)
        from agent import resolve_voice, safe_filename, sync_voices
        from database import save_generation, log_usage, update_generation_status
    except ImportError as e:
        log.warning(f"Cannot import ElevenLabs agent functions: {e}")
        print(f"\n  WARNING: Cannot import ElevenLabs agent functions: {e}")
        print("  Skipping station ID audio generation.")
        return generated_files

    os.makedirs(ELEVENLABS_OUTPUT, exist_ok=True)

    # Build the texts to generate
    texts = [
        f"You're listening to {station_name}",
    ]
    if tagline:
        texts.append(f"{station_name}. {tagline}")
    else:
        texts.append(f"This is {station_name}. The culture. The music. The movement.")

    # Pick 2 different voices
    import random
    rng = random.Random(hash(station_name) & 0xFFFFFFFF)
    selected_voices = rng.sample(STATION_ID_VOICES, min(2, len(STATION_ID_VOICES)))

    # Sync voices so they can be resolved
    try:
        sync_voices(client, conn)
    except Exception as e:
        log.warning(f"Voice sync failed: {e}")

    for i, (text, voice_name) in enumerate(zip(texts, selected_voices)):
        log.info(f"Generating station ID {i + 1}/2: voice={voice_name}, text={text[:60]}...")
        print(f"\n  Generating station ID {i + 1}/2...")
        print(f"    Voice: {voice_name}")
        print(f"    Text: {text}")

        voice_id, resolved_name = resolve_voice(client, conn, voice_name)
        if not voice_id:
            log.warning(f"Voice '{voice_name}' not found, skipping")
            print(f"    WARNING: Voice '{voice_name}' not found, skipping")
            continue

        model = 'eleven_multilingual_v2'
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # Use station_id_pattern in the filename for matching
        filename = f"{safe_filename(resolved_name)}_{station_id_pattern}_{timestamp}.mp3"
        output_path = os.path.join(ELEVENLABS_OUTPUT, filename)

        gen_data = {
            'voice_id': voice_id,
            'text': text,
            'model_id': model,
            'character_count': len(text),
            'status': 'pending',
        }
        gen_id = save_generation(conn, gen_data)

        try:
            audio_bytes = client.generate_audio(text, voice_id, model_id=model)
        except Exception as e:
            log.error(f"Generation failed for voice {voice_name}: {e}")
            update_generation_status(conn, gen_id, 'failed')
            print(f"    ERROR: Generation failed: {e}")
            continue

        with open(output_path, 'wb') as f:
            f.write(audio_bytes)

        estimated_duration = round(len(audio_bytes) / 16000, 1)
        update_generation_status(conn, gen_id, 'completed', output_path, estimated_duration)
        log_usage(conn, len(text), 1)

        generated_files.append(output_path)
        log.info(f"Station ID saved: {output_path} ({estimated_duration}s)")
        print(f"    Saved: {os.path.basename(output_path)} ({estimated_duration}s)")

    # Remove elevenlabs-agent from sys.path to avoid pollution
    if ELEVENLABS_AGENT_DIR in sys.path:
        sys.path.remove(ELEVENLABS_AGENT_DIR)

    return generated_files


def generate_market_playlist_for_station(station_key):
    """
    Generate or copy a market playlist for the new station.

    Tries to generate a proper market playlist via platform-hub.
    If that fails, copies the most recent national/hourly playlist
    with the station's branding.

    Returns the path to the playlist, or None.
    """
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    playlist_filename = f"power_fm_{station_key}_{today}.m3u"
    playlist_path = os.path.join(PLAYLISTS_DIR, playlist_filename)

    # If a playlist already exists for this market today, use it
    if os.path.isfile(playlist_path):
        log.info(f"Playlist already exists: {playlist_path}")
        return playlist_path

    # Try to find an existing national or hourly playlist to adapt
    source_playlist = None
    for prefix in ['power_fm_national_', 'power_fm_hourly_', 'power_fm_top25_']:
        candidates = []
        if os.path.isdir(PLAYLISTS_DIR):
            for fname in os.listdir(PLAYLISTS_DIR):
                if fname.startswith(prefix) and fname.endswith('.m3u'):
                    candidates.append(os.path.join(PLAYLISTS_DIR, fname))
        if candidates:
            source_playlist = sorted(candidates)[-1]
            break

    if not source_playlist:
        log.warning(f"No source playlist found to adapt for '{station_key}'")
        print(f"  WARNING: No existing playlist found to adapt.")
        print(f"  Generate playlists with: cd ~/Agents/platform-hub && venv/bin/python agent.py --playlists")
        return None

    # Read the source playlist and adapt station IDs
    custom = load_custom_stations()
    station_config = custom.get(station_key, {})
    station_id_pattern = station_config.get('station_id_pattern', '')

    with open(source_playlist, 'r') as f:
        content = f.read()

    # Update the playlist header
    market_display = station_config.get('market', station_key.title())
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        if line.startswith('#PLAYLIST:'):
            new_lines.append(f"#PLAYLIST:Power FM {market_display} - Market Playlist - {today}")
        else:
            new_lines.append(line)

    # Look for market-specific station ID files
    station_ids = []
    if os.path.isdir(ELEVENLABS_OUTPUT) and station_id_pattern:
        for fname in os.listdir(ELEVENLABS_OUTPUT):
            if fname.endswith('.mp3') and station_id_pattern in fname:
                station_ids.append(os.path.join(ELEVENLABS_OUTPUT, fname))

    # Replace generic station ID entries with market-specific ones if available
    if station_ids:
        import random
        rng = random.Random(hash(f"{station_key}_{today}") & 0xFFFFFFFF)
        final_lines = []
        for line in new_lines:
            if '[STATION ID]' in line:
                # This is an EXTINF line for a station ID — replace the next line too
                sid = rng.choice(station_ids)
                sid_name = os.path.basename(sid).replace('_', ' ').rsplit('.', 1)[0][:60]
                final_lines.append(f"#EXTINF:-1,[STATION ID] {sid_name}")
            elif any(line.strip() == os.path.join(ELEVENLABS_OUTPUT, fname)
                     for fname in (os.listdir(ELEVENLABS_OUTPUT) if os.path.isdir(ELEVENLABS_OUTPUT) else [])
                     if 'Power_FM' in fname or 'listening_to_Power' in fname):
                # This is a station ID file path line — replace with market-specific
                sid = rng.choice(station_ids)
                final_lines.append(sid)
            else:
                final_lines.append(line)
        new_lines = final_lines

    with open(playlist_path, 'w') as f:
        f.write('\n'.join(new_lines))

    log.info(f"Market playlist created: {playlist_path} (adapted from {os.path.basename(source_playlist)})")
    return playlist_path


def start_station(station_key, station_config):
    """Start the station stream using stream_server.py."""
    server_script = os.path.join(AGENT_DIR, 'stream_server.py')
    venv_python = os.path.join(AGENT_DIR, 'venv', 'bin', 'python')

    if not os.path.isfile(venv_python):
        log.warning(f"Python venv not found at {venv_python}")
        print(f"  WARNING: venv not found. Start manually with:")
        print(f"    cd {AGENT_DIR} && venv/bin/python stations.py --start {station_key}")
        return False

    # Find the playlist
    playlist = None
    today = datetime.now().strftime('%Y-%m-%d')
    market_playlist = os.path.join(PLAYLISTS_DIR, f"power_fm_{station_key}_{today}.m3u")
    if os.path.isfile(market_playlist):
        playlist = market_playlist

    if not playlist:
        # Try any existing playlist for this market
        if os.path.isdir(PLAYLISTS_DIR):
            prefix = f"power_fm_{station_key}_"
            candidates = [
                os.path.join(PLAYLISTS_DIR, f)
                for f in os.listdir(PLAYLISTS_DIR)
                if f.startswith(prefix) and f.endswith('.m3u')
            ]
            if candidates:
                playlist = sorted(candidates)[-1]

    if not playlist:
        # Fall back to national/hourly
        for prefix in ['power_fm_national_', 'power_fm_hourly_', 'power_fm_top25_']:
            candidates = []
            if os.path.isdir(PLAYLISTS_DIR):
                for f in os.listdir(PLAYLISTS_DIR):
                    if f.startswith(prefix) and f.endswith('.m3u'):
                        candidates.append(os.path.join(PLAYLISTS_DIR, f))
            if candidates:
                playlist = sorted(candidates)[-1]
                break

    if not playlist:
        log.warning(f"No playlist available for {station_key}")
        print(f"  WARNING: No playlist found. Station not started.")
        print(f"  Generate playlists first, then start with:")
        print(f"    venv/bin/python stations.py --start {station_key}")
        return False

    port = station_config['port']
    name = station_config['name']
    log_file = os.path.join(LOG_DIR, f'station_{station_key}.log')

    log.info(f"Starting {name} on port {port} with playlist {os.path.basename(playlist)}...")

    with open(log_file, 'a') as lf:
        proc = subprocess.Popen(
            [venv_python, server_script,
             '--port', str(port),
             '--name', name,
             '--playlist', playlist],
            stdout=lf,
            stderr=lf,
            start_new_session=True,
        )

    # Save PID
    pid_file = os.path.join(PID_DIR, f'{station_key}.pid')
    with open(pid_file, 'w') as f:
        f.write(str(proc.pid))

    log.info(f"  {name} started (PID {proc.pid}, port {port})")
    return True


def create_station(city, freq, port, tagline=None, no_start=False, no_audio=False):
    """
    Create a new Power FM market station.

    Args:
        city: City name (e.g., "Dallas")
        freq: Frequency string (e.g., "103.5")
        port: Port number for the stream
        tagline: Optional custom tagline
        no_start: If True, don't start the stream after creation
        no_audio: If True, skip ElevenLabs station ID generation

    Returns:
        True if station was created successfully, False otherwise.
    """
    station_key = generate_station_key(city)
    station_name = f"Power {freq} {city}"
    station_id_pattern = generate_station_id_pattern(station_name)

    print(f"\n  CREATING NEW POWER FM STATION")
    print(f"  {'=' * 50}")
    print(f"  Station:   {station_name}")
    print(f"  Key:       {station_key}")
    print(f"  Market:    {city}")
    print(f"  Frequency: {freq}")
    print(f"  Port:      {port}")
    print(f"  ID Pattern: {station_id_pattern}")
    if tagline:
        print(f"  Tagline:   {tagline}")
    print()

    # Check for conflicts
    errors = check_conflicts(station_key, port)
    if errors:
        print(f"  ERRORS:")
        for err in errors:
            print(f"    - {err}")
        print(f"\n  Station NOT created. Resolve conflicts and try again.\n")
        return False

    # Step 1: Save to custom_stations.json
    print(f"  [1/4] Saving station config...")
    custom = load_custom_stations()
    custom[station_key] = {
        'name': station_name,
        'port': port,
        'market': city,
        'freq': freq,
        'station_id_pattern': station_id_pattern,
        'fallback_patterns': ['Power_FM'],
        'tagline': tagline or '',
        'created_at': datetime.now().isoformat(timespec='seconds'),
    }
    save_custom_stations(custom)
    print(f"    Saved to {CUSTOM_STATIONS_FILE}")

    # Step 2: Generate ElevenLabs station IDs
    generated_audio = []
    if no_audio:
        print(f"\n  [2/4] Skipping station ID generation (--no-audio)")
    else:
        print(f"\n  [2/4] Generating ElevenLabs station IDs...")
        generated_audio = generate_station_ids(station_name, station_id_pattern, tagline)
        if generated_audio:
            print(f"\n    Generated {len(generated_audio)} station ID(s)")
        else:
            print(f"    No station IDs generated (ElevenLabs may not be configured)")

    # Step 3: Generate market playlist
    print(f"\n  [3/4] Generating market playlist...")
    playlist_path = generate_market_playlist_for_station(station_key)
    if playlist_path:
        print(f"    Playlist: {os.path.basename(playlist_path)}")
    else:
        print(f"    No playlist generated (will use fallback when started)")

    # Step 4: Start the station
    started = False
    if no_start:
        print(f"\n  [4/4] Skipping station start (--no-start)")
    else:
        print(f"\n  [4/4] Starting station stream...")
        time.sleep(0.5)
        started = start_station(station_key, custom[station_key])

    # Print summary
    print(f"\n  {'=' * 50}")
    print(f"  STATION CREATED SUCCESSFULLY")
    print(f"  {'=' * 50}")
    print(f"  Station:      {station_name}")
    print(f"  Key:          {station_key}")
    print(f"  Port:         {port}")
    print(f"  Stream URL:   http://localhost:{port}/stream")
    print(f"  Status Page:  http://localhost:{port}/status")
    print(f"  Config:       {CUSTOM_STATIONS_FILE}")
    if generated_audio:
        print(f"  Station IDs:  {len(generated_audio)} generated")
        for f in generated_audio:
            print(f"                {os.path.basename(f)}")
    if playlist_path:
        print(f"  Playlist:     {os.path.basename(playlist_path)}")
    print(f"  Status:       {'LIVE' if started else 'CREATED (not started)'}")
    print()
    print(f"  Manage with:")
    print(f"    venv/bin/python stations.py --start {station_key}")
    print(f"    venv/bin/python stations.py --stop {station_key}")
    print(f"    venv/bin/python stations.py --status")
    print()

    return True


def remove_station(station_key):
    """Remove a custom station from the config."""
    custom = load_custom_stations()

    if station_key not in custom:
        # Check if it's a hardcoded station
        hardcoded = get_hardcoded_stations()
        if station_key in hardcoded:
            print(f"\n  ERROR: '{station_key}' is a built-in station and cannot be removed.")
            print(f"  Only custom stations can be removed.\n")
            return False
        else:
            print(f"\n  ERROR: Station '{station_key}' not found.")
            print(f"  Use --list to see all stations.\n")
            return False

    station = custom[station_key]
    print(f"\n  Removing station: {station['name']} (port {station['port']})")

    # Stop the station if running
    pid_file = os.path.join(PID_DIR, f'{station_key}.pid')
    if os.path.isfile(pid_file):
        try:
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            import signal as sig
            os.kill(pid, sig.SIGTERM)
            print(f"    Stopped running process (PID {pid})")
        except (OSError, ProcessLookupError, ValueError):
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass

    # Remove from config
    del custom[station_key]
    save_custom_stations(custom)

    print(f"    Removed from {CUSTOM_STATIONS_FILE}")
    print(f"\n  Station '{station_key}' removed.")
    print(f"  Note: Generated audio files and playlists are NOT deleted.")
    print(f"  Delete them manually if needed from:")
    print(f"    {ELEVENLABS_OUTPUT}/")
    print(f"    {PLAYLISTS_DIR}/\n")
    return True


def list_stations():
    """List all stations: hardcoded + custom."""
    hardcoded = get_hardcoded_stations()
    custom = load_custom_stations()

    print(f"\n  POWER FM STATION NETWORK")
    print(f"  {'=' * 75}")
    print(f"  {'Key':<15} {'Station':<28} {'Market':<15} {'Port':<8} {'Type'}")
    print(f"  {'-' * 75}")

    for key in sorted(hardcoded.keys(), key=lambda k: hardcoded[k]['port']):
        station = hardcoded[key]
        print(f"  {key:<15} {station['name']:<28} {station['market']:<15} {station['port']:<8} built-in")

    if custom:
        print(f"  {'-' * 75}")
        for key in sorted(custom.keys(), key=lambda k: custom[k]['port']):
            station = custom[key]
            tagline_display = ''
            if station.get('tagline'):
                tagline_display = f"  \"{station['tagline']}\""
            print(
                f"  {key:<15} {station['name']:<28} {station['market']:<15} "
                f"{station['port']:<8} custom{tagline_display}"
            )

    total = len(hardcoded) + len(custom)
    print(f"  {'-' * 75}")
    print(f"  {len(hardcoded)} built-in + {len(custom)} custom = {total} total stations\n")


def main():
    parser = argparse.ArgumentParser(
        description='Power FM White-Label Station Builder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --city "Dallas" --freq "103.5" --port 8009
  %(prog)s --city "Toronto" --freq "99.1" --port 8010 --tagline "The six. The sound."
  %(prog)s --list
  %(prog)s --remove dallas
        """
    )

    # Create station arguments
    parser.add_argument('--city', type=str, help='City name (e.g., "Dallas")')
    parser.add_argument('--freq', type=str, help='Radio frequency (e.g., "103.5")')
    parser.add_argument('--port', type=int, help='Stream server port (e.g., 8009)')
    parser.add_argument('--tagline', type=str, help='Custom station tagline')

    # Management arguments
    parser.add_argument('--list', action='store_true', help='List all stations including custom ones')
    parser.add_argument('--remove', type=str, metavar='KEY', help='Remove a custom station by key')

    # Options
    parser.add_argument('--no-start', action='store_true', help='Create config but do not start the stream')
    parser.add_argument('--no-audio', action='store_true', help='Skip ElevenLabs station ID generation')

    args = parser.parse_args()

    # --list mode
    if args.list:
        list_stations()
        return

    # --remove mode
    if args.remove:
        key = generate_station_key(args.remove)
        remove_station(key)
        return

    # Create station mode — require city, freq, port
    if not args.city or not args.freq or not args.port:
        if args.city or args.freq or args.port:
            parser.error("Creating a station requires --city, --freq, and --port")
        else:
            parser.print_help()
            print()
            list_stations()
            return

    # Validate port range
    if args.port < 1024 or args.port > 65535:
        parser.error(f"Port must be between 1024 and 65535, got {args.port}")

    create_station(
        city=args.city,
        freq=args.freq,
        port=args.port,
        tagline=args.tagline,
        no_start=args.no_start,
        no_audio=args.no_audio,
    )


if __name__ == '__main__':
    main()
