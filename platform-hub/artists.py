#!/usr/bin/env python3
"""
Power FM Artist Profiles
Aggregates artist data from chart_entries (platform_hub.db) and YouTube
channels/videos (youtube.db) to build unified artist profiles.

Used by the dashboard for artist list and detail pages.
"""

import urllib.parse


def _safe_query(conn, sql, params=(), default=None):
    """Execute a query safely, returning default on any error."""
    if not conn:
        return default if default is not None else []
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


def _name_to_slug(name):
    """Convert an artist name to a URL-safe slug."""
    return urllib.parse.quote(name.strip(), safe='')


def _slug_to_name_pattern(slug):
    """Convert a URL slug back to a name pattern for LIKE matching."""
    return urllib.parse.unquote(slug).strip()


def get_artist_names(hub_conn):
    """Returns list of unique artist names from chart_entries."""
    rows = _safe_query(hub_conn, """
        SELECT DISTINCT artist FROM chart_entries
        WHERE artist IS NOT NULL AND artist != ''
        ORDER BY artist
    """, default=[])
    return [r['artist'].strip() if hasattr(r, 'keys') else r[0].strip() for r in rows]


def search_artists(hub_conn, query):
    """Search artists by name (case-insensitive LIKE query)."""
    pattern = f'%{query}%'
    rows = _safe_query(hub_conn, """
        SELECT DISTINCT artist FROM chart_entries
        WHERE artist LIKE ? COLLATE NOCASE
        ORDER BY artist
    """, (pattern,), default=[])
    return [r['artist'].strip() if hasattr(r, 'keys') else r[0].strip() for r in rows]


def _match_yt_channel(yt_conn, artist_name):
    """Try to find a YouTube channel matching an artist name (case-insensitive)."""
    if not yt_conn:
        return None
    name_lower = artist_name.strip().lower()
    channels = _safe_query(yt_conn, "SELECT * FROM channels", default=[])
    for ch in channels:
        title = (ch['title'] or '').strip().lower()
        if title == name_lower:
            return dict(ch)
    # Partial match fallback
    for ch in channels:
        title = (ch['title'] or '').strip().lower()
        if name_lower in title or title in name_lower:
            return dict(ch)
    return None


def _get_yt_videos_for_channel(yt_conn, channel_id):
    """Get all videos for a YouTube channel."""
    if not yt_conn or not channel_id:
        return []
    rows = _safe_query(yt_conn, """
        SELECT video_id, title, view_count, like_count, comment_count,
               published_at, duration, thumbnail_url
        FROM videos
        WHERE channel_id = ?
        ORDER BY view_count DESC
    """, (channel_id,), default=[])
    return [dict(r) for r in rows]


def get_all_artists(hub_conn, yt_conn):
    """
    Returns a list of artist dicts aggregated from chart_entries and YouTube channels.
    Each artist has: name, slug, total_views, total_likes, chart_entries (count),
    highest_rank, weeks_on_chart, videos (list), power_score_avg, subscriber_count,
    thumbnail_url
    """
    names = get_artist_names(hub_conn)
    artists = []

    for name in names:
        # Chart data
        chart_rows = _safe_query(hub_conn, """
            SELECT rank, power_score, views, likes, weeks_on_chart
            FROM chart_entries
            WHERE TRIM(artist) = TRIM(?) COLLATE NOCASE
        """, (name,), default=[])

        chart_count = len(chart_rows)
        total_views_chart = sum(r['views'] or 0 for r in chart_rows)
        total_likes_chart = sum(r['likes'] or 0 for r in chart_rows)
        highest_rank = min((r['rank'] for r in chart_rows), default=0) if chart_rows else 0
        max_weeks = max((r['weeks_on_chart'] or 0 for r in chart_rows), default=0) if chart_rows else 0
        avg_power = (sum(r['power_score'] or 0 for r in chart_rows) / chart_count) if chart_count > 0 else 0.0

        # YouTube data
        yt_channel = _match_yt_channel(yt_conn, name)
        videos = []
        subscriber_count = 0
        thumbnail_url = ''
        yt_total_views = 0

        if yt_channel:
            subscriber_count = yt_channel.get('subscriber_count', 0) or 0
            thumbnail_url = yt_channel.get('thumbnail_url', '') or ''
            yt_total_views = yt_channel.get('view_count', 0) or 0
            videos = _get_yt_videos_for_channel(yt_conn, yt_channel.get('channel_id'))

        # Combine: use YouTube total views if available (more comprehensive), else chart views
        total_views = yt_total_views if yt_total_views > 0 else total_views_chart
        total_likes = total_likes_chart  # Likes from chart entries (per-video snapshots)

        artists.append({
            'name': name.strip(),
            'slug': _name_to_slug(name),
            'total_views': total_views,
            'total_likes': total_likes,
            'chart_entries': chart_count,
            'highest_rank': highest_rank,
            'weeks_on_chart': max_weeks,
            'videos': videos[:5],  # Top 5 for list view
            'video_count': len(videos),
            'power_score_avg': round(avg_power, 2),
            'subscriber_count': subscriber_count,
            'thumbnail_url': thumbnail_url,
        })

    # Sort by highest power score average, then by total views
    artists.sort(key=lambda a: (-a['power_score_avg'], -a['total_views']))
    return artists


