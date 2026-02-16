#!/usr/bin/env python3
"""
Power FM Platform Hub — Web Dashboard
Serves a real-time web dashboard at http://localhost:5560 showing data
from all 6 API connector agent databases plus the platform hub's own DB.

Usage:
    venv/bin/python dashboard.py
    # or from agent.py:
    venv/bin/python agent.py --web
"""

import glob
import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from cms import cms_bp

app = Flask(__name__)
app.secret_key = os.environ.get('POWER_FM_SECRET_KEY', 'pfm-secret-2026-change-in-prod')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
app.register_blueprint(cms_bp)

# --- Global Navigation Bar (injected into all templates) ---
NAV_CSS = """
<style>
.pfm-topnav { background:#0f3460; padding:10px 16px; display:flex; align-items:center; gap:6px; flex-wrap:wrap; position:sticky; top:0; z-index:9999; border-bottom:3px solid #e94560; }
.pfm-topnav .logo { color:#e94560; font-weight:900; font-size:18px; letter-spacing:2px; margin-right:10px; text-decoration:none; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }
.pfm-topnav a.btn { background:#16213e; color:#ddd; text-decoration:none; padding:8px 16px; border-radius:8px; font-size:14px; font-weight:700; letter-spacing:0.5px; transition:all .15s; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }
.pfm-topnav a.btn:hover { background:#e94560; color:#fff; transform:scale(1.05); }
</style>
"""
NAV_HTML = """
<nav class="pfm-topnav">
    <a href="/" class="logo">POWER FM</a>
    <a href="/" class="btn">Dashboard</a>
    <a href="/#charts" class="btn">Charts</a>
    <a href="/network" class="btn">Network</a>
    <a href="/youtube" class="btn">YouTube</a>
    <a href="/artists" class="btn">Artists</a>
    <a href="/station/national" class="btn">Radio</a>
    <a href="/shows" class="btn">Shows</a>
    <a href="/admin/schedule" class="btn">Schedule</a>
    <a href="/request" class="btn">Requests</a>
    <a href="/membership" class="btn">Membership</a>
    <a href="/admin/" class="btn">Admin</a>
</nav>
"""


@app.after_request
def inject_nav(response):
    """Inject global navigation bar into every HTML page."""
    if response.content_type and 'text/html' in response.content_type:
        data = response.get_data(as_text=True)
        if '<body>' in data and 'pfm-topnav' not in data and 'class="sidebar"' not in data:
            data = data.replace('</head>', NAV_CSS + '</head>')
            data = data.replace('<body>', '<body>' + NAV_HTML)
            response.set_data(data)
    return response


# --- Station stream ports (from icecast-agent stations.py) ---
STATION_PORTS = {
    'national': 8000, 'la': 8001, 'nyc': 8002, 'chicago': 8003,
    'miami': 8004, 'atlanta': 8005, 'houston': 8006, 'london': 8007, 'lagos': 8008,
    'dallas': 8009,
}

STATION_NAMES = {
    'national': 'Power FM', 'la': 'Power 106 LA', 'nyc': 'Power 105.1 NYC',
    'chicago': 'Power 92 Chicago', 'miami': 'Power 96 Miami',
    'atlanta': 'Power 107.5 Atlanta', 'houston': 'Power 104 Houston',
    'london': 'Power FM London', 'lagos': 'Power FM Lagos',
    'dallas': 'Power 103.5 Dallas',
}

# --- Paths ---
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(AGENT_DIR)

AGENT_DBS = {
    'chartmetric': os.path.join(AGENTS_DIR, 'chartmetric-agent', 'data', 'chartmetric.db'),
    'elevenlabs': os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'data', 'elevenlabs.db'),
    'youtube': os.path.join(AGENTS_DIR, 'youtube-agent', 'data', 'youtube.db'),
    'icecast': os.path.join(AGENTS_DIR, 'icecast-agent', 'data', 'icecast.db'),
    'spotify': os.path.join(AGENTS_DIR, 'spotify-agent', 'data', 'spotify.db'),
    'stripe': os.path.join(AGENTS_DIR, 'stripe-agent', 'data', 'stripe.db'),
}

HUB_DB = os.path.join(AGENT_DIR, 'data', 'platform_hub.db')

# Tables to count per agent for record totals
AGENT_TABLES = {
    'chartmetric': ['artists', 'chart_entries', 'streaming_stats', 'radio_spins', 'social_metrics', 'playlists'],
    'elevenlabs': ['voices', 'generations', 'station_ids', 'ad_reads', 'templates'],
    'youtube': ['channels', 'videos', 'analytics', 'audio_extractions', 'playlists', 'comments'],
    'icecast': ['servers', 'mount_points', 'listeners', 'source_connections', 'stream_health', 'alerts'],
    'spotify': ['artists', 'tracks', 'streams', 'playlists', 'playlist_tracks', 'demographics', 'audio_features'],
    'stripe': ['customers', 'subscriptions', 'payments', 'products', 'prices', 'invoices'],
}

# Power FM layer mapping
LAYERS = {
    2: {'name': 'Distribution', 'agents': ['youtube', 'spotify']},
    3: {'name': 'YouTube-to-FM Bridge', 'agents': ['youtube']},
    4: {'name': 'Transmitter Network', 'agents': ['icecast']},
    5: {'name': 'AI Localization', 'agents': ['elevenlabs']},
    7: {'name': 'Power Charts', 'agents': ['chartmetric', 'spotify']},
    8: {'name': 'Subcarrier Paywall', 'agents': ['stripe']},
}


