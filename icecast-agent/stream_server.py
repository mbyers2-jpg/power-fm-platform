#!/usr/bin/env python3
"""
Power FM Stream Server — Python-based MP3 streaming server.

Serves a continuous MP3 audio stream over HTTP, playable by any media player
(VLC, iTunes, browser, car stereo, etc.). Acts as a lightweight Icecast replacement.

Features:
  - Reads M3U playlists and streams tracks sequentially
  - Loops playlist continuously
  - Supports multiple concurrent listeners
  - Icecast-compatible metadata headers (ICY protocol)
  - Real-time listener count tracking
  - Stores listener stats in the icecast-agent database

Usage:
    venv/bin/python stream_server.py                          # Start with default playlist
    venv/bin/python stream_server.py --playlist /path/to.m3u  # Use specific playlist
    venv/bin/python stream_server.py --port 8000              # Custom port
    venv/bin/python stream_server.py --name "Power FM Live"   # Station name

Stream URL: http://localhost:8000/stream
"""

import os
import sys
import time
import json
import signal
import logging
import argparse
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import deque

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import (
    get_connection, upsert_server, upsert_mount_point,
    record_listeners, record_health, record_source_connection,
    get_agent_state, set_agent_state,
)

# --- Logging ---
LOG_DIR = os.path.join(AGENT_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'stream_server.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('stream-server')

# --- Configuration ---
DEFAULT_PORT = 8000
CHUNK_SIZE = 4096  # Bytes per chunk sent to clients
STREAM_BITRATE = 192  # kbps (for timing calculations)
DEFAULT_PLAYLIST = os.path.expanduser(
    '~/Agents/platform-hub/playlists/power_fm_hourly_{}.m3u'.format(
        datetime.now().strftime('%Y-%m-%d')
    )
)

# --- Global State ---
running = True
listeners = {}  # {client_id: {'ip': str, 'connected_at': datetime, 'bytes_sent': int}}
listeners_lock = threading.Lock()
current_track = {'title': '', 'artist': '', 'file': ''}
track_lock = threading.Lock()
listener_counter = 0
counter_lock = threading.Lock()


def signal_handler(sig, frame):
    global running
    log.info("Shutdown signal received...")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def parse_m3u(playlist_path):
    """Parse an M3U playlist and return list of {'path': str, 'title': str}."""
    tracks = []
    if not os.path.isfile(playlist_path):
        log.error(f"Playlist not found: {playlist_path}")
        return tracks

    current_title = None
    with open(playlist_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#EXTM3U') or line.startswith('#PLAYLIST:'):
                continue
            if line.startswith('#EXTINF:'):
                # Extract title after the comma
                parts = line.split(',', 1)
                current_title = parts[1] if len(parts) > 1 else 'Unknown'
            elif not line.startswith('#'):
                if os.path.isfile(line):
                    tracks.append({
                        'path': line,
                        'title': current_title or os.path.basename(line),
                    })
                else:
                    log.warning(f"Track not found, skipping: {line}")
                current_title = None

    return tracks


def generate_audio_stream(playlist_path):
    """
    Generator that yields MP3 chunks from a playlist, looping forever.
    Yields (chunk_bytes, track_info) tuples.
    """
    tracks = parse_m3u(playlist_path)
    if not tracks:
        log.error("No playable tracks in playlist!")
        return

    log.info(f"Loaded {len(tracks)} tracks from playlist")

    while running:
        for track in tracks:
            if not running:
                return

            with track_lock:
                current_track['title'] = track['title']
                current_track['file'] = track['path']

            log.info(f"Now playing: {track['title']}")

            try:
                with open(track['path'], 'rb') as audio_file:
                    while running:
                        chunk = audio_file.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        yield chunk
            except Exception as e:
                log.error(f"Error reading {track['path']}: {e}")
                continue

            # Small gap between tracks (optional, makes transitions cleaner)
            # Yield a tiny silence gap
            if running:
                time.sleep(0.05)

        log.info("Playlist loop complete, restarting...")


class StreamBuffer:
    """
    Thread-safe circular buffer that holds the latest audio chunks.
    New listeners catch up from the current position.
    """

    def __init__(self, max_chunks=500):
        self.buffer = deque(maxlen=max_chunks)
        self.lock = threading.Lock()
        self.position = 0  # Global position counter
        self.event = threading.Event()

    def write(self, chunk):
        with self.lock:
            self.buffer.append((self.position, chunk))
            self.position += 1
        self.event.set()
        self.event.clear()

    def read_from(self, start_pos):
        """Read all chunks from start_pos onwards."""
        with self.lock:
            chunks = []
            for pos, chunk in self.buffer:
                if pos >= start_pos:
                    chunks.append((pos, chunk))
            return chunks

    def get_latest_position(self):
        with self.lock:
            return self.position


# Global stream buffer
stream_buffer = StreamBuffer()


def stream_producer(playlist_path):
    """Background thread that fills the stream buffer from the playlist."""
    bytes_per_second = (STREAM_BITRATE * 1000) / 8
    chunk_duration = CHUNK_SIZE / bytes_per_second

    for chunk in generate_audio_stream(playlist_path):
        if not running:
            break
        stream_buffer.write(chunk)
        # Pace the stream at approximately the bitrate
        time.sleep(chunk_duration)

    log.info("Stream producer stopped.")


class StreamHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves the MP3 stream and status pages."""

    station_name = "Power FM"

    def log_message(self, format, *args):
        # Suppress default HTTP logging, use our logger
        pass

    def do_GET(self):
        if self.path == '/stream' or self.path == '/':
            self._handle_stream()
        elif self.path == '/status':
            self._handle_status()
        elif self.path == '/status.json':
            self._handle_status_json()
        elif self.path == '/now-playing':
            self._handle_now_playing()
        else:
            self.send_error(404)

    def _handle_stream(self):
        """Serve the continuous MP3 stream."""
        global listener_counter

        # Register listener
        with counter_lock:
            listener_counter += 1
            client_id = listener_counter

        client_ip = self.client_address[0]
        with listeners_lock:
            listeners[client_id] = {
                'ip': client_ip,
                'connected_at': datetime.now(),
                'bytes_sent': 0,
            }

        listener_count = len(listeners)
        log.info(f"Listener #{client_id} connected from {client_ip} (total: {listener_count})")

        # Send ICY-compatible headers
        self.send_response(200)
        self.send_header('Content-Type', 'audio/mpeg')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Cache-Control', 'no-cache, no-store')
        self.send_header('Pragma', 'no-cache')
        self.send_header('icy-name', self.station_name)
        self.send_header('icy-genre', 'Hip-Hop/R&B/Afrobeats')
        self.send_header('icy-br', str(STREAM_BITRATE))
        self.send_header('icy-pub', '1')
        self.send_header('icy-url', 'http://powerfm.live')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        # Stream from buffer
        read_pos = stream_buffer.get_latest_position()
        bytes_per_second = (STREAM_BITRATE * 1000) / 8
        chunk_duration = CHUNK_SIZE / bytes_per_second

        try:
            while running:
                chunks = stream_buffer.read_from(read_pos)
                if chunks:
                    for pos, chunk in chunks:
                        self.wfile.write(chunk)
                        with listeners_lock:
                            if client_id in listeners:
                                listeners[client_id]['bytes_sent'] += len(chunk)
                        read_pos = pos + 1
                    self.wfile.flush()
                else:
                    time.sleep(chunk_duration / 2)

        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            log.debug(f"Listener #{client_id} error: {e}")
        finally:
            with listeners_lock:
                info = listeners.pop(client_id, None)

            duration = "unknown"
            sent = "0"
            if info:
                delta = datetime.now() - info['connected_at']
                duration = str(delta).split('.')[0]
                sent = f"{info['bytes_sent'] / (1024*1024):.1f}MB"

            log.info(f"Listener #{client_id} disconnected ({client_ip}, {duration}, {sent}, remaining: {len(listeners)})")

    def _handle_status(self):
        """HTML status page."""
        with listeners_lock:
            listener_list = dict(listeners)
        with track_lock:
            now_playing = dict(current_track)

        listener_count = len(listener_list)

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{self.station_name} — Stream Status</title>
    <meta http-equiv="refresh" content="5">
    <style>
        body {{ background: #1a1a2e; color: #ccd6f6; font-family: -apple-system, sans-serif; padding: 40px; }}
        h1 {{ color: #e94560; }}
        .card {{ background: #16213e; border-radius: 12px; padding: 24px; margin: 16px 0; }}
        .big-num {{ font-size: 48px; font-weight: 800; color: #e94560; }}
        .label {{ color: #8892b0; font-size: 14px; margin-top: 4px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #0f1a30; }}
        th {{ color: #8892b0; font-size: 12px; text-transform: uppercase; }}
        a {{ color: #e94560; text-decoration: none; }}
        .now-playing {{ font-size: 20px; color: #ccd6f6; font-weight: 600; }}
    </style>
</head>
<body>
    <h1>{self.station_name}</h1>
    <div class="card">
        <div class="label">NOW PLAYING</div>
        <div class="now-playing">{now_playing.get('title', 'Nothing')}</div>
    </div>
    <div style="display: flex; gap: 16px;">
        <div class="card" style="flex: 1; text-align: center;">
            <div class="big-num">{listener_count}</div>
            <div class="label">Active Listeners</div>
        </div>
        <div class="card" style="flex: 1; text-align: center;">
            <div class="big-num">{STREAM_BITRATE}</div>
            <div class="label">Bitrate (kbps)</div>
        </div>
    </div>
    <div class="card">
        <div class="label">STREAM URL</div>
        <div style="margin-top: 8px;">
            <a href="/stream">http://localhost:{self.server.server_port}/stream</a>
        </div>
    </div>"""

        if listener_list:
            html += """
    <div class="card">
        <div class="label">CONNECTED LISTENERS</div>
        <table>
            <tr><th>#</th><th>IP</th><th>Connected</th><th>Data Sent</th></tr>"""
            for cid, info in listener_list.items():
                delta = datetime.now() - info['connected_at']
                duration = str(delta).split('.')[0]
                sent = f"{info['bytes_sent'] / (1024*1024):.1f} MB"
                html += f"\n            <tr><td>{cid}</td><td>{info['ip']}</td><td>{duration}</td><td>{sent}</td></tr>"
            html += "\n        </table>\n    </div>"

        html += "\n</body>\n</html>"

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def _handle_status_json(self):
        """JSON status endpoint."""
        with listeners_lock:
            listener_count = len(listeners)
        with track_lock:
            now_playing = dict(current_track)

        data = {
            'station': self.station_name,
            'listeners': listener_count,
            'bitrate': STREAM_BITRATE,
            'now_playing': now_playing.get('title', ''),
            'stream_url': f'http://localhost:{self.server.server_port}/stream',
            'timestamp': datetime.now().isoformat(),
        }

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def _handle_now_playing(self):
        """Simple now-playing text endpoint."""
        with track_lock:
            title = current_track.get('title', 'Nothing playing')

        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(title.encode())


class ThreadedHTTPServer(HTTPServer):
    """Handle each request in a new thread."""
    allow_reuse_address = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle_request, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle_request(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def record_stats_loop(conn_unused, server_name, port):
    """Background thread to record listener stats every 30 seconds."""
    # Create a new connection for this thread (SQLite requires per-thread connections)
    stats_conn = get_connection()
    row = stats_conn.execute("""
        SELECT m.id FROM mount_points m
        JOIN servers s ON m.server_id = s.id
        WHERE s.name = ? AND m.mount_name = '/stream'
    """, (server_name,)).fetchone()
    mount_id = row['id'] if row else None

    while running:
        for _ in range(30):
            if not running:
                return
            time.sleep(1)

        with listeners_lock:
            count = len(listeners)

        try:
            if mount_id:
                record_listeners(stats_conn, mount_id, count)
                record_health(stats_conn, mount_id, is_live=True,
                              bitrate_actual=STREAM_BITRATE)
        except Exception as e:
            log.debug(f"Stats recording error: {e}")


def start_server(playlist_path, port=DEFAULT_PORT, station_name="Power FM"):
    """Start the streaming server."""
    global running

    # Find a valid playlist
    if not os.path.isfile(playlist_path):
        # Try today's hourly playlist
        today = datetime.now().strftime('%Y-%m-%d')
        alt_paths = [
            os.path.expanduser(f'~/Agents/platform-hub/playlists/power_fm_hourly_{today}.m3u'),
            os.path.expanduser(f'~/Agents/platform-hub/playlists/power_fm_top25_{today}.m3u'),
            os.path.expanduser(f'~/Agents/platform-hub/playlists/power_fm_top10_{today}.m3u'),
        ]
        for alt in alt_paths:
            if os.path.isfile(alt):
                playlist_path = alt
                break
        else:
            log.error(f"No playlist found. Generate one first: cd ~/Agents/platform-hub && venv/bin/python agent.py --playlist")
            sys.exit(1)

    tracks = parse_m3u(playlist_path)
    if not tracks:
        log.error("Playlist has no playable tracks!")
        sys.exit(1)

    # Register in icecast-agent database
    conn = get_connection()
    server_name = f"powerfm-local-{port}"
    upsert_server(conn, server_name, 'localhost', port,
                  server_type='python-stream', admin_url=f"http://localhost:{port}/status")
    # Get the server ID
    row = conn.execute("SELECT id FROM servers WHERE name = ?", (server_name,)).fetchone()
    server_id = row['id'] if row else None
    if server_id:
        upsert_mount_point(conn, server_id, '/stream',
                           content_type='audio/mpeg', bitrate=STREAM_BITRATE,
                           genre='Hip-Hop/R&B/Afrobeats', stream_title=station_name,
                           status='active')
        record_source_connection(conn, server_id, '/stream', source_ip='127.0.0.1',
                                 user_agent='Power FM Stream Server')

    # Start stream producer thread
    producer = threading.Thread(target=stream_producer, args=(playlist_path,), daemon=True)
    producer.start()

    # Wait for buffer to fill slightly
    time.sleep(0.5)

    # Start stats recorder thread
    stats_thread = threading.Thread(target=record_stats_loop,
                                    args=(conn, server_name, port), daemon=True)
    stats_thread.start()

    # Configure handler
    StreamHandler.station_name = station_name

    # Start HTTP server
    server = ThreadedHTTPServer(('0.0.0.0', port), StreamHandler)
    log.info(f"")
    log.info(f"  ╔══════════════════════════════════════════╗")
    log.info(f"  ║     POWER FM STREAM SERVER               ║")
    log.info(f"  ╠══════════════════════════════════════════╣")
    log.info(f"  ║  Stream:  http://localhost:{port}/stream    ║")
    log.info(f"  ║  Status:  http://localhost:{port}/status    ║")
    log.info(f"  ║  API:     http://localhost:{port}/status.json║")
    log.info(f"  ║  Tracks:  {len(tracks):3d}                           ║")
    log.info(f"  ║  Bitrate: {STREAM_BITRATE} kbps                      ║")
    log.info(f"  ╚══════════════════════════════════════════╝")
    log.info(f"")

    print(f"\n  POWER FM STREAM SERVER")
    print(f"  Stream:  http://localhost:{port}/stream")
    print(f"  Status:  http://localhost:{port}/status")
    print(f"  Tracks:  {len(tracks)}")
    print(f"  Bitrate: {STREAM_BITRATE} kbps")
    print(f"\n  Open in VLC: vlc http://localhost:{port}/stream")
    print(f"  Open in browser: open http://localhost:{port}/status\n")

    try:
        while running:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        server.server_close()
        log.info("Stream server stopped.")
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Power FM Stream Server')
    parser.add_argument('--playlist', type=str, default=DEFAULT_PLAYLIST,
                        help='Path to M3U playlist')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'Port to serve on (default: {DEFAULT_PORT})')
    parser.add_argument('--name', type=str, default='Power FM',
                        help='Station name (shown in ICY metadata)')
    args = parser.parse_args()

    start_server(args.playlist, port=args.port, station_name=args.name)


if __name__ == '__main__':
    main()
