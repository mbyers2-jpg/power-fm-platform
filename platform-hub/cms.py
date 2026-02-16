#!/usr/bin/env python3
"""
Power FM CMS Admin Panel — Flask Blueprint

Provides admin/CMS functionality for managing stations, playlists,
audio assets, schedules, and DJ shows. Registers as a Blueprint
under /admin on the main dashboard Flask app.

Routes:
    GET  /admin/                           — CMS dashboard overview
    GET  /admin/stations                   — Station management
    POST /admin/stations/<key>/start       — Start a station
    POST /admin/stations/<key>/stop        — Stop a station
    POST /admin/stations/create            — Create new custom station
    GET  /admin/playlists                  — Playlist management
    GET  /admin/playlists/<station_key>    — Edit specific playlist
    POST /admin/playlists/<key>/reorder    — Reorder tracks
    POST /admin/playlists/<key>/remove     — Remove track
    POST /admin/playlists/<key>/add        — Add track
    GET  /admin/library                    — Audio asset library
    GET  /admin/library/search?q=term      — Search audio files
    GET  /admin/schedule                   — Schedule management
    GET  /admin/shows                      — DJ show management
    GET  /api/admin/stats                  — JSON stats
"""

import glob
import json
import os
import subprocess
import time
from datetime import datetime
from flask import Blueprint, render_template_string, jsonify, request, redirect, url_for, session

# ---------------------------------------------------------------------------
# Blueprint setup
# ---------------------------------------------------------------------------

cms_bp = Blueprint('cms', __name__, url_prefix='/admin')

# ---------------------------------------------------------------------------
# Admin Authentication (session-based)
# ---------------------------------------------------------------------------