def _open_ro(db_path):
    """Open a database read-only. Returns conn or None."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _safe_query(conn, sql, params=(), default=None):
    """Execute a query safely, returning default on any error."""
    if not conn:
        return default
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return default if default is not None else []


def _safe_scalar(conn, sql, params=(), default=0):
    """Execute a scalar query safely."""
    if not conn:
        return default
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def _format_number(n):
    """Format a number with commas."""
    if n is None:
        return 'N/A'
    try:
        return f'{int(n):,}'
    except (ValueError, TypeError):
        return str(n)


def _format_dollars(cents):
    """Format cents as dollar string."""
    if cents is None:
        return '$0.00'
    try:
        return f'${int(cents) / 100:,.2f}'
    except (ValueError, TypeError):
        return '$0.00'


def _format_ago(iso_timestamp):
    """Format an ISO timestamp as relative time."""
    if not iso_timestamp:
        return 'never'
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        delta = datetime.utcnow() - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return 'just now'
        elif minutes < 60:
            return f'{minutes}m ago'
        elif minutes < 1440:
            return f'{minutes // 60}h ago'
        else:
            return f'{minutes // 1440}d ago'
    except (ValueError, TypeError):
        return 'unknown'


def _get_db_size(path):
    """Get file size in bytes."""
    try:
        return os.path.getsize(path) if os.path.exists(path) else 0
    except OSError:
        return 0


def _format_size(b):
    """Format bytes as human string."""
    if b < 1024:
        return f'{b} B'
    elif b < 1024 * 1024:
        return f'{b / 1024:.1f} KB'
    else:
        return f'{b / (1024 * 1024):.1f} MB'


def gather_dashboard_data():
    """Collect all data needed for the dashboard from the agent databases."""
    data = {}

    # --- Open all databases ---
    yt = _open_ro(AGENT_DBS['youtube'])
    st = _open_ro(AGENT_DBS['stripe'])
    el = _open_ro(AGENT_DBS['elevenlabs'])
    sp = _open_ro(AGENT_DBS['spotify'])
    cm = _open_ro(AGENT_DBS['chartmetric'])
    ic = _open_ro(AGENT_DBS['icecast'])
    hub = _open_ro(HUB_DB)

    # =====================================================
    # SUMMARY CARDS
    # =====================================================

    # YouTube card
    yt_channels = _safe_scalar(yt, "SELECT COUNT(*) FROM channels")
    yt_videos = _safe_scalar(yt, "SELECT COUNT(*) FROM videos")
    yt_total_views = _safe_scalar(yt, "SELECT COALESCE(SUM(view_count), 0) FROM channels")
    data['yt_channels'] = yt_channels
    data['yt_videos'] = yt_videos
    data['yt_total_views'] = yt_total_views
    data['yt_online'] = yt is not None

    # Stripe card
    st_customers = _safe_scalar(st, "SELECT COUNT(*) FROM customers")
    st_active_subs = _safe_scalar(st, "SELECT COUNT(*) FROM subscriptions WHERE status = 'active'")
    # Calculate MRR
    mrr_cents = 0
    if st:
        subs_rows = _safe_query(st, """
            SELECT pr.unit_amount_cents, pr.recurring_interval, pr.recurring_interval_count
            FROM subscriptions s
            LEFT JOIN prices pr ON s.price_id = pr.stripe_id
            WHERE s.status = 'active'
        """)
        for s in subs_rows:
            amount = s['unit_amount_cents'] or 0
            interval = s['recurring_interval'] or 'month'
            ic_val = s['recurring_interval_count'] or 1
            if interval == 'year':
                mrr_cents += amount / (12 * ic_val)
            else:
                mrr_cents += amount / ic_val
    mrr_cents = int(mrr_cents)
    arr_cents = mrr_cents * 12
    data['st_customers'] = st_customers
    data['st_active_subs'] = st_active_subs
    data['st_mrr_cents'] = mrr_cents
    data['st_arr_cents'] = arr_cents
    data['st_online'] = st is not None

    # ElevenLabs card
    el_voices = _safe_scalar(el, "SELECT COUNT(*) FROM voices")
    el_generations = _safe_scalar(el, "SELECT COUNT(*) FROM generations")
    el_station_ids = _safe_scalar(el, "SELECT COUNT(*) FROM station_ids")
    data['el_voices'] = el_voices
    data['el_generations'] = el_generations
    data['el_station_ids'] = el_station_ids
    data['el_online'] = el is not None

    # Spotify card
    sp_artists = _safe_scalar(sp, "SELECT COUNT(*) FROM artists")
    sp_tracks = _safe_scalar(sp, "SELECT COUNT(*) FROM tracks")
    data['sp_artists'] = sp_artists
    data['sp_tracks'] = sp_tracks
    data['sp_online'] = sp is not None

    # =====================================================
    # YOUTUBE SECTION
    # =====================================================
    data['yt_channels_list'] = _safe_query(yt, """
        SELECT title, subscriber_count, video_count, view_count, custom_url
        FROM channels ORDER BY subscriber_count DESC
    """, default=[])

    data['yt_top_videos'] = _safe_query(yt, """
        SELECT v.title, v.view_count, v.like_count, v.comment_count,
               v.published_at, c.title as channel_title
        FROM videos v
        LEFT JOIN channels c ON v.channel_id = c.channel_id
        ORDER BY v.view_count DESC LIMIT 10
    """, default=[])

    # =====================================================
    # REVENUE SECTION (Stripe)
    # =====================================================

    # Subscription breakdown by product (tier)
    data['sub_breakdown'] = _safe_query(st, """
        SELECT
            COALESCE(p.name, 'Unknown') as tier_name,
            COUNT(*) as sub_count,
            SUM(CASE WHEN pr.recurring_interval = 'year'
                THEN CAST(pr.unit_amount_cents AS REAL) / (12 * COALESCE(pr.recurring_interval_count, 1))
                ELSE CAST(pr.unit_amount_cents AS REAL) / COALESCE(pr.recurring_interval_count, 1)
            END) as tier_mrr_cents
        FROM subscriptions s
        LEFT JOIN prices pr ON s.price_id = pr.stripe_id
        LEFT JOIN products p ON s.product_id = p.stripe_id
        WHERE s.status = 'active'
        GROUP BY COALESCE(p.name, 'Unknown')
        ORDER BY tier_mrr_cents DESC
    """, default=[])

    data['recent_payments'] = _safe_query(st, """
        SELECT pay.amount_cents, pay.currency, pay.status, pay.description,
               pay.payment_method, pay.created_at,
               c.name as customer_name, c.email as customer_email
        FROM payments pay
        LEFT JOIN customers c ON pay.customer_id = c.stripe_id
        ORDER BY pay.created_at DESC LIMIT 15
    """, default=[])

    # =====================================================
    # ELEVENLABS SECTION
    # =====================================================
    data['el_voices_list'] = _safe_query(el, """
        SELECT name, category, language FROM voices ORDER BY name
    """, default=[])

    data['el_generations_list'] = _safe_query(el, """
        SELECT g.id, g.text, g.status, g.duration_seconds, g.created_at,
               v.name as voice_name,
               CASE
                   WHEN si.id IS NOT NULL THEN 'Station ID'
                   WHEN ar.id IS NOT NULL THEN 'Ad Read'
                   ELSE 'Audio Generation'
               END as gen_type
        FROM generations g
        LEFT JOIN voices v ON g.voice_id = v.voice_id
        LEFT JOIN station_ids si ON si.generation_id = g.id
        LEFT JOIN ad_reads ar ON ar.generation_id = g.id
        ORDER BY g.created_at DESC LIMIT 20
    """, default=[])

    # =====================================================
    # AGENT STATUS SECTION
    # =====================================================
    agent_statuses = []
    for agent_name, db_path in sorted(AGENT_DBS.items()):
        aconn = _open_ro(db_path)
        total_records = 0
        last_activity = None
        status = 'offline'

        if aconn:
            for table in AGENT_TABLES.get(agent_name, []):
                total_records += _safe_scalar(aconn, f"SELECT COUNT(*) FROM {table}", default=0)
            last_activity = _safe_scalar(aconn, "SELECT value FROM agent_state WHERE key = 'last_scan_timestamp'", default=None)
            status = 'online' if total_records > 0 else 'idle'
            # Check freshness
            if last_activity:
                try:
                    from datetime import timedelta
                    last_dt = datetime.fromisoformat(str(last_activity))
                    if (datetime.utcnow() - last_dt) > timedelta(hours=24):
                        status = 'stale'
                except (ValueError, TypeError):
                    pass
            aconn.close()

        db_size = _get_db_size(db_path)
        agent_statuses.append({
            'name': agent_name,
            'status': status,
            'records': total_records,
            'size_str': _format_size(db_size),
            'last_activity': _format_ago(last_activity) if last_activity else 'never',
        })

    data['agent_statuses'] = agent_statuses

    # Overall health
    online_count = sum(1 for a in agent_statuses if a['status'] in ('online', 'idle', 'stale'))
    data['overall_health'] = 'healthy' if online_count == len(AGENT_DBS) else (
        'degraded' if online_count > 0 else 'offline'
    )
    data['agents_online'] = online_count
    data['agents_total'] = len(AGENT_DBS)

    # =====================================================
    # LAYERS SECTION
    # =====================================================
    layer_data = []
    agent_status_map = {a['name']: a['status'] for a in agent_statuses}
    for layer_num in sorted(LAYERS.keys()):
        layer_info = LAYERS[layer_num]
        agents = layer_info['agents']
        online = sum(1 for a in agents if agent_status_map.get(a) in ('online', 'idle', 'stale'))
        total = len(agents)
        health = (online / total * 100) if total > 0 else 0
        layer_status = 'online' if online == total else ('degraded' if online > 0 else 'offline')
        layer_data.append({
            'number': layer_num,
            'name': layer_info['name'],
            'status': layer_status,
            'health': health,
            'agents': ', '.join(agents),
        })
    data['layers'] = layer_data

    # =====================================================
    # POWER CHARTS SECTION
    # =====================================================
    chart_entries = []
    chart_date = None
    chart_highlights = {'biggest_mover': None, 'highest_new': None, 'longest_running': None}

    if hub:
        # Get the most recent chart_date
        chart_date_row = _safe_query(hub, """
            SELECT DISTINCT chart_date FROM chart_entries
            ORDER BY chart_date DESC LIMIT 1
        """, default=[])
        if chart_date_row:
            chart_date = chart_date_row[0]['chart_date']
            chart_entries = _safe_query(hub, """
                SELECT rank, previous_rank, video_id, title, artist,
                       power_score, views, likes, comments, subscriber_count,
                       movement, weeks_on_chart
                FROM chart_entries
                WHERE chart_date = ?
                ORDER BY rank
            """, (chart_date,), default=[])

            # Compute highlights
            biggest_mover_diff = 0
            for e in chart_entries:
                # Biggest Mover (UP with largest rank improvement)
                if e['movement'] == 'UP' and e['previous_rank'] is not None:
                    diff = e['previous_rank'] - e['rank']
                    if diff > biggest_mover_diff:
                        biggest_mover_diff = diff
                        chart_highlights['biggest_mover'] = {
                            'title': e['title'],
                            'artist': e['artist'],
                            'diff': diff,
                            'prev_rank': e['previous_rank'],
                            'rank': e['rank'],
                        }

                # Highest New Entry (NEW with lowest rank number)
                if e['movement'] == 'NEW':
                    if chart_highlights['highest_new'] is None or e['rank'] < chart_highlights['highest_new']['rank']:
                        chart_highlights['highest_new'] = {
                            'title': e['title'],
                            'artist': e['artist'],
                            'rank': e['rank'],
                            'power_score': e['power_score'],
                        }

                # Longest Running (most weeks on chart)
                cur_longest = chart_highlights['longest_running']
                if cur_longest is None or e['weeks_on_chart'] > cur_longest['weeks']:
                    chart_highlights['longest_running'] = {
                        'title': e['title'],
                        'artist': e['artist'],
                        'rank': e['rank'],
                        'weeks': e['weeks_on_chart'],
                    }

    data['chart_entries'] = chart_entries
    data['chart_date'] = chart_date
    data['chart_highlights'] = chart_highlights
    # Compute max power score for bar width scaling
    if chart_entries:
        data['chart_max_score'] = max(e['power_score'] for e in chart_entries) or 1
    else:
        data['chart_max_score'] = 1

    # Close connections
    for c in [yt, st, el, sp, cm, ic, hub]:
        if c:
            try:
                c.close()
            except Exception:
                pass

    data['now'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    return data


# =====================================================
# HTML TEMPLATE
# =====================================================
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>POWER FM Platform Hub</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            line-height: 1.6;
            padding-bottom: 80px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }

        /* Header */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 24px 32px;
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border-radius: 12px;
            margin-bottom: 24px;
            border: 1px solid #e94560;
            box-shadow: 0 4px 20px rgba(233, 69, 96, 0.15);
        }

        .header h1 {
            font-size: 28px;
            font-weight: 800;
            letter-spacing: 3px;
            color: #fff;
        }

        .header h1 span {
            color: #e94560;
        }

        .header-right {
            text-align: right;
        }

        .header-time {
            font-size: 14px;
            color: #8892b0;
        }

        .health-badge {
            display: inline-block;
            padding: 4px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-top: 6px;
        }

        .health-healthy { background: rgba(0, 255, 136, 0.15); color: #00ff88; border: 1px solid #00ff88; }
        .health-degraded { background: rgba(255, 183, 0, 0.15); color: #ffb700; border: 1px solid #ffb700; }
        .health-offline { background: rgba(233, 69, 96, 0.15); color: #e94560; border: 1px solid #e94560; }

        /* Cards Row */
        .cards-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .card {
            background: #16213e;
            border-radius: 12px;
            padding: 20px 24px;
            border: 1px solid #1a2744;
            transition: border-color 0.2s;
        }

        .card:hover {
            border-color: #e94560;
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
        }

        .card-title {
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #8892b0;
        }

        .card-status {
            font-size: 11px;
            padding: 2px 10px;
            border-radius: 10px;
            font-weight: 600;
        }

        .status-online { background: rgba(0, 255, 136, 0.15); color: #00ff88; }
        .status-offline { background: rgba(233, 69, 96, 0.15); color: #e94560; }

        .card-value {
            font-size: 32px;
            font-weight: 800;
            color: #fff;
            margin-bottom: 4px;
        }

        .card-label {
            font-size: 12px;
            color: #8892b0;
        }

        .card-metrics {
            display: flex;
            gap: 24px;
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid #1a2744;
        }

        .card-metric {
            flex: 1;
        }

        .card-metric-value {
            font-size: 18px;
            font-weight: 700;
            color: #ccd6f6;
        }

        .card-metric-label {
            font-size: 11px;
            color: #8892b0;
        }

        /* Sections */
        .section {
            background: #16213e;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid #1a2744;
        }

        .section-title {
            font-size: 18px;
            font-weight: 700;
            color: #e94560;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid #1a2744;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        /* Tables */
        table {
            width: 100%;
            border-collapse: collapse;
        }

        thead th {
            text-align: left;
            padding: 10px 12px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #8892b0;
            border-bottom: 2px solid #1a2744;
        }

        tbody td {
            padding: 10px 12px;
            font-size: 14px;
            border-bottom: 1px solid #0f1a30;
            color: #ccd6f6;
        }

        tbody tr:hover {
            background: rgba(233, 69, 96, 0.05);
        }

        .text-right { text-align: right; }
        .text-center { text-align: center; }

        .num {
            font-variant-numeric: tabular-nums;
            font-weight: 600;
        }

        /* Status dots */
        .status-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 6px;
        }

        .dot-online { background: #00ff88; box-shadow: 0 0 6px #00ff88; }
        .dot-idle { background: #ffb700; box-shadow: 0 0 6px #ffb700; }
        .dot-stale { background: #ff8800; box-shadow: 0 0 6px #ff8800; }
        .dot-offline { background: #e94560; box-shadow: 0 0 6px #e94560; }
        .dot-error { background: #e94560; box-shadow: 0 0 6px #e94560; }

        /* Layer health bar */
        .health-bar {
            width: 100%;
            height: 8px;
            background: #0f1a30;
            border-radius: 4px;
            overflow: hidden;
        }

        .health-bar-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s;
        }

        .fill-online { background: linear-gradient(90deg, #00ff88, #00cc6a); }
        .fill-degraded { background: linear-gradient(90deg, #ffb700, #ff8800); }
        .fill-offline { background: linear-gradient(90deg, #e94560, #c73050); }

        /* Revenue highlight */
        .revenue-highlights {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }

        .rev-card {
            background: #0f1a30;
            border-radius: 8px;
            padding: 16px 20px;
            text-align: center;
        }

        .rev-value {
            font-size: 28px;
            font-weight: 800;
            color: #00ff88;
        }

        .rev-label {
            font-size: 12px;
            color: #8892b0;
            margin-top: 4px;
        }

        /* Two column layout for sections */
        .two-col {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }

        @media (max-width: 900px) {
            .two-col {
                grid-template-columns: 1fr;
            }
            .cards-row {
                grid-template-columns: 1fr;
            }
            .header {
                flex-direction: column;
                text-align: center;
                gap: 12px;
            }
            .header-right {
                text-align: center;
            }
        }

        /* Scrollable table wrapper */
        .table-wrap {
            overflow-x: auto;
        }

        /* Generation type badges */
        .gen-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }

        .gen-station { background: rgba(233, 69, 96, 0.2); color: #e94560; }
        .gen-ad { background: rgba(255, 183, 0, 0.2); color: #ffb700; }
        .gen-audio { background: rgba(0, 255, 136, 0.2); color: #00ff88; }

        /* Payment status */
        .pay-succeeded { color: #00ff88; }
        .pay-pending { color: #ffb700; }
        .pay-failed { color: #e94560; }

        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: #8892b0;
            font-style: italic;
        }

        .footer {
            text-align: center;
            padding: 20px;
            color: #4a5568;
            font-size: 12px;
        }

        /* Power Charts styles */
        .chart-rank-gold {
            background: linear-gradient(135deg, rgba(255, 215, 0, 0.12) 0%, rgba(255, 183, 0, 0.06) 100%);
            border-left: 3px solid #ffd700;
        }

        .chart-rank-gold td {
            color: #fff;
        }

        .chart-rank-num {
            font-size: 18px;
            font-weight: 800;
            color: #e94560;
            min-width: 30px;
            text-align: center;
        }

        .chart-rank-gold .chart-rank-num {
            color: #ffd700;
            text-shadow: 0 0 8px rgba(255, 215, 0, 0.4);
        }

        .movement-up { color: #00ff88; font-weight: 700; }
        .movement-down { color: #e94560; font-weight: 700; }
        .movement-new { color: #ffb700; font-weight: 700; }
        .movement-stable { color: #8892b0; }

        .power-score-cell {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .power-score-value {
            font-weight: 800;
            color: #fff;
            min-width: 44px;
        }

        .power-bar-track {
            flex: 1;
            height: 6px;
            background: #0f1a30;
            border-radius: 3px;
            overflow: hidden;
            min-width: 60px;
        }

        .power-bar-fill {
            height: 100%;
            border-radius: 3px;
            background: linear-gradient(90deg, #e94560, #ff6b81);
        }

        .chart-rank-gold .power-bar-fill {
            background: linear-gradient(90deg, #ffd700, #ffec80);
        }

        .chart-highlights {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #1a2744;
        }

        .highlight-card {
            background: #0f1a30;
            border-radius: 8px;
            padding: 16px 20px;
        }

        .highlight-label {
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #e94560;
            margin-bottom: 6px;
        }

        .highlight-title {
            font-size: 15px;
            font-weight: 700;
            color: #fff;
        }

        .highlight-detail {
            font-size: 12px;
            color: #8892b0;
            margin-top: 4px;
        }
    </style>
</head>
<body>
    <div class="container">

        <!-- HEADER -->
        <div class="header">
            <div>
                <h1><span>POWER FM</span> PLATFORM</h1>
            </div>
            <div class="header-right">
                <div class="header-time">{{ d.now }}</div>
                <div class="health-badge health-{{ d.overall_health }}">
                    &#9679; {{ d.overall_health | upper }} &mdash; {{ d.agents_online }}/{{ d.agents_total }} agents
                </div>
            </div>
        </div>

        <!-- SUMMARY CARDS -->
        <div class="cards-row">
            <!-- YouTube Card -->
            <a href="/youtube" class="card" style="text-decoration:none;color:inherit;cursor:pointer;transition:transform .2s;display:block;" onmouseover="this.style.transform='translateY(-3px)';this.style.borderColor='#e94560'" onmouseout="this.style.transform='';this.style.borderColor=''">
                <div class="card-header">
                    <div class="card-title">YouTube</div>
                    <div class="card-status {{ 'status-online' if d.yt_online else 'status-offline' }}">
                        {{ 'ONLINE' if d.yt_online else 'OFFLINE' }}
                    </div>
                </div>
                <div class="card-value">{{ fn(d.yt_total_views) }}</div>
                <div class="card-label">Total Views</div>
                <div class="card-metrics">
                    <div class="card-metric">
                        <div class="card-metric-value">{{ fn(d.yt_channels) }}</div>
                        <div class="card-metric-label">Channels</div>
                    </div>
                    <div class="card-metric">
                        <div class="card-metric-value">{{ fn(d.yt_videos) }}</div>
                        <div class="card-metric-label">Videos</div>
                    </div>
                </div>
            </a>

            <!-- Stripe Card -->
            <a href="/#revenue" class="card" style="text-decoration:none;color:inherit;cursor:pointer;transition:transform .2s;display:block;" onmouseover="this.style.transform='translateY(-3px)';this.style.borderColor='#e94560'" onmouseout="this.style.transform='';this.style.borderColor=''">
                <div class="card-header">
                    <div class="card-title">Stripe Revenue</div>
                    <div class="card-status {{ 'status-online' if d.st_online else 'status-offline' }}">
                        {{ 'ONLINE' if d.st_online else 'OFFLINE' }}
                    </div>
                </div>
                <div class="card-value">{{ fd(d.st_mrr_cents) }}</div>
                <div class="card-label">Monthly Recurring Revenue</div>
                <div class="card-metrics">
                    <div class="card-metric">
                        <div class="card-metric-value">{{ fn(d.st_active_subs) }}</div>
                        <div class="card-metric-label">Active Subs</div>
                    </div>
                    <div class="card-metric">
                        <div class="card-metric-value">{{ fn(d.st_customers) }}</div>
                        <div class="card-metric-label">Customers</div>
                    </div>
                </div>
            </a>

            <!-- ElevenLabs Card -->
            <a href="/admin/library" class="card" style="text-decoration:none;color:inherit;cursor:pointer;transition:transform .2s;display:block;" onmouseover="this.style.transform='translateY(-3px)';this.style.borderColor='#e94560'" onmouseout="this.style.transform='';this.style.borderColor=''">
                <div class="card-header">
                    <div class="card-title">ElevenLabs</div>
                    <div class="card-status {{ 'status-online' if d.el_online else 'status-offline' }}">
                        {{ 'ONLINE' if d.el_online else 'OFFLINE' }}
                    </div>
                </div>
                <div class="card-value">{{ fn(d.el_voices) }}</div>
                <div class="card-label">Voices Available</div>
                <div class="card-metrics">
                    <div class="card-metric">
                        <div class="card-metric-value">{{ fn(d.el_generations) }}</div>
                        <div class="card-metric-label">Audio Generated</div>
                    </div>
                    <div class="card-metric">
                        <div class="card-metric-value">{{ fn(d.el_station_ids) }}</div>
                        <div class="card-metric-label">Station IDs</div>
                    </div>
                </div>
            </a>

            <!-- Spotify Card -->
            <a href="/artists" class="card" style="text-decoration:none;color:inherit;cursor:pointer;transition:transform .2s;display:block;" onmouseover="this.style.transform='translateY(-3px)';this.style.borderColor='#e94560'" onmouseout="this.style.transform='';this.style.borderColor=''">
                <div class="card-header">
                    <div class="card-title">Spotify</div>
                    <div class="card-status {{ 'status-online' if d.sp_online else 'status-offline' }}">
                        {{ 'ONLINE' if d.sp_online else 'OFFLINE' }}
                    </div>
                </div>
                <div class="card-value">{{ fn(d.sp_artists) }}</div>
                <div class="card-label">Artists Tracked</div>
                <div class="card-metrics">
                    <div class="card-metric">
                        <div class="card-metric-value">{{ fn(d.sp_tracks) }}</div>
                        <div class="card-metric-label">Tracks</div>
                    </div>
                </div>
            </a>
        </div>

        <!-- POWER CHARTS SECTION -->
        <div class="section" id="charts">
            <div class="section-title">&#9733; Power Charts{% if d.chart_date %} &mdash; Week of {{ d.chart_date }}{% endif %}</div>
            {% if d.chart_entries %}
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th class="text-center" style="width: 40px;">#</th>
                            <th class="text-center" style="width: 60px;">Mv</th>
                            <th style="width: 25%;">Title</th>
                            <th>Artist</th>
                            <th style="width: 180px;">Power Score</th>
                            <th class="text-right">Views</th>
                            <th class="text-right">Likes</th>
                            <th class="text-right">Comments</th>
                            <th class="text-center">Wks</th>
                            <th class="text-center" style="width: 60px;">Play</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for e in d.chart_entries %}
                        <tr class="{{ 'chart-rank-gold' if e['rank'] == 1 else '' }}">
                            <td class="chart-rank-num">{{ e['rank'] }}</td>
                            <td class="text-center">
                                {% if e['movement'] == 'UP' %}
                                <span class="movement-up" title="Up from #{{ e['previous_rank'] }}">&#9650; {{ e['previous_rank'] - e['rank'] }}</span>
                                {% elif e['movement'] == 'DOWN' %}
                                <span class="movement-down" title="Down from #{{ e['previous_rank'] }}">&#9660; {{ e['rank'] - e['previous_rank'] }}</span>
                                {% elif e['movement'] == 'NEW' %}
                                <span class="movement-new">&#9679; NEW</span>
                                {% else %}
                                <span class="movement-stable">=</span>
                                {% endif %}
                            </td>
                            <td style="font-weight: 600;">{{ e['title'][:60] }}{{ '...' if e['title']|length > 60 else '' }}</td>
                            <td>{{ e['artist'] }}</td>
                            <td>
                                <div class="power-score-cell">
                                    <span class="power-score-value">{{ '%.1f' % e['power_score'] }}</span>
                                    <div class="power-bar-track">
                                        <div class="power-bar-fill" style="width: {{ (e['power_score'] / d.chart_max_score * 100) }}%;"></div>
                                    </div>
                                </div>
                            </td>
                            <td class="text-right num">{{ fn(e['views']) }}</td>
                            <td class="text-right num">{{ fn(e['likes']) }}</td>
                            <td class="text-right num">{{ fn(e['comments']) }}</td>
                            <td class="text-center num">{{ e['weeks_on_chart'] }}</td>
                            <td class="text-center">
                                <a href="javascript:void(0)" onclick="playVideo('{{ e['video_id'] }}', this)" style="display:inline-block;background:#e94560;color:#fff;width:32px;height:32px;line-height:32px;border-radius:50%;text-decoration:none;font-size:14px;text-align:center;" title="Play">&#9654;</a>
                            </td>
                        </tr>
                        <!-- Hidden player row -->
                        <tr id="player-{{ e['video_id'] }}" style="display:none;">
                            <td colspan="10" style="padding:0;background:#0a0a1a;">
                                <div style="max-width:640px;margin:10px auto;">
                                    <div style="position:relative;padding-top:56.25%;">
                                        <iframe id="iframe-{{ e['video_id'] }}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:8px;" allowfullscreen></iframe>
                                    </div>
                                    <div style="text-align:center;padding:8px;">
                                        <a href="javascript:void(0)" onclick="closeVideo('{{ e['video_id'] }}')" style="color:#e94560;text-decoration:none;font-size:13px;">Close Player</a>
                                    </div>
                                </div>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <!-- Chart Highlights -->
            <div class="chart-highlights">
                <div class="highlight-card">
                    <div class="highlight-label">Biggest Mover</div>
                    {% if d.chart_highlights.biggest_mover %}
                    <div class="highlight-title">{{ d.chart_highlights.biggest_mover.title[:50] }}</div>
                    <div class="highlight-detail">
                        {{ d.chart_highlights.biggest_mover.artist }}
                        &mdash; UP {{ d.chart_highlights.biggest_mover.diff }} positions
                        (#{{ d.chart_highlights.biggest_mover.prev_rank }} &rarr; #{{ d.chart_highlights.biggest_mover.rank }})
                    </div>
                    {% else %}
                    <div class="highlight-title" style="color: #8892b0;">No upward movers this week</div>
                    {% endif %}
                </div>
                <div class="highlight-card">
                    <div class="highlight-label">Highest New Entry</div>
                    {% if d.chart_highlights.highest_new %}
                    <div class="highlight-title">{{ d.chart_highlights.highest_new.title[:50] }}</div>
                    <div class="highlight-detail">
                        {{ d.chart_highlights.highest_new.artist }}
                        &mdash; enters at #{{ d.chart_highlights.highest_new.rank }}
                        (Score: {{ '%.1f' % d.chart_highlights.highest_new.power_score }})
                    </div>
                    {% else %}
                    <div class="highlight-title" style="color: #8892b0;">No new entries this week</div>
                    {% endif %}
                </div>
                <div class="highlight-card">
                    <div class="highlight-label">Longest Running</div>
                    {% if d.chart_highlights.longest_running %}
                    <div class="highlight-title">{{ d.chart_highlights.longest_running.title[:50] }}</div>
                    <div class="highlight-detail">
                        {{ d.chart_highlights.longest_running.artist }}
                        &mdash; {{ d.chart_highlights.longest_running.weeks }} week{{ 's' if d.chart_highlights.longest_running.weeks != 1 else '' }}
                        on chart at #{{ d.chart_highlights.longest_running.rank }}
                    </div>
                    {% else %}
                    <div class="highlight-title" style="color: #8892b0;">N/A</div>
                    {% endif %}
                </div>
            </div>
            {% else %}
            <div class="empty-state">No Power Charts data available yet &mdash; run chart generation first</div>
            {% endif %}
        </div>

        <!-- YOUTUBE SECTION -->
        <div class="section">
            <div class="section-title">&#9654; YouTube</div>
            {% if d.yt_channels_list %}
            <h3 style="color: #ccd6f6; margin-bottom: 12px; font-size: 14px; font-weight: 600;">Tracked Channels</h3>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Channel</th>
                            <th class="text-right">Subscribers</th>
                            <th class="text-right">Videos</th>
                            <th class="text-right">Total Views</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for ch in d.yt_channels_list %}
                        <tr>
                            <td>{{ ch['title'] or 'Untitled' }}</td>
                            <td class="text-right num">{{ fn(ch['subscriber_count']) }}</td>
                            <td class="text-right num">{{ fn(ch['video_count']) }}</td>
                            <td class="text-right num">{{ fn(ch['view_count']) }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% else %}
            <div class="empty-state">No YouTube channels tracked yet</div>
            {% endif %}

            {% if d.yt_top_videos %}
            <h3 style="color: #ccd6f6; margin: 20px 0 12px 0; font-size: 14px; font-weight: 600;">Top 10 Videos by Views</h3>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 40%;">Title</th>
                            <th>Channel</th>
                            <th class="text-right">Views</th>
                            <th class="text-right">Likes</th>
                            <th class="text-right">Comments</th>
                            <th>Published</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for v in d.yt_top_videos %}
                        <tr>
                            <td>{{ v['title'] or 'Untitled' }}</td>
                            <td>{{ v['channel_title'] or '-' }}</td>
                            <td class="text-right num">{{ fn(v['view_count']) }}</td>
                            <td class="text-right num">{{ fn(v['like_count']) }}</td>
                            <td class="text-right num">{{ fn(v['comment_count']) }}</td>
                            <td>{{ (v['published_at'] or '')[:10] }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endif %}
        </div>

        <!-- REVENUE SECTION -->
        <div class="section">
            <div class="section-title">&#36; Revenue</div>

            <div class="revenue-highlights">
                <div class="rev-card">
                    <div class="rev-value">{{ fd(d.st_mrr_cents) }}</div>
                    <div class="rev-label">Monthly Recurring Revenue (MRR)</div>
                </div>
                <div class="rev-card">
                    <div class="rev-value">{{ fd(d.st_arr_cents) }}</div>
                    <div class="rev-label">Annualized Recurring Revenue (ARR)</div>
                </div>
                <div class="rev-card">
                    <div class="rev-value">{{ fn(d.st_active_subs) }}</div>
                    <div class="rev-label">Active Subscriptions</div>
                </div>
                <div class="rev-card">
                    <div class="rev-value">{{ fn(d.st_customers) }}</div>
                    <div class="rev-label">Total Customers</div>
                </div>
            </div>

            {% if d.sub_breakdown %}
            <h3 style="color: #ccd6f6; margin-bottom: 12px; font-size: 14px; font-weight: 600;">Subscription Breakdown by Tier</h3>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Tier</th>
                            <th class="text-right">Active Subscriptions</th>
                            <th class="text-right">MRR</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for tier in d.sub_breakdown %}
                        <tr>
                            <td>{{ tier['tier_name'] }}</td>
                            <td class="text-right num">{{ fn(tier['sub_count']) }}</td>
                            <td class="text-right num">{{ fd(tier['tier_mrr_cents'] or 0) }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endif %}

            {% if d.recent_payments %}
            <h3 style="color: #ccd6f6; margin: 20px 0 12px 0; font-size: 14px; font-weight: 600;">Recent Payments</h3>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Customer</th>
                            <th>Description</th>
                            <th class="text-right">Amount</th>
                            <th class="text-center">Status</th>
                            <th>Method</th>
                            <th>Date</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for pay in d.recent_payments %}
                        <tr>
                            <td>{{ pay['customer_name'] or pay['customer_email'] or '-' }}</td>
                            <td>{{ pay['description'] or '-' }}</td>
                            <td class="text-right num">{{ fd(pay['amount_cents']) }}</td>
                            <td class="text-center">
                                <span class="pay-{{ pay['status'] or 'pending' }}">
                                    {{ (pay['status'] or 'pending') | upper }}
                                </span>
                            </td>
                            <td>{{ pay['payment_method'] or '-' }}</td>
                            <td>{{ (pay['created_at'] or '')[:16] }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% else %}
            <div class="empty-state">No payment data available</div>
            {% endif %}
        </div>

        <!-- ELEVENLABS SECTION -->
        <div class="section">
            <div class="section-title">&#127908; ElevenLabs AI Audio</div>

            {% if d.el_voices_list %}
            <h3 style="color: #ccd6f6; margin-bottom: 12px; font-size: 14px; font-weight: 600;">Available Voices ({{ d.el_voices }})</h3>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Voice Name</th>
                            <th>Category</th>
                            <th>Language</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for voice in d.el_voices_list %}
                        <tr>
                            <td>{{ voice['name'] or '-' }}</td>
                            <td>{{ voice['category'] or '-' }}</td>
                            <td>{{ voice['language'] or '-' }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endif %}

            {% if d.el_generations_list %}
            <h3 style="color: #ccd6f6; margin: 20px 0 12px 0; font-size: 14px; font-weight: 600;">Generated Audio Files</h3>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Type</th>
                            <th style="width: 35%;">Text</th>
                            <th>Voice</th>
                            <th class="text-right">Duration</th>
                            <th>Status</th>
                            <th>Created</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for gen in d.el_generations_list %}
                        <tr>
                            <td>
                                {% if gen['gen_type'] == 'Station ID' %}
                                <span class="gen-badge gen-station">Station ID</span>
                                {% elif gen['gen_type'] == 'Ad Read' %}
                                <span class="gen-badge gen-ad">Ad Read</span>
                                {% else %}
                                <span class="gen-badge gen-audio">Audio</span>
                                {% endif %}
                            </td>
                            <td>{{ (gen['text'] or '-')[:80] }}{{ '...' if gen['text'] and gen['text']|length > 80 else '' }}</td>
                            <td>{{ gen['voice_name'] or '-' }}</td>
                            <td class="text-right num">{{ '%.1f' % gen['duration_seconds'] if gen['duration_seconds'] else '-' }}s</td>
                            <td>{{ gen['status'] or '-' }}</td>
                            <td>{{ (gen['created_at'] or '')[:16] }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% else %}
            <div class="empty-state">No audio generations yet</div>
            {% endif %}
        </div>

        <!-- AGENT STATUS SECTION -->
        <div class="section">
            <div class="section-title">&#9881; Agent Status</div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Agent</th>
                            <th>Status</th>
                            <th class="text-right">Records</th>
                            <th class="text-right">DB Size</th>
                            <th>Last Activity</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for agent in d.agent_statuses %}
                        <tr>
                            <td style="font-weight: 600;">{{ agent.name }}</td>
                            <td>
                                <span class="status-dot dot-{{ agent.status }}"></span>
                                {{ agent.status | upper }}
                            </td>
                            <td class="text-right num">{{ fn(agent.records) }}</td>
                            <td class="text-right">{{ agent.size_str }}</td>
                            <td>{{ agent.last_activity }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- LAYERS SECTION -->
        <div class="section">
            <div class="section-title">&#9776; Power FM Layers</div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 60px;">Layer</th>
                            <th>Name</th>
                            <th>Status</th>
                            <th style="width: 200px;">Health</th>
                            <th>Agents</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for layer in d.layers %}
                        <tr>
                            <td class="text-center num" style="font-size: 18px; font-weight: 800; color: #e94560;">{{ layer.number }}</td>
                            <td style="font-weight: 600;">{{ layer.name }}</td>
                            <td>
                                <span class="status-dot dot-{{ layer.status }}"></span>
                                {{ layer.status | upper }}
                            </td>
                            <td>
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <div class="health-bar" style="flex: 1;">
                                        <div class="health-bar-fill fill-{{ layer.status }}" style="width: {{ layer.health }}%;"></div>
                                    </div>
                                    <span class="num" style="font-size: 12px; min-width: 40px;">{{ '%.0f' % layer.health }}%</span>
                                </div>
                            </td>
                            <td style="color: #8892b0;">{{ layer.agents }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            Power FM Platform Hub &mdash; Auto-refreshes every 60 seconds &mdash; {{ d.now }}
        </div>

    </div>

<!-- Floating Audio Player -->
<div id="player-bar" style="position:fixed;bottom:0;left:0;right:0;background:#0f0f23;border-top:2px solid #e94560;padding:12px 24px;display:flex;align-items:center;gap:16px;z-index:1000;">
    <button id="play-btn" onclick="togglePlay()" style="background:#e94560;color:#fff;border:none;border-radius:50%;width:44px;height:44px;font-size:20px;cursor:pointer;flex-shrink:0;">&#9654;</button>
    <select id="station-select" onchange="switchStation()" style="background:#1a1a2e;color:#fff;border:1px solid #333;border-radius:6px;padding:8px 12px;font-size:14px;min-width:180px;">
        <option value="national">Power FM (National)</option>
        <option value="la">Power 106 LA</option>
        <option value="nyc">Power 105.1 NYC</option>
        <option value="chicago">Power 92 Chicago</option>
        <option value="miami">Power 96 Miami</option>
        <option value="atlanta">Power 107.5 Atlanta</option>
        <option value="houston">Power 104 Houston</option>
        <option value="london">Power FM London</option>
        <option value="lagos">Power FM Lagos</option>
    </select>
    <div style="flex:1;overflow:hidden;">
        <div id="now-playing" style="color:#ccc;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Select a station and press play</div>
        <div id="station-status" style="color:#666;font-size:11px;margin-top:2px;">Ready</div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
        <span style="color:#666;font-size:12px;">VOL</span>
        <input type="range" id="volume" min="0" max="100" value="80" oninput="setVolume(this.value)" style="width:80px;accent-color:#e94560;">
    </div>
</div>
<audio id="audio-player" preload="none"></audio>
<script>
var audio = document.getElementById('audio-player');
var playing = false;
var currentStation = 'national';
var npInterval = null;

audio.volume = 0.8;

function togglePlay() {
    var btn = document.getElementById('play-btn');
    if (playing) {
        audio.pause();
        audio.src = '';
        playing = false;
        btn.innerHTML = '&#9654;';
        document.getElementById('station-status').textContent = 'Stopped';
        if (npInterval) clearInterval(npInterval);
    } else {
        currentStation = document.getElementById('station-select').value;
        audio.src = '/stream/' + currentStation;
        audio.play();
        playing = true;
        btn.innerHTML = '&#9646;&#9646;';
        document.getElementById('station-status').textContent = 'Connecting...';
        fetchNowPlaying();
        npInterval = setInterval(fetchNowPlaying, 5000);
    }
}

function switchStation() {
    if (playing) {
        audio.pause();
        audio.src = '';
        currentStation = document.getElementById('station-select').value;
        audio.src = '/stream/' + currentStation;
        audio.play();
        document.getElementById('station-status').textContent = 'Switching...';
        fetchNowPlaying();
    }
}

function setVolume(val) {
    audio.volume = val / 100;
}

function fetchNowPlaying() {
    fetch('/stream/' + currentStation + '/now-playing')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.now_playing) {
                document.getElementById('now-playing').textContent = data.now_playing;
                document.getElementById('station-status').textContent = 'LIVE - ' + data.station;
            } else if (data.error) {
                document.getElementById('station-status').textContent = data.error;
            }
        })
        .catch(function() {
            document.getElementById('station-status').textContent = 'Stream offline';
        });
}

audio.addEventListener('playing', function() {
    document.getElementById('station-status').textContent = 'LIVE';
});
audio.addEventListener('error', function() {
    document.getElementById('station-status').textContent = 'Connection error';
    document.getElementById('play-btn').innerHTML = '&#9654;';
    playing = false;
});

function playVideo(videoId, btn) {
    var row = document.getElementById('player-' + videoId);
    var iframe = document.getElementById('iframe-' + videoId);
    if (row.style.display === 'none') {
        // Close any other open players
        document.querySelectorAll('[id^="player-"]').forEach(function(r) { r.style.display = 'none'; });
        document.querySelectorAll('[id^="iframe-"]').forEach(function(f) { f.src = ''; });
        // Open this one
        iframe.src = 'https://www.youtube.com/embed/' + videoId + '?autoplay=1';
        row.style.display = 'table-row';
    } else {
        closeVideo(videoId);
    }
}
function closeVideo(videoId) {
    var row = document.getElementById('player-' + videoId);
    var iframe = document.getElementById('iframe-' + videoId);
    iframe.src = '';
    row.style.display = 'none';
}
</script>

</body>
</html>"""