def get_artist_detail(hub_conn, yt_conn, artist_name_slug):
    """
    Returns detailed info for one artist:
    - name, slug, thumbnail_url, subscriber_count
    - all chart entries across all dates
    - all YouTube videos with stats
    - total reach (views across all sources)
    - chart_history: list of {chart_date, rank, power_score, movement, views, likes, ...}
    - aggregate stats: total_views, total_likes, highest_rank, weeks_on_chart, power_score_avg
    """
    # Decode the slug back to a name pattern
    name_pattern = _slug_to_name_pattern(artist_name_slug)

    # Find exact artist name from chart_entries (case-insensitive)
    rows = _safe_query(hub_conn, """
        SELECT DISTINCT artist FROM chart_entries
        WHERE TRIM(artist) = TRIM(?) COLLATE NOCASE
    """, (name_pattern,), default=[])

    if not rows:
        # Try partial match
        rows = _safe_query(hub_conn, """
            SELECT DISTINCT artist FROM chart_entries
            WHERE TRIM(artist) LIKE ? COLLATE NOCASE
            LIMIT 1
        """, (f'%{name_pattern}%',), default=[])

    if not rows:
        return None

    artist_name = rows[0]['artist'].strip() if hasattr(rows[0], 'keys') else rows[0][0].strip()

    # Get all chart entries for this artist across all dates
    chart_entries = _safe_query(hub_conn, """
        SELECT chart_date, rank, previous_rank, video_id, title,
               power_score, views, likes, comments, subscriber_count,
               movement, weeks_on_chart
        FROM chart_entries
        WHERE TRIM(artist) = TRIM(?) COLLATE NOCASE
        ORDER BY chart_date DESC, rank ASC
    """, (artist_name,), default=[])
    chart_entries = [dict(r) for r in chart_entries]

    # Aggregate chart stats
    chart_count = len(chart_entries)
    total_views_chart = sum(e.get('views', 0) or 0 for e in chart_entries)
    total_likes_chart = sum(e.get('likes', 0) or 0 for e in chart_entries)
    total_comments = sum(e.get('comments', 0) or 0 for e in chart_entries)
    highest_rank = min((e['rank'] for e in chart_entries), default=0) if chart_entries else 0
    max_weeks = max((e.get('weeks_on_chart', 0) or 0 for e in chart_entries), default=0) if chart_entries else 0
    avg_power = (sum(e.get('power_score', 0) or 0 for e in chart_entries) / chart_count) if chart_count > 0 else 0.0
    max_power = max((e.get('power_score', 0) or 0 for e in chart_entries), default=0) if chart_entries else 0

    # Build chart history: rank over time (group by date, take best rank)
    date_map = {}
    for e in chart_entries:
        d = e['chart_date']
        if d not in date_map or e['rank'] < date_map[d]['rank']:
            date_map[d] = {
                'chart_date': d,
                'rank': e['rank'],
                'power_score': e['power_score'],
                'movement': e['movement'],
                'views': e['views'],
                'likes': e['likes'],
            }
    chart_history = sorted(date_map.values(), key=lambda x: x['chart_date'], reverse=True)

    # Unique chart dates (how many weeks on chart)
    unique_dates = set(e['chart_date'] for e in chart_entries)

    # YouTube data
    yt_channel = _match_yt_channel(yt_conn, artist_name)
    videos = []
    subscriber_count = 0
    thumbnail_url = ''
    yt_total_views = 0
    custom_url = ''
    channel_description = ''

    if yt_channel:
        subscriber_count = yt_channel.get('subscriber_count', 0) or 0
        thumbnail_url = yt_channel.get('thumbnail_url', '') or ''
        yt_total_views = yt_channel.get('view_count', 0) or 0
        custom_url = yt_channel.get('custom_url', '') or ''
        channel_description = yt_channel.get('description', '') or ''
        videos = _get_yt_videos_for_channel(yt_conn, yt_channel.get('channel_id'))

    total_views = yt_total_views if yt_total_views > 0 else total_views_chart

    return {
        'name': artist_name,
        'slug': _name_to_slug(artist_name),
        'thumbnail_url': thumbnail_url,
        'subscriber_count': subscriber_count,
        'custom_url': custom_url,
        'description': channel_description,
        'total_views': total_views,
        'total_likes': total_likes_chart,
        'total_comments': total_comments,
        'chart_entries': chart_entries,
        'chart_entry_count': chart_count,
        'highest_rank': highest_rank,
        'weeks_on_chart': len(unique_dates),
        'max_weeks_single': max_weeks,
        'power_score_avg': round(avg_power, 2),
        'power_score_max': round(max_power, 2),
        'videos': videos,
        'video_count': len(videos),
        'chart_history': chart_history,
    }