ADMIN_USERNAME = os.environ.get('POWER_FM_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('POWER_FM_ADMIN_PASS', 'PowerFM2026!')

# Public routes that don't require login
_PUBLIC_ENDPOINTS = {'cms.login'}


@cms_bp.before_request
def admin_auth_required():
    """Redirect unauthenticated users to the login page."""
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if not session.get('admin_logged_in'):
        return redirect(url_for('cms.login'))


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

LOGIN_PAGE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Login - Power FM Admin</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-card {
            width: 380px;
            background: rgba(22, 33, 62, 0.7);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(233, 69, 96, 0.25);
            border-radius: 16px;
            padding: 40px 32px 32px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        }
        .login-brand {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            margin-bottom: 32px;
        }
        .login-brand .icon {
            width: 48px; height: 48px;
            background: linear-gradient(135deg, #e94560, #c73050);
            border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            font-size: 24px; font-weight: 700; color: #fff;
        }
        .login-brand .text {
            font-size: 22px; font-weight: 700; color: #fff;
            letter-spacing: 1px;
        }
        .login-brand .sub {
            font-size: 12px; color: rgba(255,255,255,0.45);
            display: block; margin-top: 2px;
        }
        .login-field {
            margin-bottom: 18px;
        }
        .login-field label {
            display: block;
            font-size: 12px; font-weight: 600;
            color: rgba(255,255,255,0.55);
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 6px;
        }
        .login-field input {
            width: 100%;
            padding: 12px 14px;
            border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.12);
            background: rgba(15, 52, 96, 0.5);
            color: #fff;
            font-size: 15px;
            outline: none;
            transition: border-color 0.2s;
        }
        .login-field input:focus {
            border-color: #e94560;
        }
        .login-btn {
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 8px;
            background: linear-gradient(135deg, #e94560, #c73050);
            color: #fff;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: opacity 0.2s;
            margin-top: 6px;
        }
        .login-btn:hover { opacity: 0.9; }
        .login-error {
            background: rgba(233, 69, 96, 0.12);
            color: #e94560;
            border: 1px solid rgba(233, 69, 96, 0.25);
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 18px;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="login-brand">
            <div class="icon">P</div>
            <div>
                <span class="text">POWER FM</span>
                <span class="sub">Admin Panel</span>
            </div>
        </div>
        {% if error %}
            <div class="login-error">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="login-field">
                <label>Username</label>
                <input type="text" name="username" autofocus required>
            </div>
            <div class="login-field">
                <label>Password</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit" class="login-btn">Sign In</button>
        </form>
    </div>
</body>
</html>'''


@cms_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('cms.admin_dashboard'))
        return render_template_string(LOGIN_PAGE, error='Invalid username or password.')
    return render_template_string(LOGIN_PAGE, error=None)


@cms_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('cms.login'))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(AGENT_DIR)

PLAYLIST_DIR = os.path.join(AGENT_DIR, 'playlists')
ICECAST_DIR = os.path.join(AGENTS_DIR, 'icecast-agent')
ICECAST_VENV_PYTHON = os.path.join(ICECAST_DIR, 'venv', 'bin', 'python')
ICECAST_STATIONS_PY = os.path.join(ICECAST_DIR, 'stations.py')
CUSTOM_STATIONS_JSON = os.path.join(ICECAST_DIR, 'config', 'custom_stations.json')

YOUTUBE_EXTRACTIONS = os.path.join(AGENTS_DIR, 'youtube-agent', 'extractions')
ELEVENLABS_OUTPUT = os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'output')

# Default stations from dashboard.py
STATION_PORTS = {
    'national': 8000, 'la': 8001, 'nyc': 8002, 'chicago': 8003,
    'miami': 8004, 'atlanta': 8005, 'houston': 8006, 'london': 8007, 'lagos': 8008,
}
STATION_NAMES = {
    'national': 'Power FM', 'la': 'Power 106 LA', 'nyc': 'Power 105.1 NYC',
    'chicago': 'Power 92 Chicago', 'miami': 'Power 96 Miami',
    'atlanta': 'Power 107.5 Atlanta', 'houston': 'Power 104 Houston',
    'london': 'Power FM London', 'lagos': 'Power FM Lagos',
}

# Schedule blocks from scheduler.py
SCHEDULE_BLOCKS = [
    {
        'name': 'morning_power_hour', 'label': 'Morning Power Hour',
        'start_hour': 6, 'end_hour': 10, 'vibe': 'High energy, upbeat',
        'track_limit': 10, 'shuffle': False, 'station_id_every': 2, 'promos': 'heavy',
    },
    {
        'name': 'midday_mix', 'label': 'Midday Mix',
        'start_hour': 10, 'end_hour': 15, 'vibe': 'Mainstream rotation',
        'track_limit': 25, 'shuffle': False, 'station_id_every': 3, 'promos': 'standard',
    },
    {
        'name': 'afternoon_drive', 'label': 'Afternoon Drive',
        'start_hour': 15, 'end_hour': 19, 'vibe': 'Peak energy',
        'track_limit': 15, 'shuffle': True, 'station_id_every': 2, 'promos': 'heavy',
    },
    {
        'name': 'evening_vibes', 'label': 'Evening Vibes',
        'start_hour': 19, 'end_hour': 21, 'vibe': 'Chill/R&B focused',
        'track_limit': 25, 'shuffle': False, 'station_id_every': 4, 'promos': 'minimal',
    },
    {
        'name': 'late_night', 'label': 'Late Night',
        'start_hour': 21, 'end_hour': 24, 'vibe': 'Slow jams, deep cuts',
        'track_limit': 25, 'shuffle': False, 'station_id_every': 4, 'promos': 'none',
    },
    {
        'name': 'overnight', 'label': 'Overnight',
        'start_hour': 0, 'end_hour': 6, 'vibe': 'Auto-pilot',
        'track_limit': 25, 'shuffle': False, 'station_id_every': 5, 'promos': 'none',
    },
]

# DJ profiles from shows.py
DJS = {
    'dj_nova': {'name': 'DJ Nova', 'bio': 'High-energy morning host bringing the heat since day one.', 'voice': 'Charlie', 'style': 'energetic'},
    'dj_silk': {'name': 'DJ Silk', 'bio': 'Smooth late-night vibes and R&B classics.', 'voice': 'Lily', 'style': 'smooth'},
    'dj_blaze': {'name': 'DJ Blaze', 'bio': 'Afternoon drive specialist. Peak energy, peak hits.', 'voice': 'Adam', 'style': 'hype'},
    'mc_culture': {'name': 'MC Culture', 'bio': 'The voice of the culture. Midday mix master.', 'voice': 'Brian', 'style': 'authoritative'},
    'dj_phantom': {'name': 'DJ Phantom', 'bio': 'Deep cuts and underground heat. The overnight selector.', 'voice': 'Daniel', 'style': 'chill'},
}

SHOWS = {
    'morning_power_hour': {'label': 'The Morning Power Hour', 'dj': 'dj_nova', 'time': '6am-10am', 'tagline': 'Wake up and get locked in!'},
    'midday_mix': {'label': 'The Midday Mix', 'dj': 'mc_culture', 'time': '10am-3pm', 'tagline': 'Culture on rotation.'},
    'afternoon_drive': {'label': 'Afternoon Drive', 'dj': 'dj_blaze', 'time': '3pm-7pm', 'tagline': 'Peak hours. Peak hits.'},
    'evening_vibes': {'label': 'Evening Vibes', 'dj': 'dj_silk', 'time': '7pm-9pm', 'tagline': 'Slow it down. Feel the music.'},
    'late_night': {'label': 'Late Night Sessions', 'dj': 'dj_silk', 'time': '9pm-12am', 'tagline': 'After dark. Deep cuts only.'},
    'overnight': {'label': 'The Overnight', 'dj': 'dj_phantom', 'time': '12am-6am', 'tagline': 'Auto-pilot. Underground heat.'},
}

# Activity log (in-memory for this session)
_activity_log = []

def _log_activity(action, detail=''):
    """Record an action to the in-memory activity log."""
    _activity_log.insert(0, {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'action': action,
        'detail': detail,
    })
    # Keep last 50 entries
    if len(_activity_log) > 50:
        _activity_log.pop()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _load_custom_stations():
    """Load custom stations from JSON config."""
    if not os.path.exists(CUSTOM_STATIONS_JSON):
        return {}
    try:
        with open(CUSTOM_STATIONS_JSON, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_custom_stations(data):
    """Save custom stations to JSON config."""
    os.makedirs(os.path.dirname(CUSTOM_STATIONS_JSON), exist_ok=True)
    with open(CUSTOM_STATIONS_JSON, 'w') as f:
        json.dump(data, f, indent=4)


def _get_all_stations():
    """Return combined dict of built-in + custom stations with status info."""
    stations = {}
    for key, name in STATION_NAMES.items():
        port = STATION_PORTS.get(key, 0)
        stations[key] = {
            'name': name,
            'port': port,
            'market': key.replace('_', ' ').title() if key != 'national' else 'National',
            'tagline': '',
            'custom': False,
            'live': _check_station_live(port),
        }

    custom = _load_custom_stations()
    for key, info in custom.items():
        stations[key] = {
            'name': info.get('name', key),
            'port': info.get('port', 0),
            'market': info.get('market', ''),
            'tagline': info.get('tagline', ''),
            'custom': True,
            'live': _check_station_live(info.get('port', 0)),
        }
    return stations


def _check_station_live(port):
    """Quick check if a station port is responding."""
    if not port:
        return False
    try:
        import urllib.request
        req = urllib.request.Request(
            'http://localhost:%d/status.json' % port,
            method='GET'
        )
        resp = urllib.request.urlopen(req, timeout=1)
        return resp.status == 200
    except Exception:
        return False


def _get_playlist_files():
    """Get all .m3u playlist files grouped by station/type."""
    playlists = {}
    if not os.path.isdir(PLAYLIST_DIR):
        return playlists
    for fname in sorted(os.listdir(PLAYLIST_DIR)):
        if not fname.endswith('.m3u'):
            continue
        # Parse: power_fm_<key>_<date>.m3u
        base = fname.replace('.m3u', '')
        parts = base.split('_')
        # Find station key from filename (strip power_fm_ prefix and date suffix)
        if len(parts) >= 3 and parts[0] == 'power' and parts[1] == 'fm':
            # Last part is date (YYYY-MM-DD), rest is key
            date_part = parts[-1]
            key_parts = parts[2:-1]
            station_key = '_'.join(key_parts) if key_parts else 'unknown'
        else:
            station_key = base
            date_part = ''

        full_path = os.path.join(PLAYLIST_DIR, fname)
        if station_key not in playlists:
            playlists[station_key] = []
        playlists[station_key].append({
            'filename': fname,
            'path': full_path,
            'date': date_part,
            'size': os.path.getsize(full_path),
        })
    return playlists


def _parse_m3u(filepath):
    """Parse an M3U file into a list of track dicts."""
    tracks = []
    if not os.path.exists(filepath):
        return tracks
    with open(filepath, 'r') as f:
        lines = f.readlines()
    current_info = None
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTM3U') or line.startswith('#PLAYLIST:'):
            continue
        if line.startswith('#EXTINF:'):
            # #EXTINF:-1,Title goes here
            comma_idx = line.find(',')
            if comma_idx >= 0:
                current_info = line[comma_idx + 1:]
            else:
                current_info = line
        elif line and not line.startswith('#'):
            # This is a file path
            track = {
                'title': current_info or os.path.basename(line),
                'path': line,
                'filename': os.path.basename(line),
                'exists': os.path.exists(line),
            }
            # Determine source
            if '/elevenlabs-agent/' in line:
                track['source'] = 'elevenlabs'
            elif '/youtube-agent/' in line:
                track['source'] = 'youtube'
            else:
                track['source'] = 'other'
            tracks.append(track)
            current_info = None
    return tracks


def _write_m3u(filepath, tracks, playlist_name='Power FM Playlist'):
    """Write tracks list back to an M3U file."""
    with open(filepath, 'w') as f:
        f.write('#EXTM3U\n')
        f.write('#PLAYLIST:%s\n' % playlist_name)
        for t in tracks:
            title = t.get('title', os.path.basename(t.get('path', '')))
            f.write('#EXTINF:-1,%s\n' % title)
            f.write('%s\n' % t.get('path', ''))


def _get_audio_files(search_query=None):
    """Get all audio files from youtube-agent extractions and elevenlabs output."""
    files = []

    for dirpath, source_name in [
        (YOUTUBE_EXTRACTIONS, 'YouTube Extraction'),
        (ELEVENLABS_OUTPUT, 'ElevenLabs AI'),
    ]:
        if not os.path.isdir(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if not fname.lower().endswith('.mp3'):
                continue
            if search_query and search_query.lower() not in fname.lower():
                continue
            full_path = os.path.join(dirpath, fname)
            try:
                stat = os.stat(full_path)
                fsize = stat.st_size
                fmtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
            except OSError:
                fsize = 0
                fmtime = 'unknown'

            files.append({
                'filename': fname,
                'path': full_path,
                'source': source_name,
                'size': fsize,
                'size_human': _format_size(fsize),
                'modified': fmtime,
            })

    files.sort(key=lambda x: x.get('filename', '').lower())
    return files


def _format_size(b):
    """Format bytes as human string."""
    if b < 1024:
        return '%d B' % b
    elif b < 1024 * 1024:
        return '%.1f KB' % (b / 1024)
    else:
        return '%.1f MB' % (b / (1024 * 1024))


def _get_current_block_name():
    """Get the name of the current schedule block."""
    hour = datetime.now().hour
    for block in SCHEDULE_BLOCKS:
        sh = block['start_hour']
        eh = block['end_hour']
        if eh == 24:
            if hour >= sh:
                return block['name']
        elif sh < eh:
            if sh <= hour < eh:
                return block['name']
        else:
            if hour >= sh or hour < eh:
                return block['name']
    return 'overnight'


# ---------------------------------------------------------------------------
# Base HTML template with sidebar
# ---------------------------------------------------------------------------

BASE_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ page_title }} - Power FM Admin</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            min-height: 100vh;
            display: flex;
        }

        /* Sidebar */
        .sidebar {
            width: 250px;
            min-height: 100vh;
            background: linear-gradient(180deg, #16213e 0%, #0f3460 100%);
            border-right: 1px solid rgba(233, 69, 96, 0.3);
            position: fixed;
            top: 0;
            left: 0;
            z-index: 100;
            display: flex;
            flex-direction: column;
        }
        .sidebar-header {
            padding: 24px 20px 16px;
            border-bottom: 1px solid rgba(233, 69, 96, 0.2);
        }
        .sidebar-brand {
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
            color: #fff;
        }
        .sidebar-brand .brand-icon {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #e94560, #c73050);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            font-weight: 700;
        }
        .sidebar-brand .brand-text {
            font-size: 16px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }
        .sidebar-brand .brand-sub {
            font-size: 11px;
            color: rgba(255,255,255,0.5);
            display: block;
            margin-top: 1px;
        }
        .sidebar-nav {
            flex: 1;
            padding: 16px 0;
            overflow-y: auto;
        }
        .nav-section {
            padding: 0 16px;
            margin-bottom: 8px;
        }
        .nav-section-title {
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: rgba(255,255,255,0.35);
            padding: 8px 12px;
        }
        .nav-link {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 12px;
            border-radius: 8px;
            color: rgba(255,255,255,0.7);
            text-decoration: none;
            font-size: 14px;
            transition: all 0.2s;
            margin-bottom: 2px;
        }
        .nav-link:hover {
            background: rgba(233, 69, 96, 0.15);
            color: #fff;
        }
        .nav-link.active {
            background: rgba(233, 69, 96, 0.25);
            color: #e94560;
            font-weight: 600;
        }
        .nav-link .nav-icon {
            width: 20px;
            text-align: center;
            font-size: 16px;
            flex-shrink: 0;
        }
        .sidebar-footer {
            padding: 16px 20px;
            border-top: 1px solid rgba(233, 69, 96, 0.2);
            font-size: 11px;
            color: rgba(255,255,255,0.3);
        }
        .sidebar-footer a {
            color: #e94560;
            text-decoration: none;
        }

        /* Main content */
        .main-content {
            margin-left: 250px;
            flex: 1;
            min-height: 100vh;
            padding: 0;
        }
        .topbar {
            background: rgba(22, 33, 62, 0.8);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid rgba(233, 69, 96, 0.15);
            padding: 16px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 50;
        }
        .topbar h1 {
            font-size: 20px;
            font-weight: 700;
            color: #fff;
        }
        .topbar-actions {
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .page-content {
            padding: 32px;
        }

        /* Cards */
        .stat-cards {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 32px;
        }
        .stat-card {
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border: 1px solid rgba(233, 69, 96, 0.2);
            border-radius: 12px;
            padding: 20px;
            transition: border-color 0.2s;
        }
        .stat-card:hover {
            border-color: #e94560;
        }
        .stat-card .stat-label {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: rgba(255,255,255,0.5);
            margin-bottom: 8px;
        }
        .stat-card .stat-value {
            font-size: 28px;
            font-weight: 700;
            color: #fff;
        }
        .stat-card .stat-value .accent {
            color: #e94560;
        }
        .stat-card .stat-sub {
            font-size: 12px;
            color: rgba(255,255,255,0.4);
            margin-top: 4px;
        }

        /* Table styles */
        .data-table {
            width: 100%;
            border-collapse: collapse;
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border: 1px solid rgba(233, 69, 96, 0.2);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 24px;
        }
        .data-table th {
            background: rgba(233, 69, 96, 0.1);
            padding: 12px 16px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: rgba(255,255,255,0.6);
            text-align: left;
            font-weight: 600;
            border-bottom: 1px solid rgba(233, 69, 96, 0.15);
        }
        .data-table td {
            padding: 12px 16px;
            font-size: 14px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            vertical-align: middle;
        }
        .data-table tr:last-child td {
            border-bottom: none;
        }
        .data-table tr:hover td {
            background: rgba(233, 69, 96, 0.05);
        }

        /* Status badges */
        .badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.5px;
        }
        .badge-live {
            background: rgba(0, 255, 136, 0.15);
            color: #00ff88;
            border: 1px solid rgba(0, 255, 136, 0.3);
        }
        .badge-offline {
            background: rgba(233, 69, 96, 0.15);
            color: #e94560;
            border: 1px solid rgba(233, 69, 96, 0.3);
        }
        .badge-custom {
            background: rgba(255, 183, 0, 0.15);
            color: #ffb700;
            border: 1px solid rgba(255, 183, 0, 0.3);
        }
        .badge-youtube {
            background: rgba(255, 0, 0, 0.12);
            color: #ff4444;
            border: 1px solid rgba(255, 0, 0, 0.25);
        }
        .badge-elevenlabs {
            background: rgba(128, 0, 255, 0.12);
            color: #b388ff;
            border: 1px solid rgba(128, 0, 255, 0.25);
        }
        .badge-active {
            background: rgba(0, 200, 83, 0.12);
            color: #00c853;
        }

        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            border: none;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.2s;
            font-family: inherit;
        }
        .btn-primary {
            background: linear-gradient(135deg, #e94560, #c73050);
            color: #fff;
        }
        .btn-primary:hover {
            background: linear-gradient(135deg, #ff5a7a, #e94560);
            transform: translateY(-1px);
        }
        .btn-success {
            background: linear-gradient(135deg, #00c853, #009624);
            color: #fff;
        }
        .btn-success:hover {
            background: linear-gradient(135deg, #00e676, #00c853);
        }
        .btn-danger {
            background: linear-gradient(135deg, #e94560, #b71c1c);
            color: #fff;
        }
        .btn-danger:hover {
            background: linear-gradient(135deg, #ff5a7a, #e94560);
        }
        .btn-outline {
            background: transparent;
            color: #e94560;
            border: 1px solid rgba(233, 69, 96, 0.4);
        }
        .btn-outline:hover {
            background: rgba(233, 69, 96, 0.1);
            border-color: #e94560;
        }
        .btn-sm {
            padding: 5px 10px;
            font-size: 12px;
        }

        /* Section panels */
        .panel {
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border: 1px solid rgba(233, 69, 96, 0.2);
            border-radius: 12px;
            margin-bottom: 24px;
            overflow: hidden;
        }
        .panel-header {
            padding: 16px 20px;
            border-bottom: 1px solid rgba(233, 69, 96, 0.15);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .panel-header h2 {
            font-size: 16px;
            font-weight: 700;
            color: #fff;
        }
        .panel-body {
            padding: 20px;
        }

        /* Form styles */
        .form-group {
            margin-bottom: 16px;
        }
        .form-group label {
            display: block;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: rgba(255,255,255,0.6);
            margin-bottom: 6px;
        }
        .form-control {
            width: 100%;
            padding: 10px 14px;
            background: rgba(0,0,0,0.3);
            border: 1px solid rgba(233, 69, 96, 0.2);
            border-radius: 8px;
            color: #e0e0e0;
            font-size: 14px;
            font-family: inherit;
            transition: border-color 0.2s;
        }
        .form-control:focus {
            outline: none;
            border-color: #e94560;
        }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }

        /* Audio player */
        audio {
            height: 32px;
            width: 200px;
            border-radius: 16px;
        }
        audio::-webkit-media-controls-panel {
            background: rgba(233, 69, 96, 0.15);
        }

        /* Search bar */
        .search-bar {
            display: flex;
            gap: 8px;
            margin-bottom: 24px;
        }
        .search-bar .form-control {
            flex: 1;
            max-width: 400px;
        }

        /* Track list */
        .track-list {
            list-style: none;
        }
        .track-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 16px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            transition: background 0.15s;
        }
        .track-item:hover {
            background: rgba(233, 69, 96, 0.05);
        }
        .track-item:last-child {
            border-bottom: none;
        }
        .track-num {
            width: 28px;
            height: 28px;
            border-radius: 6px;
            background: rgba(233, 69, 96, 0.15);
            color: #e94560;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 700;
            flex-shrink: 0;
        }
        .track-info {
            flex: 1;
            min-width: 0;
        }
        .track-title {
            font-size: 14px;
            color: #fff;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .track-path {
            font-size: 11px;
            color: rgba(255,255,255,0.35);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-top: 2px;
        }
        .track-actions {
            display: flex;
            gap: 6px;
            align-items: center;
            flex-shrink: 0;
        }

        /* Activity list */
        .activity-list {
            list-style: none;
        }
        .activity-item {
            padding: 10px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            display: flex;
            gap: 12px;
        }
        .activity-item:last-child { border-bottom: none; }
        .activity-time {
            font-size: 11px;
            color: rgba(255,255,255,0.35);
            white-space: nowrap;
            min-width: 130px;
        }
        .activity-text {
            font-size: 13px;
            color: rgba(255,255,255,0.8);
        }

        /* Grid for shows */
        .show-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        .show-card {
            background: linear-gradient(135deg, rgba(22, 33, 62, 0.8), rgba(15, 52, 96, 0.8));
            border: 1px solid rgba(233, 69, 96, 0.2);
            border-radius: 12px;
            padding: 20px;
            transition: border-color 0.2s;
        }
        .show-card:hover { border-color: #e94560; }
        .show-card.show-live {
            border-color: #00ff88;
            box-shadow: 0 0 20px rgba(0, 255, 136, 0.1);
        }
        .show-card .show-name {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            margin-bottom: 4px;
        }
        .show-card .show-time {
            font-size: 13px;
            color: #e94560;
            font-weight: 600;
            margin-bottom: 8px;
        }
        .show-card .show-tagline {
            font-size: 13px;
            color: rgba(255,255,255,0.5);
            font-style: italic;
            margin-bottom: 12px;
        }
        .show-card .dj-info {
            display: flex;
            align-items: center;
            gap: 12px;
            padding-top: 12px;
            border-top: 1px solid rgba(255,255,255,0.08);
        }
        .dj-avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #e94560, #0f3460);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            font-weight: 700;
            color: #fff;
            flex-shrink: 0;
        }
        .dj-name {
            font-size: 14px;
            font-weight: 600;
            color: #fff;
        }
        .dj-style {
            font-size: 11px;
            color: rgba(255,255,255,0.4);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        /* Flash messages */
        .flash-msg {
            padding: 12px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
            font-weight: 500;
        }
        .flash-success {
            background: rgba(0, 200, 83, 0.12);
            color: #00c853;
            border: 1px solid rgba(0, 200, 83, 0.25);
        }
        .flash-error {
            background: rgba(233, 69, 96, 0.12);
            color: #e94560;
            border: 1px solid rgba(233, 69, 96, 0.25);
        }

        /* Schedule block visualization */
        .schedule-timeline {
            display: flex;
            height: 48px;
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 24px;
        }
        .schedule-block-vis {
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 600;
            color: rgba(255,255,255,0.8);
            transition: opacity 0.2s;
        }
        .schedule-block-vis:hover { opacity: 0.8; }
        .schedule-block-vis.active-block {
            box-shadow: inset 0 0 0 2px #fff;
        }

        /* Responsive */
        @media (max-width: 900px) {
            .sidebar { width: 60px; }
            .sidebar .brand-text, .sidebar .brand-sub, .sidebar .nav-section-title,
            .sidebar .nav-link span:not(.nav-icon), .sidebar-footer { display: none; }
            .sidebar .nav-link { justify-content: center; padding: 10px; }
            .main-content { margin-left: 60px; }
            .form-row { grid-template-columns: 1fr; }
            .stat-cards { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 600px) {
            .stat-cards { grid-template-columns: 1fr; }
            .page-content { padding: 16px; }
        }

        /* Utility classes */
        .inline-form { display: inline; }
        .ml-4 { margin-left: 4px; }
        .mt-12 { margin-top: 12px; }
        .page-header-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
        }
        .page-heading {
            color: #fff;
            font-size: 22px;
            margin-bottom: 4px;
        }
        .page-subtitle {
            font-size: 13px;
            color: rgba(255,255,255,0.4);
        }
    </style>
</head>
<body>
    <!-- Sidebar -->
    <nav class="sidebar">
        <div class="sidebar-header">
            <a href="/admin/" class="sidebar-brand">
                <div class="brand-icon">P</div>
                <div>
                    <span class="brand-text">POWER FM</span>
                    <span class="brand-sub">Admin Panel</span>
                </div>
            </a>
        </div>
        <div class="sidebar-nav">
            <div class="nav-section">
                <div class="nav-section-title">Overview</div>
                <a href="/admin/" class="nav-link {{ 'active' if active_page == 'dashboard' else '' }}">
                    <span class="nav-icon">&#9632;</span><span>Dashboard</span>
                </a>
            </div>
            <div class="nav-section">
                <div class="nav-section-title">Content</div>
                <a href="/admin/stations" class="nav-link {{ 'active' if active_page == 'stations' else '' }}">
                    <span class="nav-icon">&#9783;</span><span>Stations</span>
                </a>
                <a href="/admin/playlists" class="nav-link {{ 'active' if active_page == 'playlists' else '' }}">
                    <span class="nav-icon">&#9835;</span><span>Playlists</span>
                </a>
                <a href="/admin/library" class="nav-link {{ 'active' if active_page == 'library' else '' }}">
                    <span class="nav-icon">&#9836;</span><span>Audio Library</span>
                </a>
            </div>
            <div class="nav-section">
                <div class="nav-section-title">Programming</div>
                <a href="/admin/schedule" class="nav-link {{ 'active' if active_page == 'schedule' else '' }}">
                    <span class="nav-icon">&#9200;</span><span>Schedule</span>
                </a>
                <a href="/admin/shows" class="nav-link {{ 'active' if active_page == 'shows' else '' }}">
                    <span class="nav-icon">&#9734;</span><span>DJ Shows</span>
                </a>
            </div>
            <div class="nav-section">
                <div class="nav-section-title">Platform</div>
                <a href="/" class="nav-link">
                    <span class="nav-icon">&#8592;</span><span>Main Dashboard</span>
                </a>
            </div>
        </div>
        <div class="sidebar-footer">
            Power FM CMS v1.0<br>
            <a href="/">Back to dashboard</a>
            &nbsp;&middot;&nbsp;
            <a href="/admin/logout">Logout</a>
        </div>
    </nav>

    <!-- Main -->
    <div class="main-content">
        <div class="topbar">
            <h1>{{ page_title }}</h1>
            <div class="topbar-actions">
                <span style="font-size:12px; color:rgba(255,255,255,0.4);">{{ now }}</span>
            </div>
        </div>
        <div class="page-content">
            {% if flash_msg %}
                <div class="flash-msg {{ flash_type or 'flash-success' }}">{{ flash_msg }}</div>
            {% endif %}
            {% block content %}{% endblock %}
        </div>
    </div>
</body>
</html>'''


# ---------------------------------------------------------------------------
# Route: Admin Dashboard Overview
# ---------------------------------------------------------------------------

@cms_bp.route('/')
def admin_dashboard():
    stations = _get_all_stations()
    live_count = sum(1 for s in stations.values() if s['live'])
    total_stations = len(stations)

    audio_files = _get_audio_files()
    total_audio = len(audio_files)

    playlists = _get_playlist_files()
    total_playlists = sum(len(v) for v in playlists.values())

    # Count total tracks across all playlists
    total_tracks = 0
    for key, pl_list in playlists.items():
        for pl in pl_list:
            tracks = _parse_m3u(pl['path'])
            total_tracks += len(tracks)

    current_block = _get_current_block_name()

    html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
        <!-- Stat cards -->
        <div class="stat-cards">
            <div class="stat-card">
                <div class="stat-label">Stations</div>
                <div class="stat-value"><span class="accent">{{ live_count }}</span> / {{ total_stations }}</div>
                <div class="stat-sub">{{ live_count }} live now</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Audio Files</div>
                <div class="stat-value">{{ total_audio }}</div>
                <div class="stat-sub">YouTube + ElevenLabs</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Playlists</div>
                <div class="stat-value">{{ total_playlists }}</div>
                <div class="stat-sub">{{ total_tracks }} total tracks</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Current Block</div>
                <div class="stat-value" style="font-size:18px;">{{ current_block_label }}</div>
                <div class="stat-sub">{{ current_show_time }}</div>
            </div>
        </div>

        <!-- Quick actions -->
        <div class="panel">
            <div class="panel-header">
                <h2>Quick Actions</h2>
            </div>
            <div class="panel-body" style="display:flex; gap:12px; flex-wrap:wrap;">
                <a href="/admin/stations" class="btn btn-primary">Manage Stations</a>
                <a href="/admin/playlists" class="btn btn-outline">Edit Playlists</a>
                <a href="/admin/library" class="btn btn-outline">Browse Audio Library</a>
                <a href="/admin/shows" class="btn btn-outline">DJ Shows</a>
                <a href="/admin/schedule" class="btn btn-outline">View Schedule</a>
            </div>
        </div>

        <!-- Two column: Station status + Activity -->
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:24px;">
            <!-- Station status -->
            <div class="panel">
                <div class="panel-header">
                    <h2>Station Status</h2>
                    <a href="/admin/stations" class="btn btn-sm btn-outline">View All</a>
                </div>
                <div class="panel-body" style="padding:0;">
                    <table class="data-table" style="border:none; margin:0;">
                        <thead>
                            <tr><th>Station</th><th>Port</th><th>Status</th></tr>
                        </thead>
                        <tbody>
                        {% for key, s in stations.items() %}
                            <tr>
                                <td>{{ s.name }}{% if s.custom %} <span class="badge badge-custom">Custom</span>{% endif %}</td>
                                <td style="font-family:monospace; color:rgba(255,255,255,0.5);">:{{ s.port }}</td>
                                <td>
                                    {% if s.live %}
                                        <span class="badge badge-live">LIVE</span>
                                    {% else %}
                                        <span class="badge badge-offline">OFFLINE</span>
                                    {% endif %}
                                </td>
                            </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Activity log -->
            <div class="panel">
                <div class="panel-header">
                    <h2>Recent Activity</h2>
                </div>
                <div class="panel-body">
                    {% if activity_log %}
                        <ul class="activity-list">
                        {% for item in activity_log[:15] %}
                            <li class="activity-item">
                                <span class="activity-time">{{ item.time }}</span>
                                <span class="activity-text"><strong>{{ item.action }}</strong> {{ item.detail }}</span>
                            </li>
                        {% endfor %}
                        </ul>
                    {% else %}
                        <div style="text-align:center; padding:40px 0; color:rgba(255,255,255,0.3);">
                            No activity recorded yet this session.<br>
                            <span style="font-size:12px;">Actions will appear here as you manage the platform.</span>
                        </div>
                    {% endif %}
                </div>
            </div>
        </div>
    ''')

    # Get current show info for display
    current_show = SHOWS.get(current_block, {})
    current_show_label = current_show.get('label', current_block.replace('_', ' ').title())
    current_show_time = current_show.get('time', '')

    return render_template_string(html,
        page_title='CMS Dashboard',
        active_page='dashboard',
        now=datetime.now().strftime('%Y-%m-%d %H:%M'),
        flash_msg=request.args.get('msg'),
        flash_type='flash-success' if not request.args.get('err') else 'flash-error',
        live_count=live_count,
        total_stations=total_stations,
        total_audio=total_audio,
        total_playlists=total_playlists,
        total_tracks=total_tracks,
        current_block_label=current_show_label,
        current_show_time=current_show_time,
        stations=stations,
        activity_log=_activity_log,
    )


# ---------------------------------------------------------------------------
# Route: Station Management
# ---------------------------------------------------------------------------

@cms_bp.route('/stations')
def stations_page():
    stations = _get_all_stations()
    next_port = max([s['port'] for s in stations.values()] + [8009]) + 1

    html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
        <div class="stat-cards">
            <div class="stat-card">
                <div class="stat-label">Total Stations</div>
                <div class="stat-value">{{ total }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Live</div>
                <div class="stat-value" style="color:#00ff88;">{{ live }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Offline</div>
                <div class="stat-value" style="color:#e94560;">{{ offline }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Custom Stations</div>
                <div class="stat-value">{{ custom_count }}</div>
            </div>
        </div>

        <!-- Station list -->
        <div class="panel">
            <div class="panel-header">
                <h2>All Stations</h2>
            </div>
            <div class="panel-body" style="padding:0;">
                <table class="data-table" style="border:none; margin:0;">
                    <thead>
                        <tr>
                            <th>Station</th><th>Key</th><th>Port</th><th>Market</th><th>Status</th><th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                    {% for key, s in stations.items() %}
                        <tr>
                            <td>
                                <strong style="color:#fff;">{{ s.name }}</strong>
                                {% if s.custom %} <span class="badge badge-custom">Custom</span>{% endif %}
                                {% if s.tagline %}<br><span style="font-size:11px; color:rgba(255,255,255,0.4);">{{ s.tagline }}</span>{% endif %}
                            </td>
                            <td style="font-family:monospace; font-size:12px; color:rgba(255,255,255,0.5);">{{ key }}</td>
                            <td style="font-family:monospace;">:{{ s.port }}</td>
                            <td>{{ s.market }}</td>
                            <td>
                                {% if s.live %}
                                    <span class="badge badge-live">LIVE</span>
                                {% else %}
                                    <span class="badge badge-offline">OFFLINE</span>
                                {% endif %}
                            </td>
                            <td>
                                {% if s.live %}
                                    <form method="POST" action="/admin/stations/{{ key }}/stop" class="inline-form">
                                        <button type="submit" class="btn btn-sm btn-danger">Stop</button>
                                    </form>
                                {% else %}
                                    <form method="POST" action="/admin/stations/{{ key }}/start" class="inline-form">
                                        <button type="submit" class="btn btn-sm btn-success">Start</button>
                                    </form>
                                {% endif %}
                                <a href="/admin/playlists/{{ key }}" class="btn btn-sm btn-outline">Playlist</a>
                            </td>
                        </tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Create new station -->
        <div class="panel">
            <div class="panel-header">
                <h2>Create New Custom Station</h2>
            </div>
            <div class="panel-body">
                <form method="POST" action="/admin/stations/create">
                    <div class="form-row">
                        <div class="form-group">
                            <label>Station Name</label>
                            <input type="text" name="name" class="form-control" placeholder="Power 103.5 Dallas" required>
                        </div>
                        <div class="form-group">
                            <label>Station Key (lowercase, no spaces)</label>
                            <input type="text" name="key" class="form-control" placeholder="dallas" required pattern="[a-z0-9_]+">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Port</label>
                            <input type="number" name="port" class="form-control" value="{{ next_port }}" required>
                        </div>
                        <div class="form-group">
                            <label>Market</label>
                            <input type="text" name="market" class="form-control" placeholder="Dallas">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Frequency</label>
                            <input type="text" name="freq" class="form-control" placeholder="103.5">
                        </div>
                        <div class="form-group">
                            <label>Tagline</label>
                            <input type="text" name="tagline" class="form-control" placeholder="The Lone Star sound.">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary mt-12">Create Station</button>
                </form>
            </div>
        </div>
    ''')

    live = sum(1 for s in stations.values() if s['live'])
    custom_count = sum(1 for s in stations.values() if s.get('custom'))

    return render_template_string(html,
        page_title='Station Management',
        active_page='stations',
        now=datetime.now().strftime('%Y-%m-%d %H:%M'),
        flash_msg=request.args.get('msg'),
        flash_type='flash-success' if not request.args.get('err') else 'flash-error',
        stations=stations,
        total=len(stations),
        live=live,
        offline=len(stations) - live,
        custom_count=custom_count,
        next_port=next_port,
    )


@cms_bp.route('/stations/<key>/start', methods=['POST'])
def station_start(key):
    try:
        result = subprocess.run(
            [ICECAST_VENV_PYTHON, ICECAST_STATIONS_PY, '--start', key],
            capture_output=True, text=True, timeout=15,
            cwd=ICECAST_DIR,
        )
        if result.returncode == 0:
            _log_activity('Station Started', key)
            return redirect('/admin/stations?msg=Station+%s+started' % key)
        else:
            err = result.stderr.strip()[:200] if result.stderr else 'Unknown error'
            _log_activity('Station Start Failed', '%s: %s' % (key, err))
            return redirect('/admin/stations?msg=Start+failed:+%s&err=1' % err[:80])
    except subprocess.TimeoutExpired:
        _log_activity('Station Start Timeout', key)
        return redirect('/admin/stations?msg=Start+timed+out+for+%s&err=1' % key)
    except Exception as e:
        _log_activity('Station Start Error', '%s: %s' % (key, str(e)))
        return redirect('/admin/stations?msg=Error:+%s&err=1' % str(e)[:80])


@cms_bp.route('/stations/<key>/stop', methods=['POST'])
def station_stop(key):
    try:
        result = subprocess.run(
            [ICECAST_VENV_PYTHON, ICECAST_STATIONS_PY, '--stop', key],
            capture_output=True, text=True, timeout=15,
            cwd=ICECAST_DIR,
        )
        if result.returncode == 0:
            _log_activity('Station Stopped', key)
            return redirect('/admin/stations?msg=Station+%s+stopped' % key)
        else:
            err = result.stderr.strip()[:200] if result.stderr else 'Unknown error'
            _log_activity('Station Stop Failed', '%s: %s' % (key, err))
            return redirect('/admin/stations?msg=Stop+failed:+%s&err=1' % err[:80])
    except subprocess.TimeoutExpired:
        _log_activity('Station Stop Timeout', key)
        return redirect('/admin/stations?msg=Stop+timed+out+for+%s&err=1' % key)
    except Exception as e:
        _log_activity('Station Stop Error', '%s: %s' % (key, str(e)))
        return redirect('/admin/stations?msg=Error:+%s&err=1' % str(e)[:80])


@cms_bp.route('/stations/create', methods=['POST'])
def station_create():
    key = request.form.get('key', '').strip().lower().replace(' ', '_')
    name = request.form.get('name', '').strip()
    port = request.form.get('port', '0').strip()
    market = request.form.get('market', '').strip()
    freq = request.form.get('freq', '').strip()
    tagline = request.form.get('tagline', '').strip()

    if not key or not name:
        return redirect('/admin/stations?msg=Station+key+and+name+required&err=1')

    # Check for duplicate key
    all_stations = _get_all_stations()
    if key in all_stations:
        return redirect('/admin/stations?msg=Station+key+already+exists&err=1')

    try:
        port = int(port)
    except ValueError:
        return redirect('/admin/stations?msg=Invalid+port+number&err=1')

    custom = _load_custom_stations()
    custom[key] = {
        'name': name,
        'port': port,
        'market': market,
        'freq': freq,
        'station_id_pattern': name.replace(' ', '_'),
        'fallback_patterns': ['Power_FM'],
        'tagline': tagline,
        'created_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }
    _save_custom_stations(custom)
    _log_activity('Station Created', '%s (%s) on port %d' % (name, key, port))
    return redirect('/admin/stations?msg=Station+%s+created+successfully' % name.replace(' ', '+'))


# ---------------------------------------------------------------------------
# Route: Playlist Management
# ---------------------------------------------------------------------------

@cms_bp.route('/playlists')
def playlists_page():
    playlists = _get_playlist_files()

    # Build summary per station key
    playlist_summary = []
    for skey in sorted(playlists.keys()):
        pl_list = playlists[skey]
        # Use the latest playlist (most recent date)
        latest = sorted(pl_list, key=lambda x: x['date'], reverse=True)[0] if pl_list else None
        track_count = 0
        if latest:
            tracks = _parse_m3u(latest['path'])
            track_count = len(tracks)
        playlist_summary.append({
            'key': skey,
            'label': skey.replace('_', ' ').title(),
            'count': len(pl_list),
            'latest_date': latest['date'] if latest else 'N/A',
            'track_count': track_count,
            'latest_file': latest['filename'] if latest else '',
        })

    html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
        <div class="stat-cards">
            <div class="stat-card">
                <div class="stat-label">Station Playlists</div>
                <div class="stat-value">{{ playlists|length }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Files</div>
                <div class="stat-value">{{ total_files }}</div>
            </div>
        </div>

        <div class="panel">
            <div class="panel-header">
                <h2>All Playlists</h2>
            </div>
            <div class="panel-body" style="padding:0;">
                <table class="data-table" style="border:none; margin:0;">
                    <thead>
                        <tr><th>Station / Block</th><th>Files</th><th>Tracks</th><th>Latest Date</th><th>Actions</th></tr>
                    </thead>
                    <tbody>
                    {% for pl in playlists %}
                        <tr>
                            <td><strong style="color:#fff;">{{ pl.label }}</strong></td>
                            <td>{{ pl.count }}</td>
                            <td>{{ pl.track_count }}</td>
                            <td style="color:rgba(255,255,255,0.5);">{{ pl.latest_date }}</td>
                            <td>
                                <a href="/admin/playlists/{{ pl.key }}" class="btn btn-sm btn-primary">Edit Playlist</a>
                            </td>
                        </tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    ''')

    total_files = sum(p['count'] for p in playlist_summary)

    return render_template_string(html,
        page_title='Playlist Management',
        active_page='playlists',
        now=datetime.now().strftime('%Y-%m-%d %H:%M'),
        flash_msg=request.args.get('msg'),
        flash_type='flash-success' if not request.args.get('err') else 'flash-error',
        playlists=playlist_summary,
        total_files=total_files,
    )


@cms_bp.route('/playlists/<station_key>')
def playlist_edit(station_key):
    playlists = _get_playlist_files()
    pl_list = playlists.get(station_key, [])

    if not pl_list:
        return redirect('/admin/playlists?msg=No+playlist+found+for+%s&err=1' % station_key)

    # Use the latest
    latest = sorted(pl_list, key=lambda x: x['date'], reverse=True)[0]
    tracks = _parse_m3u(latest['path'])

    # Get available audio files for "add track" selector
    available_audio = _get_audio_files()

    html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
        <div class="page-header-row">
            <div>
                <h2 class="page-heading">{{ station_label }}</h2>
                <div class="page-subtitle">{{ filename }} &mdash; {{ tracks|length }} tracks</div>
            </div>
            <a href="/admin/playlists" class="btn btn-outline">Back to Playlists</a>
        </div>

        <!-- Track list -->
        <div class="panel">
            <div class="panel-header">
                <h2>Tracks ({{ tracks|length }})</h2>
            </div>
            <div class="panel-body" style="padding:0;">
                <ul class="track-list" id="track-list">
                {% for track in tracks %}
                    <li class="track-item" data-idx="{{ loop.index0 }}">
                        <span class="track-num">{{ loop.index }}</span>
                        <div class="track-info">
                            <div class="track-title">{{ track.title }}</div>
                            <div class="track-path">{{ track.filename }}
                                {% if track.source == 'elevenlabs' %}
                                    <span class="badge badge-elevenlabs" class="ml-4">AI Voice</span>
                                {% elif track.source == 'youtube' %}
                                    <span class="badge badge-youtube" class="ml-4">YouTube</span>
                                {% endif %}
                                {% if not track.exists %}
                                    <span class="badge badge-offline" class="ml-4">FILE MISSING</span>
                                {% endif %}
                            </div>
                        </div>
                        <div class="track-actions">
                            {% if track.exists %}
                                <audio controls preload="none">
                                    <source src="/admin/audio-proxy?path={{ track.path|urlencode }}" type="audio/mpeg">
                                </audio>
                            {% endif %}
                            {% if loop.index0 > 0 %}
                                <form method="POST" action="/admin/playlists/{{ station_key }}/reorder" class="inline-form">
                                    <input type="hidden" name="from_idx" value="{{ loop.index0 }}">
                                    <input type="hidden" name="to_idx" value="{{ loop.index0 - 1 }}">
                                    <button type="submit" class="btn btn-sm btn-outline" title="Move up">&#9650;</button>
                                </form>
                            {% endif %}
                            {% if loop.index0 < tracks|length - 1 %}
                                <form method="POST" action="/admin/playlists/{{ station_key }}/reorder" class="inline-form">
                                    <input type="hidden" name="from_idx" value="{{ loop.index0 }}">
                                    <input type="hidden" name="to_idx" value="{{ loop.index0 + 1 }}">
                                    <button type="submit" class="btn btn-sm btn-outline" title="Move down">&#9660;</button>
                                </form>
                            {% endif %}
                            <form method="POST" action="/admin/playlists/{{ station_key }}/remove" class="inline-form">
                                <input type="hidden" name="idx" value="{{ loop.index0 }}">
                                <button type="submit" class="btn btn-sm btn-danger" title="Remove" onclick="return confirm('Remove this track?');">&#10005;</button>
                            </form>
                        </div>
                    </li>
                {% endfor %}
                </ul>
            </div>
        </div>

        <!-- Add track -->
        <div class="panel">
            <div class="panel-header">
                <h2>Add Track to Playlist</h2>
            </div>
            <div class="panel-body">
                <form method="POST" action="/admin/playlists/{{ station_key }}/add">
                    <div class="form-group">
                        <label>Select Audio File</label>
                        <select name="audio_path" class="form-control" required style="max-width:100%;">
                            <option value="">-- Select an audio file --</option>
                            <optgroup label="YouTube Extractions">
                            {% for af in available_audio %}
                                {% if af.source == 'YouTube Extraction' %}
                                    <option value="{{ af.path }}">{{ af.filename }} ({{ af.size_human }})</option>
                                {% endif %}
                            {% endfor %}
                            </optgroup>
                            <optgroup label="ElevenLabs AI Voice">
                            {% for af in available_audio %}
                                {% if af.source == 'ElevenLabs AI' %}
                                    <option value="{{ af.path }}">{{ af.filename }} ({{ af.size_human }})</option>
                                {% endif %}
                            {% endfor %}
                            </optgroup>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Custom Track Title (optional)</label>
                        <input type="text" name="title" class="form-control" placeholder="Leave blank to use filename">
                    </div>
                    <div class="form-group">
                        <label>Insert Position</label>
                        <select name="position" class="form-control" style="max-width:200px;">
                            <option value="end">End of playlist</option>
                            <option value="start">Start of playlist</option>
                            {% for i in range(tracks|length) %}
                                <option value="{{ i }}">After track {{ i + 1 }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <button type="submit" class="btn btn-primary">Add Track</button>
                </form>
            </div>
        </div>
    ''')

    return render_template_string(html,
        page_title='Edit Playlist: %s' % station_key.replace('_', ' ').title(),
        active_page='playlists',
        now=datetime.now().strftime('%Y-%m-%d %H:%M'),
        flash_msg=request.args.get('msg'),
        flash_type='flash-success' if not request.args.get('err') else 'flash-error',
        station_key=station_key,
        station_label=station_key.replace('_', ' ').title(),
        filename=latest['filename'],
        tracks=tracks,
        available_audio=available_audio,
    )


@cms_bp.route('/playlists/<station_key>/reorder', methods=['POST'])
def playlist_reorder(station_key):
    playlists = _get_playlist_files()
    pl_list = playlists.get(station_key, [])
    if not pl_list:
        return redirect('/admin/playlists?msg=Playlist+not+found&err=1')

    latest = sorted(pl_list, key=lambda x: x['date'], reverse=True)[0]
    tracks = _parse_m3u(latest['path'])

    try:
        from_idx = int(request.form.get('from_idx', -1))
        to_idx = int(request.form.get('to_idx', -1))
    except ValueError:
        return redirect('/admin/playlists/%s?msg=Invalid+indices&err=1' % station_key)

    if 0 <= from_idx < len(tracks) and 0 <= to_idx < len(tracks):
        track = tracks.pop(from_idx)
        tracks.insert(to_idx, track)
        _write_m3u(latest['path'], tracks, 'Power FM %s' % station_key.replace('_', ' ').title())
        _log_activity('Track Reordered', '%s: moved #%d to #%d' % (station_key, from_idx + 1, to_idx + 1))
        return redirect('/admin/playlists/%s?msg=Track+reordered' % station_key)

    return redirect('/admin/playlists/%s?msg=Invalid+track+position&err=1' % station_key)


@cms_bp.route('/playlists/<station_key>/remove', methods=['POST'])
def playlist_remove(station_key):
    playlists = _get_playlist_files()
    pl_list = playlists.get(station_key, [])
    if not pl_list:
        return redirect('/admin/playlists?msg=Playlist+not+found&err=1')

    latest = sorted(pl_list, key=lambda x: x['date'], reverse=True)[0]
    tracks = _parse_m3u(latest['path'])

    try:
        idx = int(request.form.get('idx', -1))
    except ValueError:
        return redirect('/admin/playlists/%s?msg=Invalid+index&err=1' % station_key)

    if 0 <= idx < len(tracks):
        removed = tracks.pop(idx)
        _write_m3u(latest['path'], tracks, 'Power FM %s' % station_key.replace('_', ' ').title())
        _log_activity('Track Removed', '%s: removed "%s"' % (station_key, removed.get('title', 'unknown')[:60]))
        return redirect('/admin/playlists/%s?msg=Track+removed' % station_key)

    return redirect('/admin/playlists/%s?msg=Invalid+track+index&err=1' % station_key)


@cms_bp.route('/playlists/<station_key>/add', methods=['POST'])
def playlist_add(station_key):
    playlists = _get_playlist_files()
    pl_list = playlists.get(station_key, [])
    if not pl_list:
        return redirect('/admin/playlists?msg=Playlist+not+found&err=1')

    latest = sorted(pl_list, key=lambda x: x['date'], reverse=True)[0]
    tracks = _parse_m3u(latest['path'])

    audio_path = request.form.get('audio_path', '').strip()
    title = request.form.get('title', '').strip()
    position = request.form.get('position', 'end').strip()

    if not audio_path:
        return redirect('/admin/playlists/%s?msg=No+audio+file+selected&err=1' % station_key)

    if not title:
        title = os.path.basename(audio_path).replace('.mp3', '').replace('_', ' ')

    new_track = {
        'title': title,
        'path': audio_path,
        'filename': os.path.basename(audio_path),
    }

    if position == 'end':
        tracks.append(new_track)
    elif position == 'start':
        tracks.insert(0, new_track)
    else:
        try:
            pos = int(position)
            tracks.insert(pos + 1, new_track)
        except ValueError:
            tracks.append(new_track)

    _write_m3u(latest['path'], tracks, 'Power FM %s' % station_key.replace('_', ' ').title())
    _log_activity('Track Added', '%s: added "%s"' % (station_key, title[:60]))
    return redirect('/admin/playlists/%s?msg=Track+added+successfully' % station_key)


# ---------------------------------------------------------------------------
# Route: Audio proxy (for inline playback)
# ---------------------------------------------------------------------------

@cms_bp.route('/audio-proxy')
def audio_proxy():
    """Serve audio files for inline preview in the admin panel."""
    filepath = request.args.get('path', '')
    # Security: only allow files from known audio directories
    allowed_prefixes = [YOUTUBE_EXTRACTIONS, ELEVENLABS_OUTPUT]
    safe = False
    for prefix in allowed_prefixes:
        if filepath.startswith(prefix) and '..' not in filepath:
            safe = True
            break

    if not safe or not os.path.exists(filepath):
        return 'Forbidden', 403

    from flask import send_file
    return send_file(filepath, mimetype='audio/mpeg')


# ---------------------------------------------------------------------------
# Route: Audio Asset Library
# ---------------------------------------------------------------------------

@cms_bp.route('/library')
def library_page():
    search_q = request.args.get('q', '').strip()
    files = _get_audio_files(search_query=search_q if search_q else None)

    yt_count = sum(1 for f in files if 'YouTube' in f['source'])
    el_count = sum(1 for f in files if 'ElevenLabs' in f['source'])
    total_size = sum(f['size'] for f in files)

    html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
        <div class="stat-cards">
            <div class="stat-card">
                <div class="stat-label">Total Audio Files</div>
                <div class="stat-value">{{ files|length }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">YouTube Extractions</div>
                <div class="stat-value" style="color:#ff4444;">{{ yt_count }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">ElevenLabs AI</div>
                <div class="stat-value" style="color:#b388ff;">{{ el_count }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Size</div>
                <div class="stat-value" style="font-size:20px;">{{ total_size_human }}</div>
            </div>
        </div>

        <!-- Search -->
        <form method="GET" action="/admin/library" class="search-bar">
            <input type="text" name="q" class="form-control" placeholder="Search audio files by filename..." value="{{ search_q }}">
            <button type="submit" class="btn btn-primary">Search</button>
            {% if search_q %}
                <a href="/admin/library" class="btn btn-outline">Clear</a>
            {% endif %}
        </form>

        {% if search_q %}
            <div style="margin-bottom:16px; font-size:13px; color:rgba(255,255,255,0.5);">
                Showing {{ files|length }} results for &ldquo;{{ search_q }}&rdquo;
            </div>
        {% endif %}

        <!-- File list -->
        <div class="panel">
            <div class="panel-header">
                <h2>Audio Files</h2>
            </div>
            <div class="panel-body" style="padding:0;">
                <table class="data-table" style="border:none; margin:0;">
                    <thead>
                        <tr><th>Filename</th><th>Source</th><th>Size</th><th>Modified</th><th>Preview</th></tr>
                    </thead>
                    <tbody>
                    {% for f in files %}
                        <tr>
                            <td>
                                <div style="max-width:400px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                                    <strong style="color:#fff;">{{ f.filename }}</strong>
                                </div>
                                <div style="font-size:11px; color:rgba(255,255,255,0.25); max-width:400px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{{ f.path }}</div>
                            </td>
                            <td>
                                {% if 'YouTube' in f.source %}
                                    <span class="badge badge-youtube">YouTube</span>
                                {% else %}
                                    <span class="badge badge-elevenlabs">ElevenLabs</span>
                                {% endif %}
                            </td>
                            <td style="white-space:nowrap;">{{ f.size_human }}</td>
                            <td style="white-space:nowrap; color:rgba(255,255,255,0.5);">{{ f.modified }}</td>
                            <td>
                                <audio controls preload="none" style="height:28px; width:180px;">
                                    <source src="/admin/audio-proxy?path={{ f.path|urlencode }}" type="audio/mpeg">
                                </audio>
                            </td>
                        </tr>
                    {% endfor %}
                    {% if not files %}
                        <tr>
                            <td colspan="5" style="text-align:center; padding:40px; color:rgba(255,255,255,0.3);">
                                {% if search_q %}
                                    No audio files match &ldquo;{{ search_q }}&rdquo;
                                {% else %}
                                    No audio files found in the extraction directories.
                                {% endif %}
                            </td>
                        </tr>
                    {% endif %}
                    </tbody>
                </table>
            </div>
        </div>
    ''')

    return render_template_string(html,
        page_title='Audio Library',
        active_page='library',
        now=datetime.now().strftime('%Y-%m-%d %H:%M'),
        flash_msg=request.args.get('msg'),
        flash_type='flash-success' if not request.args.get('err') else 'flash-error',
        files=files,
        yt_count=yt_count,
        el_count=el_count,
        total_size_human=_format_size(total_size),
        search_q=search_q,
    )


@cms_bp.route('/library/search')
def library_search():
    """Redirect search to library page with query param."""
    q = request.args.get('q', '')
    return redirect('/admin/library?q=%s' % q)


# ---------------------------------------------------------------------------
# Route: Schedule Management
# ---------------------------------------------------------------------------

@cms_bp.route('/schedule')
def schedule_page():
    current_block = _get_current_block_name()

    # Colors for schedule blocks
    block_colors = [
        '#e94560',  # morning
        '#ff8800',  # midday
        '#ffb700',  # afternoon
        '#00c853',  # evening
        '#536dfe',  # late night
        '#7c4dff',  # overnight
    ]

    html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
        <!-- Visual timeline -->
        <div class="panel" style="margin-bottom:24px;">
            <div class="panel-header"><h2>24-Hour Schedule Timeline</h2></div>
            <div class="panel-body">
                <div class="schedule-timeline">
                    {% for i, block in enumerate(blocks) %}
                        {% set hours = block.end_hour - block.start_hour if block.end_hour > block.start_hour else (24 - block.start_hour + block.end_hour) %}
                        {% set width_pct = (hours / 24.0) * 100 %}
                        <div class="schedule-block-vis {{ 'active-block' if block.name == current_block else '' }}"
                             style="width:{{ width_pct }}%; background:{{ colors[i] }};">
                            {{ block.label }}
                        </div>
                    {% endfor %}
                </div>
                <div style="display:flex; justify-content:space-between; font-size:11px; color:rgba(255,255,255,0.3); margin-top:4px;">
                    <span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>12am</span>
                </div>
            </div>
        </div>

        <!-- Blocks table -->
        <div class="panel">
            <div class="panel-header"><h2>Schedule Blocks</h2></div>
            <div class="panel-body" style="padding:0;">
                <table class="data-table" style="border:none; margin:0;">
                    <thead>
                        <tr>
                            <th>Block</th><th>Hours</th><th>Vibe</th>
                            <th>Tracks</th><th>Shuffle</th><th>Station IDs</th><th>Promos</th><th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                    {% for block in blocks %}
                        <tr>
                            <td>
                                <strong style="color:#fff;">{{ block.label }}</strong>
                            </td>
                            <td style="font-family:monospace;">
                                {{ '%02d' % block.start_hour }}:00 - {{ '%02d' % (block.end_hour if block.end_hour < 24 else 0) }}:00
                            </td>
                            <td style="color:rgba(255,255,255,0.6);">{{ block.vibe }}</td>
                            <td>{{ block.track_limit }}</td>
                            <td>
                                {% if block.shuffle %}
                                    <span class="badge badge-live">Yes</span>
                                {% else %}
                                    <span style="color:rgba(255,255,255,0.3);">No</span>
                                {% endif %}
                            </td>
                            <td>Every {{ block.station_id_every }} tracks</td>
                            <td>
                                {% if block.promos == 'heavy' %}
                                    <span class="badge badge-custom">Heavy</span>
                                {% elif block.promos == 'standard' %}
                                    <span class="badge badge-active">Standard</span>
                                {% elif block.promos == 'minimal' %}
                                    <span style="color:rgba(255,255,255,0.5);">Minimal</span>
                                {% else %}
                                    <span style="color:rgba(255,255,255,0.3);">None</span>
                                {% endif %}
                            </td>
                            <td>
                                {% if block.name == current_block %}
                                    <span class="badge badge-live">ON AIR</span>
                                {% else %}
                                    <span style="color:rgba(255,255,255,0.3);">Scheduled</span>
                                {% endif %}
                            </td>
                        </tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Current show info -->
        <div class="panel">
            <div class="panel-header"><h2>Currently On Air</h2></div>
            <div class="panel-body">
                <div style="display:flex; align-items:center; gap:20px;">
                    <div class="dj-avatar" style="width:60px; height:60px; font-size:24px;">
                        {{ current_dj_initial }}
                    </div>
                    <div>
                        <div style="font-size:20px; font-weight:700; color:#fff;">{{ current_show_label }}</div>
                        <div style="font-size:14px; color:#e94560; font-weight:600;">{{ current_show_time }}</div>
                        <div style="font-size:13px; color:rgba(255,255,255,0.5); margin-top:4px;">
                            DJ: {{ current_dj_name }} &mdash; {{ current_tagline }}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    ''')

    current_show = SHOWS.get(current_block, {})
    dj_key = current_show.get('dj', '')
    dj = DJS.get(dj_key, {})

    return render_template_string(html,
        page_title='Schedule Management',
        active_page='schedule',
        now=datetime.now().strftime('%Y-%m-%d %H:%M'),
        flash_msg=request.args.get('msg'),
        flash_type='flash-success' if not request.args.get('err') else 'flash-error',
        blocks=SCHEDULE_BLOCKS,
        colors=block_colors,
        current_block=current_block,
        current_show_label=current_show.get('label', 'Unknown'),
        current_show_time=current_show.get('time', ''),
        current_tagline=current_show.get('tagline', ''),
        current_dj_name=dj.get('name', 'Auto'),
        current_dj_initial=dj.get('name', 'A')[0] if dj.get('name') else 'A',
        enumerate=enumerate,
    )


# ---------------------------------------------------------------------------
# Route: DJ Show Management
# ---------------------------------------------------------------------------

@cms_bp.route('/shows')
def shows_page():
    current_block = _get_current_block_name()

    html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
        <div class="stat-cards">
            <div class="stat-card">
                <div class="stat-label">DJ Profiles</div>
                <div class="stat-value">{{ djs|length }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Shows</div>
                <div class="stat-value">{{ shows|length }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Currently On Air</div>
                <div class="stat-value" style="font-size:16px; color:#00ff88;">{{ current_show_label }}</div>
            </div>
        </div>

        <!-- Show cards -->
        <h3 style="color:#fff; margin-bottom:16px;">Show Schedule</h3>
        <div class="show-grid">
        {% for show_key, show in shows.items() %}
            {% set dj = djs.get(show.dj, {}) %}
            <div class="show-card {{ 'show-live' if show_key == current_block else '' }}">
                {% if show_key == current_block %}
                    <div style="margin-bottom:8px;"><span class="badge badge-live">ON AIR NOW</span></div>
                {% endif %}
                <div class="show-name">{{ show.label }}</div>
                <div class="show-time">{{ show.time }}</div>
                <div class="show-tagline">{{ show.tagline }}</div>
                <div class="dj-info">
                    <div class="dj-avatar">{{ dj.name[0] if dj.name else '?' }}</div>
                    <div>
                        <div class="dj-name">{{ dj.name or 'Unknown' }}</div>
                        <div class="dj-style">{{ dj.style or '' }} &mdash; Voice: {{ dj.voice or 'N/A' }}</div>
                    </div>
                </div>
            </div>
        {% endfor %}
        </div>

        <!-- DJ Profiles -->
        <h3 style="color:#fff; margin:32px 0 16px;">DJ Profiles</h3>
        <div class="panel">
            <div class="panel-body" style="padding:0;">
                <table class="data-table" style="border:none; margin:0;">
                    <thead>
                        <tr><th>DJ</th><th>Key</th><th>Voice</th><th>Style</th><th>Bio</th></tr>
                    </thead>
                    <tbody>
                    {% for dj_key, dj in djs.items() %}
                        <tr>
                            <td>
                                <div style="display:flex; align-items:center; gap:10px;">
                                    <div class="dj-avatar" style="width:32px; height:32px; font-size:14px;">{{ dj.name[0] }}</div>
                                    <strong style="color:#fff;">{{ dj.name }}</strong>
                                </div>
                            </td>
                            <td style="font-family:monospace; font-size:12px; color:rgba(255,255,255,0.5);">{{ dj_key }}</td>
                            <td>{{ dj.voice }}</td>
                            <td><span class="badge badge-custom">{{ dj.style }}</span></td>
                            <td style="color:rgba(255,255,255,0.6); max-width:300px;">{{ dj.bio }}</td>
                        </tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    ''')

    current_show = SHOWS.get(current_block, {})

    return render_template_string(html,
        page_title='DJ Shows',
        active_page='shows',
        now=datetime.now().strftime('%Y-%m-%d %H:%M'),
        flash_msg=request.args.get('msg'),
        flash_type='flash-success' if not request.args.get('err') else 'flash-error',
        shows=SHOWS,
        djs=DJS,
        current_block=current_block,
        current_show_label=current_show.get('label', 'Unknown'),
    )


# ---------------------------------------------------------------------------
# API Route: Admin Stats JSON
# ---------------------------------------------------------------------------

@cms_bp.route('/api/admin/stats')
def api_admin_stats():
    """Return JSON stats for the admin dashboard (for AJAX polling)."""
    stations = _get_all_stations()
    live_count = sum(1 for s in stations.values() if s['live'])

    audio_files = _get_audio_files()
    playlists = _get_playlist_files()
    total_playlists = sum(len(v) for v in playlists.values())
    total_tracks = 0
    for key, pl_list in playlists.items():
        for pl in pl_list:
            tracks = _parse_m3u(pl['path'])
            total_tracks += len(tracks)

    current_block = _get_current_block_name()
    current_show = SHOWS.get(current_block, {})
    dj_key = current_show.get('dj', '')
    dj = DJS.get(dj_key, {})

    data = {
        'stations': {
            'total': len(stations),
            'live': live_count,
            'offline': len(stations) - live_count,
            'details': {
                k: {'name': v['name'], 'port': v['port'], 'live': v['live'], 'custom': v.get('custom', False)}
                for k, v in stations.items()
            },
        },
        'audio': {
            'total_files': len(audio_files),
            'youtube_count': sum(1 for f in audio_files if 'YouTube' in f['source']),
            'elevenlabs_count': sum(1 for f in audio_files if 'ElevenLabs' in f['source']),
            'total_size_bytes': sum(f['size'] for f in audio_files),
        },
        'playlists': {
            'total_files': total_playlists,
            'total_tracks': total_tracks,
        },
        'schedule': {
            'current_block': current_block,
            'current_show': current_show.get('label', ''),
            'current_dj': dj.get('name', ''),
            'current_time_slot': current_show.get('time', ''),
        },
        'activity_log': _activity_log[:10],
        'timestamp': datetime.now().isoformat(),
    }
    return jsonify(data)