# =====================================================
# STATION LANDING PAGE TEMPLATE
# =====================================================
STATION_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ name }} - Power FM</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 40px 20px;
        }
        .station-card {
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border: 2px solid #e94560;
            border-radius: 20px;
            padding: 48px 56px;
            text-align: center;
            max-width: 520px;
            width: 100%;
            box-shadow: 0 8px 40px rgba(233, 69, 96, 0.2);
        }
        .station-name {
            font-size: 36px;
            font-weight: 800;
            color: #fff;
            letter-spacing: 2px;
            margin-bottom: 8px;
        }
        .station-market {
            font-size: 16px;
            color: #8892b0;
            text-transform: uppercase;
            letter-spacing: 3px;
            margin-bottom: 32px;
        }
        .play-btn {
            background: #e94560;
            color: #fff;
            border: none;
            border-radius: 50%;
            width: 88px;
            height: 88px;
            font-size: 36px;
            cursor: pointer;
            margin: 0 auto 24px auto;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.15s, box-shadow 0.15s;
            box-shadow: 0 4px 20px rgba(233, 69, 96, 0.4);
        }
        .play-btn:hover {
            transform: scale(1.08);
            box-shadow: 0 6px 28px rgba(233, 69, 96, 0.6);
        }
        .now-playing {
            font-size: 18px;
            color: #ccd6f6;
            margin-bottom: 8px;
            min-height: 28px;
        }
        .status-text {
            font-size: 13px;
            color: #666;
            margin-bottom: 24px;
        }
        .volume-row {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-bottom: 32px;
        }
        .volume-row span {
            color: #666;
            font-size: 12px;
        }
        .volume-row input[type=range] {
            width: 120px;
            accent-color: #e94560;
        }
        .other-stations {
            border-top: 1px solid #1a2744;
            padding-top: 24px;
            margin-top: 8px;
        }
        .other-stations-label {
            font-size: 11px;
            color: #8892b0;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-bottom: 12px;
        }
        .station-links {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
        }
        .station-links a {
            color: #8892b0;
            text-decoration: none;
            font-size: 13px;
            padding: 4px 12px;
            border: 1px solid #1a2744;
            border-radius: 16px;
            transition: border-color 0.2s, color 0.2s;
        }
        .station-links a:hover {
            border-color: #e94560;
            color: #e94560;
        }
        .station-links a.active {
            border-color: #e94560;
            color: #e94560;
            background: rgba(233, 69, 96, 0.1);
        }
        .back-link {
            margin-top: 24px;
        }
        .back-link a {
            color: #8892b0;
            text-decoration: none;
            font-size: 13px;
        }
        .back-link a:hover {
            color: #e94560;
        }
    </style>
</head>
<body>
    <div class="station-card">
        <div class="station-name">{{ name }}</div>
        <div class="station-market">{{ key | upper }}</div>

        <button class="play-btn" id="play-btn" onclick="togglePlay()">&#9654;</button>

        <div class="now-playing" id="now-playing">Press play to listen</div>
        <div class="status-text" id="station-status">Ready</div>

        <div class="volume-row">
            <span>VOL</span>
            <input type="range" id="volume" min="0" max="100" value="80" oninput="setVolume(this.value)">
        </div>

        <div class="other-stations">
            <div class="other-stations-label">All Stations</div>
            <div class="station-links">
                {% for skey, sname in stations.items() %}
                <a href="/station/{{ skey }}" class="{{ 'active' if skey == key else '' }}">{{ sname }}</a>
                {% endfor %}
            </div>
        </div>
    </div>

    <div class="back-link">
        <a href="/">&larr; Back to Dashboard</a>
    </div>

    <audio id="audio-player" preload="none"></audio>
    <script>
    var audio = document.getElementById('audio-player');
    var playing = false;
    var npInterval = null;
    var stationKey = '{{ key }}';

    audio.volume = 0.8;

    function togglePlay() {
        var btn = document.getElementById('play-btn');
        if (playing) {
            audio.pause();
            audio.src = '';
            playing = false;
            btn.innerHTML = '&#9654;';
            document.getElementById('station-status').textContent = 'Stopped';
            if (npInterval) clearInterval(npInterval);
        } else {
            audio.src = '/stream/' + stationKey;
            audio.play();
            playing = true;
            btn.innerHTML = '&#9646;&#9646;';
            document.getElementById('station-status').textContent = 'Connecting...';
            fetchNowPlaying();
            npInterval = setInterval(fetchNowPlaying, 5000);
        }
    }

    function setVolume(val) {
        audio.volume = val / 100;
    }

    function fetchNowPlaying() {
        fetch('/stream/' + stationKey + '/now-playing')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.now_playing) {
                    document.getElementById('now-playing').textContent = data.now_playing;
                    document.getElementById('station-status').textContent = 'LIVE';
                } else if (data.error) {
                    document.getElementById('station-status').textContent = data.error;
                }
            })
            .catch(function() {
                document.getElementById('station-status').textContent = 'Stream offline';
            });
    }

    audio.addEventListener('playing', function() {
        document.getElementById('station-status').textContent = 'LIVE';
    });
    audio.addEventListener('error', function() {
        document.getElementById('station-status').textContent = 'Connection error';
        document.getElementById('play-btn').innerHTML = '&#9654;';
        playing = false;
    });
    </script>
</body>
</html>"""


NETWORK_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Network Status - Power FM</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 0 0 60px 0;
        }

        /* Header */
        .net-header {
            text-align: center;
            padding: 48px 20px 32px 20px;
        }
        .net-header h1 {
            font-size: 36px;
            font-weight: 800;
            letter-spacing: 4px;
            color: #fff;
            margin-bottom: 8px;
        }
        .net-header h1 span { color: #e94560; }
        .net-header .subtitle {
            font-size: 14px;
            color: #8892b0;
            letter-spacing: 3px;
            text-transform: uppercase;
        }

        /* Summary bar */
        .summary-bar {
            max-width: 1100px;
            margin: 0 auto 32px auto;
            padding: 0 20px;
            display: flex;
            justify-content: center;
            gap: 32px;
            flex-wrap: wrap;
        }
        .summary-item {
            background: #16213e;
            border: 1px solid #1a2744;
            border-radius: 12px;
            padding: 16px 28px;
            text-align: center;
            min-width: 180px;
        }
        .summary-value {
            font-size: 28px;
            font-weight: 800;
            color: #fff;
        }
        .summary-value.all-online { color: #00ff88; }
        .summary-label {
            font-size: 11px;
            color: #8892b0;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-top: 4px;
        }

        /* Station grid */
        .station-grid {
            max-width: 1100px;
            margin: 0 auto;
            padding: 0 20px;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
        }
        @media (max-width: 900px) {
            .station-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        @media (max-width: 580px) {
            .station-grid {
                grid-template-columns: 1fr;
            }
        }

        .scard {
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border: 1px solid #1a2744;
            border-radius: 16px;
            padding: 28px 24px;
            text-align: center;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        .scard:hover {
            border-color: #e94560;
            box-shadow: 0 4px 20px rgba(233, 69, 96, 0.15);
        }
        .scard-name {
            font-size: 22px;
            font-weight: 800;
            color: #fff;
            letter-spacing: 1px;
            margin-bottom: 4px;
        }
        .scard-market {
            font-size: 12px;
            color: #8892b0;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-bottom: 16px;
        }
        .scard-status {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 14px;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }
        .dot-live { background: #00ff88; box-shadow: 0 0 8px rgba(0, 255, 136, 0.6); }
        .dot-offline { background: #e94560; box-shadow: 0 0 8px rgba(233, 69, 96, 0.6); }
        .text-live { color: #00ff88; }
        .text-offline { color: #e94560; }

        .scard-np {
            font-size: 14px;
            color: #ccd6f6;
            min-height: 20px;
            margin-bottom: 6px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .scard-listeners {
            font-size: 12px;
            color: #8892b0;
            margin-bottom: 18px;
        }
        .listen-btn {
            display: inline-block;
            padding: 8px 28px;
            background: #e94560;
            color: #fff;
            text-decoration: none;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 1px;
            transition: transform 0.15s, box-shadow 0.15s;
        }
        .listen-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 4px 16px rgba(233, 69, 96, 0.4);
        }

        /* Footer */
        .net-footer {
            text-align: center;
            padding: 40px 20px 20px 20px;
            font-size: 13px;
            color: #555;
        }
        .net-footer a {
            color: #8892b0;
            text-decoration: none;
        }
        .net-footer a:hover {
            color: #e94560;
        }
    </style>
</head>
<body>
    <div class="net-header">
        <h1><span>POWER FM</span> NETWORK STATUS</h1>
        <div class="subtitle">9 Markets. One Culture.</div>
    </div>

    <div class="summary-bar">
        <div class="summary-item">
            <div class="summary-value" id="sum-online">--/9</div>
            <div class="summary-label">Stations Online</div>
        </div>
        <div class="summary-item">
            <div class="summary-value" id="sum-listeners">--</div>
            <div class="summary-label">Total Listeners</div>
        </div>
        <div class="summary-item">
            <div class="summary-value" style="font-size:20px;" id="sum-block">{{ current_block }}</div>
            <div class="summary-label">Current Block</div>
        </div>
    </div>

    <div class="station-grid">
        {% for key, name in stations.items() %}
        <div class="scard" id="card-{{ key }}">
            <div class="scard-name">{{ name }}</div>
            <div class="scard-market">{{ key | upper }}</div>
            <div class="scard-status" id="status-{{ key }}">
                <span class="status-dot dot-offline"></span>
                <span class="text-offline">Checking...</span>
            </div>
            <div class="scard-np" id="np-{{ key }}">--</div>
            <div class="scard-listeners" id="listeners-{{ key }}">-- listeners</div>
            <a class="listen-btn" href="/station/{{ key }}">Listen</a>
        </div>
        {% endfor %}
    </div>

    <div class="net-footer">
        Powered by <a href="/">Power FM Platform Hub</a>
    </div>

    <script>
    var stationKeys = {{ stations.keys() | list | tojson }};

    function refreshAll() {
        var promises = stationKeys.map(function(key) {
            return fetch('/stream/' + key + '/now-playing')
                .then(function(r) { return r.json(); })
                .then(function(data) { return { key: key, data: data, online: true }; })
                .catch(function() { return { key: key, data: {}, online: false }; });
        });

        Promise.all(promises).then(function(results) {
            var onlineCount = 0;
            var totalListeners = 0;

            results.forEach(function(r) {
                var statusEl = document.getElementById('status-' + r.key);
                var npEl = document.getElementById('np-' + r.key);
                var listenersEl = document.getElementById('listeners-' + r.key);

                if (r.online && !r.data.error) {
                    onlineCount++;
                    var np = r.data.now_playing || r.data.title || 'Live';
                    var listeners = r.data.listeners || 0;
                    totalListeners += listeners;

                    statusEl.innerHTML = '<span class="status-dot dot-live"></span><span class="text-live">LIVE</span>';
                    npEl.textContent = np;
                    listenersEl.textContent = listeners + ' listener' + (listeners !== 1 ? 's' : '');
                } else {
                    statusEl.innerHTML = '<span class="status-dot dot-offline"></span><span class="text-offline">OFFLINE</span>';
                    npEl.textContent = '--';
                    listenersEl.textContent = '-- listeners';
                }
            });

            var sumOnlineEl = document.getElementById('sum-online');
            sumOnlineEl.textContent = onlineCount + '/9';
            if (onlineCount === 9) {
                sumOnlineEl.className = 'summary-value all-online';
            } else {
                sumOnlineEl.className = 'summary-value';
            }
            document.getElementById('sum-listeners').textContent = totalListeners.toLocaleString();
        });
    }

    refreshAll();
    setInterval(refreshAll, 15000);
    </script>
</body>
</html>"""


# =====================================================
# SONG REQUEST PAGE TEMPLATE
# =====================================================
REQUEST_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Power FM Request Line</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #16213e 0%, #1a1a2e 100%);
            border-bottom: 2px solid #e94560;
            padding: 20px;
            text-align: center;
        }
        .header h1 {
            color: #e94560;
            font-size: 28px;
            margin-bottom: 4px;
        }
        .header p {
            color: #888;
            font-size: 14px;
        }
        .container {
            max-width: 700px;
            margin: 0 auto;
            padding: 20px;
        }
        .form-section {
            background: #16213e;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid #2a2a4a;
        }
        .form-section h2 {
            color: #e94560;
            font-size: 18px;
            margin-bottom: 16px;
        }
        .form-group {
            margin-bottom: 14px;
        }
        .form-group label {
            display: block;
            color: #aaa;
            font-size: 13px;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .form-group input,
        .form-group select,
        .form-group textarea {
            width: 100%;
            padding: 10px 14px;
            background: #0f3460;
            border: 1px solid #2a2a4a;
            border-radius: 8px;
            color: #e0e0e0;
            font-size: 15px;
            font-family: inherit;
            transition: border-color 0.2s;
        }
        .form-group input:focus,
        .form-group select:focus,
        .form-group textarea:focus {
            outline: none;
            border-color: #e94560;
        }
        .form-group textarea {
            resize: vertical;
            min-height: 70px;
        }
        .form-group .hint {
            color: #666;
            font-size: 12px;
            margin-top: 3px;
        }
        .submit-btn {
            width: 100%;
            padding: 12px;
            background: #e94560;
            color: #fff;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
            margin-top: 8px;
        }
        .submit-btn:hover {
            background: #d63851;
        }
        .submit-btn:disabled {
            background: #555;
            cursor: not-allowed;
        }
        .confirmation {
            display: none;
            background: #1b4332;
            border: 1px solid #2d6a4f;
            border-radius: 8px;
            padding: 14px 18px;
            margin-bottom: 16px;
            color: #95d5b2;
            font-size: 15px;
            text-align: center;
        }
        .confirmation.show { display: block; }
        .error-msg {
            display: none;
            background: #4a1525;
            border: 1px solid #e94560;
            border-radius: 8px;
            padding: 14px 18px;
            margin-bottom: 16px;
            color: #f4a0a0;
            font-size: 15px;
            text-align: center;
        }
        .error-msg.show { display: block; }
        .recent-section {
            background: #16213e;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #2a2a4a;
        }
        .recent-section h2 {
            color: #e94560;
            font-size: 18px;
            margin-bottom: 16px;
        }
        .request-item {
            padding: 12px 0;
            border-bottom: 1px solid #2a2a4a;
        }
        .request-item:last-child { border-bottom: none; }
        .request-item .song {
            font-size: 15px;
            font-weight: 600;
            color: #e0e0e0;
        }
        .request-item .artist {
            color: #aaa;
            font-size: 13px;
        }
        .request-item .meta {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
            margin-top: 4px;
            font-size: 12px;
            color: #777;
        }
        .request-item .message-text {
            font-style: italic;
            color: #999;
            font-size: 13px;
            margin-top: 4px;
        }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .badge.pending { background: #5c4a1e; color: #f0c040; }
        .badge.queued { background: #1a3a5c; color: #60a5fa; }
        .badge.played { background: #1b4332; color: #6ee7a0; }
        .badge.rejected { background: #4a1525; color: #f87171; }
        .empty-state {
            text-align: center;
            color: #666;
            padding: 30px;
            font-size: 14px;
        }
        .back-link {
            display: inline-block;
            color: #e94560;
            text-decoration: none;
            font-size: 14px;
            margin-bottom: 16px;
        }
        .back-link:hover { text-decoration: underline; }
        @media (max-width: 600px) {
            .container { padding: 12px; }
            .form-section, .recent-section { padding: 16px; }
            .header h1 { font-size: 22px; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Power FM Request Line</h1>
        <p>Request a song on any Power FM station</p>
    </div>
    <div class="container">
        <a href="/" class="back-link">&larr; Back to Dashboard</a>

        <div id="confirmation" class="confirmation"></div>
        <div id="error-msg" class="error-msg"></div>

        <div class="form-section">
            <h2>Submit a Request</h2>
            <form id="request-form">
                <div class="form-group">
                    <label for="listener_name">Your Name</label>
                    <input type="text" id="listener_name" name="listener_name" placeholder="Anonymous" />
                    <div class="hint">Optional &mdash; leave blank to stay anonymous</div>
                </div>
                <div class="form-group">
                    <label for="station_key">Station</label>
                    <select id="station_key" name="station_key">
                        {% for key, name in stations.items() %}
                        <option value="{{ key }}">{{ name }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label for="song_title">Song Title *</label>
                    <input type="text" id="song_title" name="song_title" placeholder="Enter song title" required />
                </div>
                <div class="form-group">
                    <label for="artist">Artist</label>
                    <input type="text" id="artist" name="artist" placeholder="Artist name" />
                    <div class="hint">Optional</div>
                </div>
                <div class="form-group">
                    <label for="message">Message / Shoutout</label>
                    <textarea id="message" name="message" placeholder="Send a shoutout or dedication..."></textarea>
                    <div class="hint">Optional &mdash; your message may be read on air</div>
                </div>
                <button type="submit" class="submit-btn" id="submit-btn">Submit Request</button>
            </form>
        </div>

        <div class="recent-section">
            <h2>Recent Requests</h2>
            <div id="recent-list">
                <div class="empty-state">Loading requests...</div>
            </div>
        </div>
    </div>

    <script>
    const stationNames = {{ stations | tojson }};

    function formatTime(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr + 'Z');
            return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch(e) { return isoStr; }
    }

    function loadRecent() {
        fetch('/api/requests')
            .then(r => r.json())
            .then(data => {
                const list = document.getElementById('recent-list');
                const reqs = (data.requests || []).slice(0, 10);
                if (reqs.length === 0) {
                    list.innerHTML = '<div class="empty-state">No requests yet. Be the first!</div>';
                    return;
                }
                let html = '';
                reqs.forEach(function(req) {
                    const station = stationNames[req.station_key] || req.station_key;
                    const artistHtml = req.artist ? '<span class="artist"> &mdash; ' + escHtml(req.artist) + '</span>' : '';
                    const msgHtml = req.message ? '<div class="message-text">&ldquo;' + escHtml(req.message) + '&rdquo;</div>' : '';
                    html += '<div class="request-item">' +
                        '<div><span class="song">' + escHtml(req.song_title) + '</span>' + artistHtml + '</div>' +
                        '<div class="meta">' +
                            '<span class="badge ' + req.status + '">' + req.status + '</span>' +
                            '<span>' + station + '</span>' +
                            '<span>by ' + escHtml(req.listener_name || 'Anonymous') + '</span>' +
                            '<span>' + formatTime(req.submitted_at) + '</span>' +
                        '</div>' +
                        msgHtml +
                    '</div>';
                });
                list.innerHTML = html;
            })
            .catch(function() {
                document.getElementById('recent-list').innerHTML = '<div class="empty-state">Could not load requests.</div>';
            });
    }

    function escHtml(s) {
        if (!s) return '';
        return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    document.getElementById('request-form').addEventListener('submit', function(e) {
        e.preventDefault();
        var btn = document.getElementById('submit-btn');
        var conf = document.getElementById('confirmation');
        var errDiv = document.getElementById('error-msg');
        conf.className = 'confirmation';
        errDiv.className = 'error-msg';
        btn.disabled = true;
        btn.textContent = 'Submitting...';

        var payload = {
            listener_name: document.getElementById('listener_name').value || 'Anonymous',
            station_key: document.getElementById('station_key').value,
            song_title: document.getElementById('song_title').value,
            artist: document.getElementById('artist').value,
            message: document.getElementById('message').value,
        };

        fetch('/api/requests', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(result) {
            btn.disabled = false;
            btn.textContent = 'Submit Request';
            if (result.ok && result.data.success) {
                conf.textContent = 'Request submitted! Your song (#' + result.data.request_id + ') has been added to the queue.';
                conf.className = 'confirmation show';
                document.getElementById('request-form').reset();
                loadRecent();
            } else {
                errDiv.textContent = result.data.error || 'Something went wrong. Please try again.';
                errDiv.className = 'error-msg show';
            }
        })
        .catch(function() {
            btn.disabled = false;
            btn.textContent = 'Submit Request';
            errDiv.textContent = 'Network error. Please try again.';
            errDiv.className = 'error-msg show';
        });
    });

    // Load recent requests on page load and refresh every 30 seconds
    loadRecent();
    setInterval(loadRecent, 30000);
    </script>
</body>
</html>"""


@app.route('/')
def index():
    data = gather_dashboard_data()
    return render_template_string(
        TEMPLATE,
        d=data,
        fn=_format_number,
        fd=_format_dollars,
    )


def _cors_json(data):
    """Create a JSON response with CORS headers."""
    response = jsonify(data)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return {}
    return dict(row)


def _rows_to_list(rows):
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    if not rows:
        return []
    return [dict(r) for r in rows]


# =====================================================
# REST API ENDPOINTS
# =====================================================

@app.route('/api/charts')
def api_charts():
    """GET /api/charts — Current Power Charts data."""
    hub = _open_ro(HUB_DB)
    chart_date = None
    entries = []

    if hub:
        try:
            chart_date_row = _safe_query(hub, """
                SELECT DISTINCT chart_date FROM chart_entries
                ORDER BY chart_date DESC LIMIT 1
            """, default=[])
            if chart_date_row:
                chart_date = chart_date_row[0]['chart_date']
                raw_entries = _safe_query(hub, """
                    SELECT rank, previous_rank, video_id, title, artist,
                           power_score, views, likes, comments, subscriber_count,
                           movement, weeks_on_chart
                    FROM chart_entries
                    WHERE chart_date = ?
                    ORDER BY rank
                """, (chart_date,), default=[])
                entries = _rows_to_list(raw_entries)
        finally:
            hub.close()

    return _cors_json({
        'chart_date': chart_date,
        'entries': entries,
        'count': len(entries),
    })


@app.route('/api/charts/history/<video_id>')
def api_chart_history(video_id):
    """GET /api/charts/history/<video_id> — Chart history for a specific video."""
    hub = _open_ro(HUB_DB)
    history = []

    if hub:
        try:
            raw = _safe_query(hub, """
                SELECT chart_date, rank, previous_rank, title, artist,
                       power_score, views, likes, comments, subscriber_count,
                       movement, weeks_on_chart
                FROM chart_entries
                WHERE video_id = ?
                ORDER BY chart_date DESC
            """, (video_id,), default=[])
            history = _rows_to_list(raw)
        finally:
            hub.close()

    return _cors_json({
        'video_id': video_id,
        'history': history,
        'count': len(history),
    })


@app.route('/api/revenue')
def api_revenue():
    """GET /api/revenue — Stripe revenue data."""
    st = _open_ro(AGENT_DBS['stripe'])
    result = {
        'mrr_cents': 0,
        'arr_cents': 0,
        'mrr_dollars': '$0.00',
        'arr_dollars': '$0.00',
        'active_subscriptions': 0,
        'customer_count': 0,
        'subscription_breakdown': [],
    }

    if st:
        try:
            result['customer_count'] = _safe_scalar(st, "SELECT COUNT(*) FROM customers", default=0)
            result['active_subscriptions'] = _safe_scalar(st, "SELECT COUNT(*) FROM subscriptions WHERE status = 'active'", default=0)

            # Calculate MRR
            mrr_cents = 0
            subs_rows = _safe_query(st, """
                SELECT pr.unit_amount_cents, pr.recurring_interval, pr.recurring_interval_count
                FROM subscriptions s
                LEFT JOIN prices pr ON s.price_id = pr.stripe_id
                WHERE s.status = 'active'
            """)
            for s in subs_rows:
                amount = s['unit_amount_cents'] or 0
                interval = s['recurring_interval'] or 'month'
                ic_val = s['recurring_interval_count'] or 1
                if interval == 'year':
                    mrr_cents += amount / (12 * ic_val)
                else:
                    mrr_cents += amount / ic_val
            mrr_cents = int(mrr_cents)
            arr_cents = mrr_cents * 12
            result['mrr_cents'] = mrr_cents
            result['arr_cents'] = arr_cents
            result['mrr_dollars'] = _format_dollars(mrr_cents)
            result['arr_dollars'] = _format_dollars(arr_cents)

            # Subscription breakdown by tier
            raw_breakdown = _safe_query(st, """
                SELECT
                    COALESCE(p.name, 'Unknown') as tier_name,
                    COUNT(*) as sub_count,
                    SUM(CASE WHEN pr.recurring_interval = 'year'
                        THEN CAST(pr.unit_amount_cents AS REAL) / (12 * COALESCE(pr.recurring_interval_count, 1))
                        ELSE CAST(pr.unit_amount_cents AS REAL) / COALESCE(pr.recurring_interval_count, 1)
                    END) as tier_mrr_cents
                FROM subscriptions s
                LEFT JOIN prices pr ON s.price_id = pr.stripe_id
                LEFT JOIN products p ON s.product_id = p.stripe_id
                WHERE s.status = 'active'
                GROUP BY COALESCE(p.name, 'Unknown')
                ORDER BY tier_mrr_cents DESC
            """, default=[])
            result['subscription_breakdown'] = _rows_to_list(raw_breakdown)
        finally:
            st.close()

    return _cors_json(result)


@app.route('/api/agents')
def api_agents():
    """GET /api/agents — Agent status for all connected agents."""
    agent_statuses = []
    for agent_name, db_path in sorted(AGENT_DBS.items()):
        aconn = _open_ro(db_path)
        total_records = 0
        last_activity = None
        status = 'offline'

        if aconn:
            try:
                for table in AGENT_TABLES.get(agent_name, []):
                    total_records += _safe_scalar(aconn, f"SELECT COUNT(*) FROM {table}", default=0)
                last_activity = _safe_scalar(aconn, "SELECT value FROM agent_state WHERE key = 'last_scan_timestamp'", default=None)
                status = 'online' if total_records > 0 else 'idle'
                if last_activity:
                    try:
                        last_dt = datetime.fromisoformat(str(last_activity))
                        if (datetime.utcnow() - last_dt) > timedelta(hours=24):
                            status = 'stale'
                    except (ValueError, TypeError):
                        pass
            finally:
                aconn.close()

        db_size = _get_db_size(db_path)
        agent_statuses.append({
            'name': agent_name,
            'status': status,
            'records': total_records,
            'db_size': db_size,
            'db_size_formatted': _format_size(db_size),
            'last_activity': str(last_activity) if last_activity else None,
            'last_activity_relative': _format_ago(last_activity) if last_activity else 'never',
        })

    online_count = sum(1 for a in agent_statuses if a['status'] in ('online', 'idle', 'stale'))
    return _cors_json({
        'agents': agent_statuses,
        'agents_online': online_count,
        'agents_total': len(AGENT_DBS),
        'overall_health': 'healthy' if online_count == len(AGENT_DBS) else (
            'degraded' if online_count > 0 else 'offline'
        ),
    })


@app.route('/api/youtube')
def api_youtube():
    """GET /api/youtube — YouTube channels and top 10 videos."""
    yt = _open_ro(AGENT_DBS['youtube'])
    channels = []
    videos = []

    if yt:
        try:
            raw_channels = _safe_query(yt, """
                SELECT channel_id, title, custom_url, subscriber_count, video_count, view_count
                FROM channels ORDER BY subscriber_count DESC
            """, default=[])
            channels = _rows_to_list(raw_channels)

            raw_videos = _safe_query(yt, """
                SELECT v.video_id, v.title, v.view_count, v.like_count, v.comment_count,
                       v.published_at, v.duration, c.title as channel_title
                FROM videos v
                LEFT JOIN channels c ON v.channel_id = c.channel_id
                ORDER BY v.view_count DESC LIMIT 10
            """, default=[])
            videos = _rows_to_list(raw_videos)
        finally:
            yt.close()

    return _cors_json({
        'channels': channels,
        'videos': videos,
        'channel_count': len(channels),
        'video_count': len(videos),
    })


@app.route('/api/youtube/channels')
def api_youtube_channels():
    """GET /api/youtube/channels — YouTube channels only."""
    yt = _open_ro(AGENT_DBS['youtube'])
    channels = []

    if yt:
        try:
            raw = _safe_query(yt, """
                SELECT channel_id, title, custom_url, subscriber_count, video_count, view_count
                FROM channels ORDER BY subscriber_count DESC
            """, default=[])
            channels = _rows_to_list(raw)
        finally:
            yt.close()

    return _cors_json({
        'channels': channels,
        'count': len(channels),
    })


@app.route('/api/youtube/videos')
def api_youtube_videos():
    """GET /api/youtube/videos — Top videos with stats."""
    yt = _open_ro(AGENT_DBS['youtube'])
    videos = []

    if yt:
        try:
            raw = _safe_query(yt, """
                SELECT v.video_id, v.title, v.view_count, v.like_count, v.comment_count,
                       v.published_at, v.duration, c.title as channel_title
                FROM videos v
                LEFT JOIN channels c ON v.channel_id = c.channel_id
                ORDER BY v.view_count DESC LIMIT 10
            """, default=[])
            videos = _rows_to_list(raw)
        finally:
            yt.close()

    return _cors_json({
        'videos': videos,
        'count': len(videos),
    })


@app.route('/api/stream')
def api_stream():
    """GET /api/stream — Proxy stream server status from Icecast."""
    try:
        req = urllib.request.Request('http://localhost:8000/status.json')
        req.add_header('User-Agent', 'PowerFM-Dashboard/1.0')
        with urllib.request.urlopen(req, timeout=5) as resp:
            stream_data = json.loads(resp.read().decode('utf-8'))
        result = {
            'online': True,
            'data': stream_data,
        }
    except Exception:
        result = {
            'online': False,
            'data': {},
            'error': 'Stream server unavailable',
        }

    return _cors_json(result)


@app.route('/api/playlists')
def api_playlists():
    """GET /api/playlists — List available playlist files."""
    playlists_dir = os.path.join(AGENT_DIR, 'playlists')
    playlist_files = []

    if os.path.isdir(playlists_dir):
        for filepath in sorted(glob.glob(os.path.join(playlists_dir, '*'))):
            if os.path.isfile(filepath):
                filename = os.path.basename(filepath)
                stat = os.stat(filepath)
                playlist_files.append({
                    'filename': filename,
                    'path': filepath,
                    'size': stat.st_size,
                    'size_formatted': _format_size(stat.st_size),
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })

    return _cors_json({
        'playlists': playlist_files,
        'count': len(playlist_files),
    })


@app.route('/api/layers')
def api_layers():
    """GET /api/layers — Power FM layer status."""
    # Build agent status map first
    agent_status_map = {}
    for agent_name, db_path in AGENT_DBS.items():
        aconn = _open_ro(db_path)
        status = 'offline'
        if aconn:
            try:
                total = 0
                for table in AGENT_TABLES.get(agent_name, []):
                    total += _safe_scalar(aconn, f"SELECT COUNT(*) FROM {table}", default=0)
                last_activity = _safe_scalar(aconn, "SELECT value FROM agent_state WHERE key = 'last_scan_timestamp'", default=None)
                status = 'online' if total > 0 else 'idle'
                if last_activity:
                    try:
                        last_dt = datetime.fromisoformat(str(last_activity))
                        if (datetime.utcnow() - last_dt) > timedelta(hours=24):
                            status = 'stale'
                    except (ValueError, TypeError):
                        pass
            finally:
                aconn.close()
        agent_status_map[agent_name] = status

    layer_data = []
    for layer_num in sorted(LAYERS.keys()):
        layer_info = LAYERS[layer_num]
        agents = layer_info['agents']
        online = sum(1 for a in agents if agent_status_map.get(a) in ('online', 'idle', 'stale'))
        total = len(agents)
        health = (online / total * 100) if total > 0 else 0
        layer_status = 'online' if online == total else ('degraded' if online > 0 else 'offline')
        layer_data.append({
            'layer': layer_num,
            'name': layer_info['name'],
            'status': layer_status,
            'health_pct': health,
            'agents': agents,
            'agents_online': online,
            'agents_total': total,
        })

    return _cors_json({
        'layers': layer_data,
        'count': len(layer_data),
    })


# =====================================================
# STREAM PROXY + STATION ROUTES
# =====================================================

@app.route('/stream/<key>')
def stream_proxy(key):
    """Proxy audio stream from a station server."""
    port = STATION_PORTS.get(key)
    if not port:
        return 'Station not found', 404
    try:
        req = urllib.request.urlopen(f'http://localhost:{port}/stream', timeout=5)
        def generate():
            try:
                while True:
                    chunk = req.read(4096)
                    if not chunk:
                        break
                    yield chunk
            except Exception:
                pass
            finally:
                req.close()
        return Response(generate(), mimetype='audio/mpeg', headers={
            'ICY-Name': STATION_NAMES.get(key, 'Power FM'),
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        })
    except Exception:
        return 'Station offline', 503


@app.route('/stream/<key>/now-playing')
def stream_now_playing(key):
    """Get now-playing info for a station."""
    port = STATION_PORTS.get(key)
    if not port:
        return _cors_json({'error': 'Station not found'}), 404
    try:
        data = urllib.request.urlopen(f'http://localhost:{port}/status.json', timeout=3).read()
        response = jsonify(json.loads(data))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception:
        return _cors_json({'error': 'Station offline', 'station': key})


@app.route('/station/<key>')
def station_page(key):
    if key not in STATION_PORTS:
        return 'Station not found', 404
    return render_template_string(STATION_TEMPLATE,
        key=key,
        name=STATION_NAMES.get(key, 'Power FM'),
        port=STATION_PORTS[key],
        stations=STATION_NAMES,
    )


SHOWS_TEMPLATE = """<!DOCTYPE html>
<html><head>
<title>Power FM — Shows</title>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#1a1a2e; color:#eee; font-family:-apple-system,BlinkMacSystemFont,sans-serif; min-height:100vh; }
.container { max-width:1100px; margin:0 auto; padding:30px 20px; }
h1 { font-size:32px; margin-bottom:8px; } h1 span { color:#e94560; }
.subtitle { color:#888; font-size:14px; margin-bottom:30px; }
.current { background:linear-gradient(135deg,#e94560,#c0392b); border-radius:16px; padding:24px 30px; margin-bottom:30px; }
.current h2 { font-size:22px; margin-bottom:4px; } .current p { font-size:14px; opacity:0.85; }
.blocks { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:16px; margin-bottom:40px; }
.block { background:#16213e; border-radius:14px; padding:22px; cursor:pointer; transition:all .2s; text-decoration:none; color:#eee; display:block; border:2px solid transparent; }
.block:hover { border-color:#e94560; transform:translateY(-2px); }
.block h3 { font-size:20px; margin-bottom:6px; }
.block .time { color:#e94560; font-size:14px; font-weight:700; margin-bottom:8px; }
.block .vibe { color:#888; font-size:13px; margin-bottom:10px; }
.block .dj { display:flex; align-items:center; gap:10px; margin-top:10px; }
.block .dj-avatar { width:36px; height:36px; border-radius:50%; background:#e94560; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:16px; }
.block .dj-name { font-size:14px; } .block .dj-style { color:#888; font-size:12px; }
.listen-btn { display:inline-block; background:#e94560; color:#fff; padding:8px 20px; border-radius:8px; font-size:14px; font-weight:700; text-decoration:none; margin-top:12px; }
.listen-btn:hover { background:#c0392b; }
.on-air { display:inline-block; background:#27ae60; color:#fff; font-size:11px; padding:2px 8px; border-radius:10px; margin-left:8px; animation:pulse 2s infinite; }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
</style></head><body>
<div class="container">
<h1><span>Power FM</span> Shows</h1>
<p class="subtitle">Tap a show to listen live on Power FM</p>

<div class="current">
    <h2>Now: {{ current_block }}<span class="on-air">ON AIR</span></h2>
    <p>Currently streaming across all 10 stations</p>
    <a class="listen-btn" href="/station/national">Listen Live</a>
</div>

<div class="blocks">
    <a class="block" href="/station/national">
        <h3>Morning Power Hour</h3>
        <div class="time">6:00 AM — 10:00 AM</div>
        <div class="vibe">High energy, upbeat — wake up and get locked in</div>
        <div class="dj"><div class="dj-avatar">N</div><div><div class="dj-name">DJ Nova</div><div class="dj-style">Energetic</div></div></div>
        <span class="listen-btn">Listen</span>
    </a>
    <a class="block" href="/station/national">
        <h3>Midday Mix</h3>
        <div class="time">10:00 AM — 3:00 PM</div>
        <div class="vibe">Mainstream rotation — culture on rotation</div>
        <div class="dj"><div class="dj-avatar">M</div><div><div class="dj-name">MC Culture</div><div class="dj-style">Authoritative</div></div></div>
        <span class="listen-btn">Listen</span>
    </a>
    <a class="block" href="/station/national">
        <h3>Afternoon Drive</h3>
        <div class="time">3:00 PM — 7:00 PM</div>
        <div class="vibe">Peak energy — peak hours, peak hits</div>
        <div class="dj"><div class="dj-avatar">B</div><div><div class="dj-name">DJ Blaze</div><div class="dj-style">High Energy</div></div></div>
        <span class="listen-btn">Listen</span>
    </a>
    <a class="block" href="/station/national">
        <h3>Evening Vibes</h3>
        <div class="time">7:00 PM — 9:00 PM</div>
        <div class="vibe">Chillout focused — slow it down, feel the music</div>
        <div class="dj"><div class="dj-avatar">S</div><div><div class="dj-name">DJ Silk</div><div class="dj-style">Smooth</div></div></div>
        <span class="listen-btn">Listen</span>
    </a>
    <a class="block" href="/station/national">
        <h3>Late Night Sessions</h3>
        <div class="time">9:00 PM — 12:00 AM</div>
        <div class="vibe">Slow jams, deep cuts</div>
        <span class="listen-btn">Listen</span>
    </a>
    <a class="block" href="/station/national">
        <h3>The Overnight</h3>
        <div class="time">12:00 AM — 6:00 AM</div>
        <div class="vibe">Non-stop mix, low-key vibes</div>
        <span class="listen-btn">Listen</span>
    </a>
</div>
</div></body></html>"""


@app.route('/shows')
def shows_page():
    """Public shows page with schedule block buttons."""
    from scheduler import get_current_block
    current_block = get_current_block()
    block_label = current_block['label'] if current_block else 'Unknown'
    return render_template_string(SHOWS_TEMPLATE, current_block=block_label)


@app.route('/api/analytics')
def api_analytics():
    """GET /api/analytics — Listener analytics summary."""
    from analytics import get_analytics_report, init_analytics_db
    hub = _open_ro(os.path.join(AGENT_DIR, 'data', 'platform_hub.db'))
    if not hub:
        return _cors_json({'error': 'Database unavailable'})
    try:
        init_analytics_db(hub)
        report = get_analytics_report(hub)
        return _cors_json(report)
    finally:
        hub.close()


@app.route('/api/analytics/snapshot')
def api_analytics_snapshot():
    """GET /api/analytics/snapshot — Take a snapshot and return current listeners."""
    from analytics import collect_snapshot, init_analytics_db
    # Need read-write for snapshots
    import sqlite3
    db_path = os.path.join(AGENT_DIR, 'data', 'platform_hub.db')
    hub = sqlite3.connect(db_path)
    hub.row_factory = sqlite3.Row
    try:
        init_analytics_db(hub)
        result = collect_snapshot(hub)
        return _cors_json({'listeners': result, 'timestamp': datetime.now().isoformat()})
    finally:
        hub.close()


@app.route('/api/shows')
def api_shows():
    """GET /api/shows — Current show schedule with DJ info."""
    from shows import get_show_schedule, get_current_show
    schedule = get_show_schedule()
    current = get_current_show()
    return _cors_json({
        'current_show': current,
        'schedule': schedule,
    })


@app.route('/network')
def network_page():
    """Public network status page showing all 9 stations."""
    from scheduler import get_current_block
    current_block = get_current_block()
    block_label = current_block['label'] if current_block else 'Unknown'
    return render_template_string(NETWORK_TEMPLATE,
        stations=STATION_NAMES,
        ports=STATION_PORTS,
        current_block=block_label,
    )


# =====================================================
# SONG REQUEST ROUTES
# =====================================================

@app.route('/request')
def request_page():
    """Song request form page."""
    return render_template_string(REQUEST_TEMPLATE, stations=STATION_NAMES)


@app.route('/api/requests', methods=['GET', 'POST', 'OPTIONS'])
def api_requests():
    """GET: list requests. POST: submit a new request. OPTIONS: CORS preflight."""
    import sqlite3 as _sqlite3
    from requests_mod import init_requests_db, submit_request, get_recent_requests, get_request_stats

    # Handle CORS preflight
    if request.method == 'OPTIONS':
        resp = app.make_default_options_response()
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    db_path = os.path.join(AGENT_DIR, 'data', 'platform_hub.db')
    conn = _sqlite3.connect(db_path)
    conn.row_factory = _sqlite3.Row
    init_requests_db(conn)

    try:
        if request.method == 'POST':
            data = request.get_json() or {}
            if not data.get('song_title'):
                return _cors_json({'error': 'Song title is required'}), 400
            req_id = submit_request(
                conn,
                listener_name=data.get('listener_name', 'Anonymous'),
                station_key=data.get('station_key', 'national'),
                song_title=data['song_title'],
                artist=data.get('artist', ''),
                message=data.get('message', ''),
            )
            return _cors_json({'success': True, 'request_id': req_id, 'message': 'Request submitted!'})
        else:
            station = request.args.get('station')
            reqs = get_recent_requests(conn, limit=50)
            stats = get_request_stats(conn)
            return _cors_json({
                'requests': [dict(r) for r in reqs],
                'stats': stats,
            })
    finally:
        conn.close()


# =====================================================
# ARTIST PROFILE TEMPLATES
# =====================================================

ARTISTS_LIST_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Artists - Power FM</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            line-height: 1.6;
            padding-bottom: 40px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 24px 32px;
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border-radius: 12px;
            margin-bottom: 24px;
            border: 1px solid #e94560;
            box-shadow: 0 4px 20px rgba(233, 69, 96, 0.15);
        }
        .header h1 {
            font-size: 28px;
            font-weight: 800;
            letter-spacing: 3px;
            color: #fff;
        }
        .header h1 span { color: #e94560; }
        .header-right { text-align: right; }
        .back-link a {
            color: #8892b0;
            text-decoration: none;
            font-size: 14px;
            transition: color 0.2s;
        }
        .back-link a:hover { color: #e94560; }
        .artist-count {
            font-size: 14px;
            color: #8892b0;
            margin-bottom: 20px;
        }
        .artists-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
            gap: 20px;
        }
        .artist-card {
            background: #16213e;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #1a2744;
            transition: border-color 0.2s, transform 0.15s;
            text-decoration: none;
            color: inherit;
            display: block;
        }
        .artist-card:hover {
            border-color: #e94560;
            transform: translateY(-2px);
        }
        .artist-card-top {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 16px;
        }
        .artist-avatar {
            width: 56px;
            height: 56px;
            border-radius: 50%;
            object-fit: cover;
            border: 2px solid #e94560;
            flex-shrink: 0;
        }
        .artist-avatar-placeholder {
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: linear-gradient(135deg, #e94560, #0f3460);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
            font-weight: 800;
            color: #fff;
            flex-shrink: 0;
        }
        .artist-card-name {
            font-size: 20px;
            font-weight: 700;
            color: #fff;
        }
        .artist-card-sub {
            font-size: 12px;
            color: #8892b0;
            margin-top: 2px;
        }
        .artist-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }
        .artist-stat {
            background: #0f1a30;
            border-radius: 8px;
            padding: 10px 12px;
            text-align: center;
        }
        .artist-stat-value {
            font-size: 16px;
            font-weight: 700;
            color: #fff;
        }
        .artist-stat-label {
            font-size: 10px;
            color: #8892b0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 2px;
        }
        .artist-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-rank {
            background: rgba(233, 69, 96, 0.15);
            color: #e94560;
            border: 1px solid rgba(233, 69, 96, 0.3);
        }
        .badge-power {
            background: rgba(0, 255, 136, 0.1);
            color: #00ff88;
            border: 1px solid rgba(0, 255, 136, 0.3);
        }
        .badge-entries {
            background: rgba(255, 183, 0, 0.1);
            color: #ffb700;
            border: 1px solid rgba(255, 183, 0, 0.3);
        }
        .artist-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #8892b0;
        }
        .empty-state h2 { color: #ccd6f6; margin-bottom: 8px; }
        @media (max-width: 720px) {
            .artists-grid { grid-template-columns: 1fr; }
            .header { flex-direction: column; text-align: center; gap: 12px; }
            .header-right { text-align: center; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1><span>POWER FM</span> ARTISTS</h1>
            </div>
            <div class="header-right back-link">
                <a href="/">&larr; Back to Dashboard</a>
            </div>
        </div>

        {% if artists %}
        <div class="artist-count">{{ artists|length }} artist{{ 's' if artists|length != 1 else '' }} on Power FM</div>
        <div class="artists-grid">
            {% for a in artists %}
            <a class="artist-card" href="/artist/{{ a.slug }}">
                <div class="artist-card-top">
                    {% if a.thumbnail_url %}
                    <img class="artist-avatar" src="{{ a.thumbnail_url }}" alt="{{ a.name }}">
                    {% else %}
                    <div class="artist-avatar-placeholder">{{ a.name[0] | upper }}</div>
                    {% endif %}
                    <div>
                        <div class="artist-card-name">{{ a.name }}</div>
                        <div class="artist-card-sub">
                            {% if a.subscriber_count > 0 %}{{ '{:,}'.format(a.subscriber_count) }} subscribers{% endif %}
                            {% if a.video_count > 0 %} &middot; {{ a.video_count }} videos{% endif %}
                        </div>
                    </div>
                </div>
                <div class="artist-stats">
                    <div class="artist-stat">
                        <div class="artist-stat-value">{{ '{:,}'.format(a.total_views) }}</div>
                        <div class="artist-stat-label">Views</div>
                    </div>
                    <div class="artist-stat">
                        <div class="artist-stat-value">{{ '{:,}'.format(a.total_likes) }}</div>
                        <div class="artist-stat-label">Likes</div>
                    </div>
                    <div class="artist-stat">
                        <div class="artist-stat-value">{{ '%.1f' % a.power_score_avg }}</div>
                        <div class="artist-stat-label">Avg Score</div>
                    </div>
                </div>
                <div class="artist-badges">
                    {% if a.highest_rank > 0 %}
                    <span class="artist-badge badge-rank">#{{ a.highest_rank }} Peak</span>
                    {% endif %}
                    <span class="artist-badge badge-entries">{{ a.chart_entries }} chart entr{{ 'ies' if a.chart_entries != 1 else 'y' }}</span>
                    {% if a.weeks_on_chart > 0 %}
                    <span class="artist-badge badge-power">{{ a.weeks_on_chart }} wk{{ 's' if a.weeks_on_chart != 1 else '' }} on chart</span>
                    {% endif %}
                </div>
            </a>
            {% endfor %}
        </div>
        {% else %}
        <div class="empty-state">
            <h2>No Artists Yet</h2>
            <p>Artist profiles will appear here once chart data is generated.</p>
        </div>
        {% endif %}
    </div>
</body>
</html>"""


ARTIST_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ artist.name }} - Power FM</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            line-height: 1.6;
            padding-bottom: 40px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .back-bar {
            margin-bottom: 20px;
        }
        .back-bar a {
            color: #8892b0;
            text-decoration: none;
            font-size: 14px;
            transition: color 0.2s;
        }
        .back-bar a:hover { color: #e94560; }

        /* Artist Hero */
        .artist-hero {
            background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
            border-radius: 16px;
            padding: 40px;
            border: 1px solid #e94560;
            box-shadow: 0 4px 24px rgba(233, 69, 96, 0.15);
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            gap: 32px;
        }
        .hero-avatar {
            width: 100px;
            height: 100px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid #e94560;
            flex-shrink: 0;
        }
        .hero-avatar-placeholder {
            width: 100px;
            height: 100px;
            border-radius: 50%;
            background: linear-gradient(135deg, #e94560, #0f3460);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 40px;
            font-weight: 800;
            color: #fff;
            flex-shrink: 0;
        }
        .hero-info { flex: 1; }
        .hero-name {
            font-size: 36px;
            font-weight: 800;
            color: #fff;
            letter-spacing: 1px;
            margin-bottom: 4px;
        }
        .hero-sub {
            font-size: 14px;
            color: #8892b0;
        }
        .hero-desc {
            font-size: 13px;
            color: #8892b0;
            margin-top: 8px;
            max-width: 600px;
            line-height: 1.5;
        }

        /* Stats Cards */
        .stats-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: #16213e;
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            border: 1px solid #1a2744;
        }
        .stat-value {
            font-size: 28px;
            font-weight: 800;
            color: #fff;
        }
        .stat-value-accent { color: #e94560; }
        .stat-value-green { color: #00ff88; }
        .stat-label {
            font-size: 11px;
            color: #8892b0;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-top: 4px;
        }

        /* Sections */
        .section {
            background: #16213e;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid #1a2744;
        }
        .section-title {
            font-size: 18px;
            font-weight: 700;
            color: #e94560;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid #1a2744;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        /* Tables */
        .table-wrap { overflow-x: auto; }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        thead th {
            text-align: left;
            padding: 10px 12px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #8892b0;
            border-bottom: 2px solid #1a2744;
        }
        tbody td {
            padding: 10px 12px;
            font-size: 14px;
            border-bottom: 1px solid #0f1a30;
            color: #ccd6f6;
        }
        tbody tr:hover {
            background: rgba(233, 69, 96, 0.05);
        }
        .text-right { text-align: right; }
        .text-center { text-align: center; }
        .num {
            font-variant-numeric: tabular-nums;
            font-weight: 600;
        }

        /* Movement badges */
        .movement-up { color: #00ff88; font-weight: 700; }
        .movement-down { color: #e94560; font-weight: 700; }
        .movement-new { color: #ffb700; font-weight: 700; }
        .movement-stable { color: #8892b0; }

        /* Power score bar */
        .power-score-cell {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .power-score-value {
            font-weight: 800;
            color: #fff;
            min-width: 44px;
        }
        .power-bar-track {
            flex: 1;
            height: 6px;
            background: #0f1a30;
            border-radius: 3px;
            overflow: hidden;
            min-width: 60px;
        }
        .power-bar-fill {
            height: 100%;
            border-radius: 3px;
            background: linear-gradient(90deg, #e94560, #ff6b81);
        }

        /* Video list */
        .video-item {
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 12px 0;
            border-bottom: 1px solid #0f1a30;
        }
        .video-item:last-child { border-bottom: none; }
        .video-thumb {
            width: 120px;
            height: 68px;
            border-radius: 6px;
            object-fit: cover;
            background: #0f1a30;
            flex-shrink: 0;
        }
        .video-info { flex: 1; min-width: 0; }
        .video-title {
            font-size: 14px;
            font-weight: 600;
            color: #ccd6f6;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .video-meta {
            font-size: 12px;
            color: #8892b0;
            margin-top: 4px;
        }
        .video-stats {
            display: flex;
            gap: 16px;
            flex-shrink: 0;
        }
        .video-stat {
            text-align: right;
        }
        .video-stat-value {
            font-size: 14px;
            font-weight: 700;
            color: #ccd6f6;
        }
        .video-stat-label {
            font-size: 10px;
            color: #8892b0;
            text-transform: uppercase;
        }

        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: #8892b0;
            font-style: italic;
        }

        /* Rank highlight */
        .rank-1 { color: #ffd700; font-weight: 800; }
        .rank-2 { color: #c0c0c0; font-weight: 700; }
        .rank-3 { color: #cd7f32; font-weight: 700; }

        @media (max-width: 720px) {
            .artist-hero {
                flex-direction: column;
                text-align: center;
                padding: 24px;
                gap: 16px;
            }
            .hero-desc { margin: 8px auto 0 auto; }
            .stats-row { grid-template-columns: repeat(2, 1fr); }
            .video-item { flex-direction: column; align-items: flex-start; }
            .video-stats { width: 100%; justify-content: flex-start; gap: 20px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="back-bar">
            <a href="/artists">&larr; All Artists</a>
            &nbsp;&middot;&nbsp;
            <a href="/">Dashboard</a>
        </div>

        <!-- Artist Hero -->
        <div class="artist-hero">
            {% if artist.thumbnail_url %}
            <img class="hero-avatar" src="{{ artist.thumbnail_url }}" alt="{{ artist.name }}">
            {% else %}
            <div class="hero-avatar-placeholder">{{ artist.name[0] | upper }}</div>
            {% endif %}
            <div class="hero-info">
                <div class="hero-name">{{ artist.name }}</div>
                <div class="hero-sub">
                    {% if artist.subscriber_count > 0 %}{{ '{:,}'.format(artist.subscriber_count) }} YouTube subscribers{% endif %}
                    {% if artist.custom_url %} &middot; {{ artist.custom_url }}{% endif %}
                    {% if artist.video_count > 0 %} &middot; {{ artist.video_count }} videos{% endif %}
                </div>
                {% if artist.description %}
                <div class="hero-desc">{{ artist.description[:200] }}{{ '...' if artist.description|length > 200 else '' }}</div>
                {% endif %}
            </div>
        </div>

        <!-- Stats Cards -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-value">{{ '{:,}'.format(artist.total_views) }}</div>
                <div class="stat-label">Total Views</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ '{:,}'.format(artist.total_likes) }}</div>
                <div class="stat-label">Total Likes</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-value-accent">
                    {% if artist.highest_rank > 0 %}#{{ artist.highest_rank }}{% else %}--{% endif %}
                </div>
                <div class="stat-label">Highest Chart Position</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ artist.weeks_on_chart }}</div>
                <div class="stat-label">Weeks on Chart</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-value-green">{{ '%.1f' % artist.power_score_avg }}</div>
                <div class="stat-label">Avg Power Score</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ '%.1f' % artist.power_score_max }}</div>
                <div class="stat-label">Peak Power Score</div>
            </div>
        </div>

        <!-- Chart History -->
        <div class="section">
            <div class="section-title">Chart History</div>
            {% if artist.chart_entries %}
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th class="text-center">#</th>
                            <th style="width: 30%;">Title</th>
                            <th style="width: 160px;">Power Score</th>
                            <th class="text-center">Movement</th>
                            <th class="text-right">Views</th>
                            <th class="text-right">Likes</th>
                            <th class="text-right">Comments</th>
                            <th class="text-center">Wks</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for e in artist.chart_entries %}
                        <tr>
                            <td>{{ e.chart_date }}</td>
                            <td class="text-center num {{ 'rank-1' if e.rank == 1 else ('rank-2' if e.rank == 2 else ('rank-3' if e.rank == 3 else '')) }}">
                                {{ e.rank }}
                            </td>
                            <td style="font-weight: 600;">{{ e.title[:55] }}{{ '...' if e.title|length > 55 else '' }}</td>
                            <td>
                                <div class="power-score-cell">
                                    <span class="power-score-value">{{ '%.1f' % e.power_score }}</span>
                                    <div class="power-bar-track">
                                        <div class="power-bar-fill" style="width: {{ (e.power_score / artist.power_score_max * 100) if artist.power_score_max > 0 else 0 }}%;"></div>
                                    </div>
                                </div>
                            </td>
                            <td class="text-center">
                                {% if e.movement == 'UP' %}
                                <span class="movement-up">&#9650; UP</span>
                                {% elif e.movement == 'DOWN' %}
                                <span class="movement-down">&#9660; DOWN</span>
                                {% elif e.movement == 'NEW' %}
                                <span class="movement-new">&#9679; NEW</span>
                                {% else %}
                                <span class="movement-stable">=</span>
                                {% endif %}
                            </td>
                            <td class="text-right num">{{ '{:,}'.format(e.views or 0) }}</td>
                            <td class="text-right num">{{ '{:,}'.format(e.likes or 0) }}</td>
                            <td class="text-right num">{{ '{:,}'.format(e.comments or 0) }}</td>
                            <td class="text-center num">{{ e.weeks_on_chart or 1 }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% else %}
            <div class="empty-state">No chart entries found for this artist.</div>
            {% endif %}
        </div>

        <!-- Videos Section -->
        <div class="section">
            <div class="section-title">Videos ({{ artist.video_count }})</div>
            {% if artist.videos %}
            {% for v in artist.videos %}
            <div class="video-item">
                {% if v.thumbnail_url %}
                <img class="video-thumb" src="{{ v.thumbnail_url }}" alt="{{ v.title }}">
                {% else %}
                <div class="video-thumb"></div>
                {% endif %}
                <div class="video-info">
                    <div class="video-title">{{ v.title or 'Untitled' }}</div>
                    <div class="video-meta">
                        {% if v.published_at %}Published {{ v.published_at[:10] }}{% endif %}
                        {% if v.duration %} &middot; {{ v.duration }}{% endif %}
                    </div>
                </div>
                <div class="video-stats">
                    <div class="video-stat">
                        <div class="video-stat-value">{{ '{:,}'.format(v.view_count or 0) }}</div>
                        <div class="video-stat-label">Views</div>
                    </div>
                    <div class="video-stat">
                        <div class="video-stat-value">{{ '{:,}'.format(v.like_count or 0) }}</div>
                        <div class="video-stat-label">Likes</div>
                    </div>
                </div>
            </div>
            {% endfor %}
            {% else %}
            <div class="empty-state">No YouTube videos found for this artist.</div>
            {% endif %}
        </div>
    </div>
</body>
</html>"""


# =====================================================
# ARTIST ROUTES
# =====================================================

@app.route('/artists')
def artists_list():
    """List all artists on Power FM."""
    from artists import get_all_artists
    hub = _open_ro(os.path.join(AGENT_DIR, 'data', 'platform_hub.db'))
    yt = _open_ro(AGENT_DBS.get('youtube', ''))
    artists = get_all_artists(hub, yt) if hub else []
    if hub:
        hub.close()
    if yt:
        yt.close()
    return render_template_string(ARTISTS_LIST_TEMPLATE, artists=artists)


@app.route('/artist/<path:name>')
def artist_detail(name):
    """Artist profile page."""
    from artists import get_artist_detail
    hub = _open_ro(os.path.join(AGENT_DIR, 'data', 'platform_hub.db'))
    yt = _open_ro(AGENT_DBS.get('youtube', ''))
    artist = get_artist_detail(hub, yt, name) if hub else None
    if hub:
        hub.close()
    if yt:
        yt.close()
    if not artist:
        return 'Artist not found', 404
    return render_template_string(ARTIST_TEMPLATE, artist=artist)


@app.route('/api/artists')
def api_artists():
    """GET /api/artists -- All artists with stats."""
    from artists import get_all_artists
    hub = _open_ro(os.path.join(AGENT_DIR, 'data', 'platform_hub.db'))
    yt = _open_ro(AGENT_DBS.get('youtube', ''))
    artists = get_all_artists(hub, yt) if hub else []
    if hub:
        hub.close()
    if yt:
        yt.close()
    return _cors_json({'artists': artists, 'count': len(artists)})


# =====================================================
# YOUTUBE CHANNELS PAGES
# =====================================================

YOUTUBE_CHANNELS_TEMPLATE = """<!DOCTYPE html>
<html><head>
<title>Power FM — YouTube Channels</title>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#1a1a2e; color:#eee; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }
.header { background:linear-gradient(135deg,#16213e,#0f3460); padding:30px 40px; border-bottom:3px solid #e94560; }
.header h1 { font-size:28px; } .header h1 span { color:#e94560; }
.header p { color:#888; margin-top:5px; }
.container { max-width:1200px; margin:0 auto; padding:30px 40px; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:20px; margin-bottom:30px; }
.stat { background:#16213e; border-radius:12px; padding:20px; text-align:center; }
.stat .num { font-size:32px; font-weight:700; color:#e94560; } .stat .lbl { color:#888; font-size:13px; margin-top:5px; }
.channels { display:grid; grid-template-columns:repeat(auto-fill,minmax(350px,1fr)); gap:20px; }
.channel { background:#16213e; border-radius:12px; padding:25px; transition:transform .2s; cursor:pointer; text-decoration:none; color:#eee; display:block; }
.channel:hover { transform:translateY(-3px); border:1px solid #e94560; }
.channel h2 { font-size:20px; margin-bottom:8px; } .channel h2 span { color:#e94560; font-size:14px; }
.channel-stats { display:flex; gap:20px; margin:12px 0; }
.channel-stat { text-align:center; }
.channel-stat .v { font-size:20px; font-weight:700; } .channel-stat .l { color:#888; font-size:12px; }
.channel .desc { color:#888; font-size:13px; margin-top:10px; }
.badge { display:inline-block; background:#e94560; color:#fff; font-size:11px; padding:2px 8px; border-radius:10px; margin-left:8px; }
.back { display:inline-block; margin-top:20px; color:#e94560; text-decoration:none; }
</style></head><body>
<div class="header">
    <h1><span>POWER FM</span> YOUTUBE CHANNELS</h1>
    <p>{{ channels|length }} channels tracked — {{ total_views|int }} total views</p>
</div>
<div class="container">
<div class="stats">
    <div class="stat"><div class="num">{{ channels|length }}</div><div class="lbl">CHANNELS</div></div>
    <div class="stat"><div class="num">{{ total_videos }}</div><div class="lbl">VIDEOS</div></div>
    <div class="stat"><div class="num">{{ "{:,.0f}".format(total_views) }}</div><div class="lbl">TOTAL VIEWS</div></div>
    <div class="stat"><div class="num">{{ "{:,.0f}".format(total_subs) }}</div><div class="lbl">SUBSCRIBERS</div></div>
</div>
<div class="channels">
{% for ch in channels %}
<a class="channel" href="/youtube/{{ ch.channel_id }}">
    <h2>{{ ch.title }} {% if ch.subscriber_count > 50000 %}<span class="badge">VERIFIED</span>{% endif %}</h2>
    <div class="channel-stats">
        <div class="channel-stat"><div class="v">{{ "{:,.0f}".format(ch.view_count) }}</div><div class="l">Views</div></div>
        <div class="channel-stat"><div class="v">{{ "{:,.0f}".format(ch.subscriber_count) }}</div><div class="l">Subscribers</div></div>
        <div class="channel-stat"><div class="v">{{ ch.video_count }}</div><div class="l">Videos</div></div>
    </div>
    <div class="desc">{{ ch.extracted }} tracks extracted for Power FM rotation</div>
</a>
{% endfor %}
</div>
</div></body></html>"""

YOUTUBE_CHANNEL_DETAIL_TEMPLATE = """<!DOCTYPE html>
<html><head>
<title>{{ channel.title }} — Power FM</title>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#1a1a2e; color:#eee; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }
.header { background:linear-gradient(135deg,#16213e,#0f3460); padding:30px 40px; border-bottom:3px solid #e94560; }
.header h1 { font-size:28px; } .header h1 span { color:#e94560; }
.header p { color:#888; margin-top:5px; }
.container { max-width:1200px; margin:0 auto; padding:30px 40px; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:20px; margin-bottom:30px; }
.stat { background:#16213e; border-radius:12px; padding:20px; text-align:center; }
.stat .num { font-size:28px; font-weight:700; color:#e94560; } .stat .lbl { color:#888; font-size:13px; margin-top:5px; }
.videos { display:grid; grid-template-columns:repeat(auto-fill,minmax(380px,1fr)); gap:20px; }
.video-card { background:#16213e; border-radius:12px; overflow:hidden; }
.video-card .thumb { position:relative; width:100%; padding-top:56.25%; background:#0a0a1a; }
.video-card .thumb iframe { position:absolute; top:0; left:0; width:100%; height:100%; border:none; }
.video-card .info { padding:15px; }
.video-card .info h3 { font-size:14px; line-height:1.4; margin-bottom:8px; }
.video-card .meta { display:flex; gap:15px; color:#888; font-size:12px; }
.video-card .meta span { display:flex; align-items:center; gap:4px; }
.badge-extracted { display:inline-block; background:#27ae60; color:#fff; font-size:10px; padding:2px 6px; border-radius:8px; margin-left:6px; }
.badge-chart { display:inline-block; background:#e94560; color:#fff; font-size:10px; padding:2px 6px; border-radius:8px; margin-left:6px; }
.back { display:inline-block; margin-bottom:20px; color:#e94560; text-decoration:none; font-size:14px; }
.yt-link { display:inline-block; margin-top:10px; color:#e94560; text-decoration:none; font-size:13px; }
</style></head><body>
<div class="header">
    <h1><span>{{ channel.title }}</span></h1>
    <p>{{ "{:,.0f}".format(channel.subscriber_count) }} subscribers — {{ "{:,.0f}".format(channel.view_count) }} total views — {{ videos|length }} videos</p>
</div>
<div class="container">
<a class="back" href="/youtube">← All Channels</a>
<div class="stats">
    <div class="stat"><div class="num">{{ "{:,.0f}".format(channel.view_count) }}</div><div class="lbl">TOTAL VIEWS</div></div>
    <div class="stat"><div class="num">{{ "{:,.0f}".format(channel.subscriber_count) }}</div><div class="lbl">SUBSCRIBERS</div></div>
    <div class="stat"><div class="num">{{ videos|length }}</div><div class="lbl">VIDEOS</div></div>
    <div class="stat"><div class="num">{{ extracted_count }}</div><div class="lbl">EXTRACTED FOR FM</div></div>
</div>
<div class="videos">
{% for v in videos %}
<div class="video-card">
    <div class="thumb">
        <iframe src="https://www.youtube.com/embed/{{ v.video_id }}" allowfullscreen loading="lazy"></iframe>
    </div>
    <div class="info">
        <h3>{{ v.title }}{% if v.extracted %}<span class="badge-extracted">ON FM</span>{% endif %}{% if v.chart_pos %}<span class="badge-chart">#{{ v.chart_pos }}</span>{% endif %}</h3>
        <div class="meta">
            <span>{{ "{:,.0f}".format(v.view_count) }} views</span>
            <span>{{ "{:,.0f}".format(v.like_count) }} likes</span>
            <span>{{ v.comment_count }} comments</span>
        </div>
    </div>
</div>
{% endfor %}
</div>
</div></body></html>"""


@app.route('/youtube')
def youtube_channels_page():
    """Public YouTube channels page."""
    yt = _open_ro(AGENT_DBS['youtube'])
    channels = []
    total_views = 0
    total_subs = 0
    total_videos = 0

    if yt:
        try:
            raw = _safe_query(yt, """
                SELECT channel_id, title, subscriber_count, video_count, view_count
                FROM channels ORDER BY view_count DESC
            """, default=[])
            for r in raw:
                ch = dict(r)
                # Count extracted audio files for this channel
                extracted = _safe_query(yt, """
                    SELECT COUNT(*) as cnt FROM videos WHERE channel_id = ?
                """, (ch['channel_id'],), default=[])
                ext_ids = _safe_query(yt, """
                    SELECT video_id FROM videos WHERE channel_id = ?
                """, (ch['channel_id'],), default=[])
                ext_count = 0
                for e in ext_ids:
                    fpath = os.path.join(AGENTS_DIR, 'youtube-agent', 'extractions', e['video_id'] + '.mp3')
                    if os.path.isfile(fpath):
                        ext_count += 1
                ch['extracted'] = ext_count
                channels.append(ch)
                total_views += ch['view_count'] or 0
                total_subs += ch['subscriber_count'] or 0
                total_videos += ch['video_count'] or 0
        finally:
            yt.close()

    return render_template_string(YOUTUBE_CHANNELS_TEMPLATE,
        channels=channels, total_views=total_views,
        total_subs=total_subs, total_videos=total_videos)


@app.route('/youtube/<channel_id>')
def youtube_channel_detail(channel_id):
    """Per-channel page with embedded YouTube players."""
    yt = _open_ro(AGENT_DBS['youtube'])
    channel = None
    videos = []
    extracted_count = 0

    if yt:
        try:
            ch_raw = _safe_query(yt, """
                SELECT channel_id, title, subscriber_count, video_count, view_count
                FROM channels WHERE channel_id = ?
            """, (channel_id,), default=[])
            if ch_raw:
                channel = dict(ch_raw[0])

            vid_raw = _safe_query(yt, """
                SELECT video_id, title, view_count, like_count, comment_count, published_at
                FROM videos WHERE channel_id = ? ORDER BY view_count DESC
            """, (channel_id,), default=[])

            # Load chart positions
            hub = _open_ro(os.path.join(AGENT_DIR, 'data', 'platform_hub.db'))
            chart_positions = {}
            if hub:
                try:
                    chart_raw = _safe_query(hub, """
                        SELECT video_id, position FROM chart_entries
                        WHERE chart_date = (SELECT MAX(chart_date) FROM chart_entries)
                    """, default=[])
                    for cr in chart_raw:
                        chart_positions[cr['video_id']] = cr['position']
                except Exception:
                    pass
                finally:
                    hub.close()

            for r in vid_raw:
                v = dict(r)
                fpath = os.path.join(AGENTS_DIR, 'youtube-agent', 'extractions', v['video_id'] + '.mp3')
                v['extracted'] = os.path.isfile(fpath)
                v['chart_pos'] = chart_positions.get(v['video_id'])
                if v['extracted']:
                    extracted_count += 1
                videos.append(v)
        finally:
            yt.close()

    if not channel:
        return 'Channel not found', 404

    return render_template_string(YOUTUBE_CHANNEL_DETAIL_TEMPLATE,
        channel=channel, videos=videos, extracted_count=extracted_count)


# =====================================================
# MEMBERSHIP / SUBSCRIPTION PAGE
# =====================================================

MEMBERSHIP_TEMPLATE = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Power FM — Membership</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#eee;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}
.hero{text-align:center;padding:60px 20px 30px}
.hero h1{font-size:42px;font-weight:900;letter-spacing:2px;color:#e94560;margin-bottom:12px}
.hero p{font-size:18px;color:#aaa;max-width:600px;margin:0 auto}
.plans{display:flex;justify-content:center;gap:30px;padding:30px 20px 60px;flex-wrap:wrap}
.plan-card{background:rgba(22,33,62,0.6);backdrop-filter:blur(12px);border:1px solid rgba(233,69,96,0.15);border-radius:20px;padding:40px 32px;width:320px;text-align:center;transition:all .25s ease;position:relative}
.plan-card:hover{transform:translateY(-6px);border-color:rgba(233,69,96,0.5);box-shadow:0 20px 60px rgba(0,0,0,0.4)}
.plan-card.recommended{border-color:#e94560;box-shadow:0 0 30px rgba(233,69,96,0.2)}
.plan-card.recommended::before{content:"RECOMMENDED";position:absolute;top:-14px;left:50%;transform:translateX(-50%);background:#e94560;color:#fff;font-size:11px;font-weight:800;padding:4px 18px;border-radius:20px;letter-spacing:1.5px}
.plan-name{font-size:24px;font-weight:800;margin-bottom:8px;color:#fff}
.plan-price{font-size:48px;font-weight:900;color:#e94560;margin-bottom:4px}
.plan-price span{font-size:18px;color:#888;font-weight:400}
.plan-interval{font-size:14px;color:#666;margin-bottom:24px}
.features{list-style:none;margin-bottom:32px;text-align:left}
.features li{padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:14px;color:#ccc}
.features li::before{content:"✓";color:#e94560;font-weight:700;margin-right:10px}
.subscribe-btn{display:inline-block;width:100%;padding:14px 32px;background:#e94560;color:#fff;border:none;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer;transition:all .2s;text-transform:uppercase;letter-spacing:1px}
.subscribe-btn:hover{background:#d63651;transform:scale(1.03)}
.subscribe-btn:disabled{background:#444;cursor:not-allowed;transform:none}
.test-badge{display:inline-block;background:rgba(233,69,96,0.15);color:#e94560;font-size:11px;padding:4px 12px;border-radius:8px;margin-top:30px;letter-spacing:1px;font-weight:600}
.footer-note{text-align:center;padding:0 20px 40px;color:#555;font-size:13px}
</style>
</head><body>
<div class="hero">
    <h1>POWER FM MEMBERSHIP</h1>
    <p>Unlock premium radio, exclusive content, and artist access. Choose the plan that fits your vibe.</p>
    <div class="test-badge">TEST MODE — No real charges</div>
</div>
<div class="plans">
{% for plan in plans %}
    <div class="plan-card {{ 'recommended' if plan.recommended else '' }}">
        <div class="plan-name">{{ plan.name }}</div>
        <div class="plan-price">${{ plan.price }}<span>/mo</span></div>
        <div class="plan-interval">billed monthly</div>
        <ul class="features">
        {% for feature in plan.features %}
            <li>{{ feature }}</li>
        {% endfor %}
        </ul>
        <button class="subscribe-btn" onclick="subscribe('{{ plan.price_id }}')">Subscribe</button>
    </div>
{% endfor %}
</div>
<div class="footer-note">
    Powered by Stripe. Secure payment processing. Cancel anytime.
</div>
<script>
async function subscribe(priceId) {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Redirecting...';
    try {
        const resp = await fetch('/api/checkout', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({price_id: priceId})
        });
        const data = await resp.json();
        if (data.url) {
            window.location.href = data.url;
        } else {
            alert(data.error || 'Failed to start checkout');
            btn.disabled = false;
            btn.textContent = 'Subscribe';
        }
    } catch (err) {
        alert('Network error — please try again');
        btn.disabled = false;
        btn.textContent = 'Subscribe';
    }
}
</script>
</body></html>"""

# Features for each tier
PLAN_FEATURES = {
    'Power FM Basic': [
        'Live radio streaming',
        'Standard audio quality',
        'Weekly playlist updates',
        'Community chat access',
    ],
    'Power FM Pro': [
        'Everything in Basic',
        'HD audio streaming',
        'Early access to new shows',
        'Exclusive artist interviews',
        'Ad-free listening',
    ],
    'Power FM Premium': [
        'Everything in Pro',
        'Ultra HD / lossless audio',
        'Direct artist Q&A sessions',
        'Backstage content & stems',
        'Priority song requests',
        'VIP event invites',
    ],
}

RECOMMENDED_PLAN = 'Power FM Pro'


@app.route('/membership')
def membership():
    """Public membership page showing subscription plans."""
    stripe_db = _open_ro(AGENT_DBS.get('stripe', ''))
    plans = []

    if stripe_db:
        try:
            products = _safe_query(stripe_db, """
                SELECT stripe_id, name, description FROM products
                WHERE active = 1 ORDER BY name
            """, default=[])

            for prod in products:
                prod_dict = dict(prod)
                price_row = _safe_query(stripe_db, """
                    SELECT stripe_id, unit_amount_cents FROM prices
                    WHERE product_id = ? AND active = 1
                    ORDER BY unit_amount_cents ASC LIMIT 1
                """, (prod_dict['stripe_id'],), default=[])

                if price_row:
                    price_data = dict(price_row[0])
                    amount_cents = price_data.get('unit_amount_cents', 0)
                    plans.append({
                        'name': prod_dict['name'],
                        'description': prod_dict.get('description', ''),
                        'price': f"{amount_cents / 100:.2f}",
                        'price_id': price_data['stripe_id'],
                        'features': PLAN_FEATURES.get(prod_dict['name'], []),
                        'recommended': prod_dict['name'] == RECOMMENDED_PLAN,
                    })
        finally:
            stripe_db.close()

    # Sort by price ascending
    plans.sort(key=lambda p: float(p['price']))

    return render_template_string(MEMBERSHIP_TEMPLATE, plans=plans)


@app.route('/api/checkout', methods=['POST'])
def api_checkout():
    """Create a Stripe Checkout session and return the URL."""
    import sys
    stripe_agent_dir = os.path.join(AGENTS_DIR, 'stripe-agent')
    if stripe_agent_dir not in sys.path:
        sys.path.insert(0, stripe_agent_dir)
    from api_client import StripeClient

    body = request.get_json(force=True)
    price_id = body.get('price_id')
    if not price_id:
        return jsonify({'error': 'price_id is required'}), 400

    base_url = request.host_url.rstrip('/')
    success_url = f"{base_url}/membership?status=success"
    cancel_url = f"{base_url}/membership?status=cancelled"

    client = StripeClient()
    if not client.is_configured():
        return jsonify({'error': 'Stripe is not configured'}), 503

    session = client.create_checkout_session(price_id, success_url, cancel_url)
    if not session:
        return jsonify({'error': 'Failed to create checkout session'}), 502

    return jsonify({'url': session.get('url')})


def start_dashboard(host='0.0.0.0', port=5560, debug=False):
    """Start the Flask dashboard server."""
    print(f"\n  POWER FM Platform Dashboard")
    print(f"  http://localhost:{port}")
    print(f"  Auto-refresh: 60s\n")
    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    start_dashboard()
