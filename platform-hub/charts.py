"""
Power Charts Engine for Power FM Platform Hub.

Reads YouTube video data (views, likes, comments, publish date, channel subscribers),
calculates a weighted Power Score, ranks tracks, tracks week-over-week movement,
and generates a Top 25 Power Charts report.

Power Score formula (weights sum to 1.0):
    Views:                          0.40
    Likes:                          0.20
    Comments:                       0.15
    Recency bonus (30-day decay):   0.15
    Subscriber-normalized views:    0.10
"""

import os
import math
import sqlite3
import logging
from datetime import datetime, timedelta

from database import (
    save_chart_entry, save_chart_history,
    get_previous_chart, get_chart_entries,
)

log = logging.getLogger('platform-hub.charts')

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YOUTUBE_DB = os.path.join(AGENTS_DIR, 'youtube-agent', 'data', 'youtube.db')
SPOTIFY_DB = os.path.join(AGENTS_DIR, 'spotify-agent', 'data', 'spotify.db')

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')

# Scoring weights
W_VIEWS = 0.40
W_LIKES = 0.20
W_COMMENTS = 0.15
W_RECENCY = 0.15
W_SUB_NORM = 0.10

# Recency decay window (days)
RECENCY_WINDOW_DAYS = 30

# Chart size
CHART_SIZE = 25


def _open_readonly(db_path):
    """Open a database read-only. Returns conn or None."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        log.debug(f"Cannot open {db_path}: {e}")
        return None


def _fetch_youtube_data():
    """
    Pull all videos and their channel subscriber counts from YouTube agent DB.
    Returns a list of dicts with keys:
        video_id, title, channel_title, published_at,
        view_count, like_count, comment_count, subscriber_count
    """
    conn = _open_readonly(YOUTUBE_DB)
    if not conn:
        log.warning("YouTube database not available — cannot generate charts")
        return []

    try:
        rows = conn.execute("""
            SELECT
                v.video_id,
                v.title,
                v.published_at,
                v.view_count,
                v.like_count,
                v.comment_count,
                v.channel_id,
                c.title AS channel_title,
                c.subscriber_count
            FROM videos v
            LEFT JOIN channels c ON v.channel_id = c.channel_id
            WHERE v.status = 'active'
            ORDER BY v.view_count DESC
        """).fetchall()

        data = []
        for r in rows:
            data.append({
                'video_id': r['video_id'],
                'title': r['title'] or 'Untitled',
                'channel_title': r['channel_title'] or 'Unknown Artist',
                'published_at': r['published_at'],
                'view_count': r['view_count'] or 0,
                'like_count': r['like_count'] or 0,
                'comment_count': r['comment_count'] or 0,
                'subscriber_count': r['subscriber_count'] or 0,
                'channel_id': r['channel_id'],
            })
        return data
    except Exception as e:
        log.error(f"Error reading YouTube data: {e}")
        return []
    finally:
        conn.close()


def _calculate_recency_score(published_at_str, now=None):
    """
    Calculate recency bonus: 1.0 for today, decaying to 0.0 over RECENCY_WINDOW_DAYS.
    Videos older than the window get a small floor bonus (0.05) so they aren't completely zeroed.
    """
    if not published_at_str:
        return 0.05

    if now is None:
        now = datetime.utcnow()

    try:
        # Handle ISO format with or without timezone
        pub_str = published_at_str.replace('Z', '+00:00')
        if '+' in pub_str:
            pub_str = pub_str.split('+')[0]
        pub_dt = datetime.fromisoformat(pub_str)
    except (ValueError, TypeError):
        return 0.05

    days_old = (now - pub_dt).total_seconds() / 86400.0
    if days_old < 0:
        days_old = 0

    if days_old <= RECENCY_WINDOW_DAYS:
        # Linear decay from 1.0 to 0.1 over the window
        return 1.0 - (0.9 * days_old / RECENCY_WINDOW_DAYS)
    else:
        # Slow logarithmic decay for older content, floor at 0.02
        decay = 0.1 / (1 + math.log1p(days_old - RECENCY_WINDOW_DAYS) / 5.0)
        return max(decay, 0.02)


def _calculate_sub_normalized_views(views, subscriber_count):
    """
    Calculate subscriber-normalized view ratio.
    Higher ratio means the video is outperforming the channel's subscriber base.
    Capped at 10.0 to prevent tiny-channel outliers from dominating.
    """
    if subscriber_count <= 0:
        # No subscriber data — use a neutral score
        return 0.5
    ratio = views / subscriber_count
    # Cap and normalize to 0-1 range (ratio of 10+ = max score)
    return min(ratio / 10.0, 1.0)


def calculate_power_scores(videos, now=None):
    """
    Calculate Power Score for each video.

    The raw components are normalized across the full dataset before weighting,
    so each component contributes proportionally regardless of scale.

    Returns a list of dicts sorted by power_score descending.
    """
    if not videos:
        return []

    if now is None:
        now = datetime.utcnow()

    # --- Step 1: Compute raw component values ---
    for v in videos:
        v['_recency'] = _calculate_recency_score(v['published_at'], now)
        v['_sub_norm'] = _calculate_sub_normalized_views(v['view_count'], v['subscriber_count'])

    # --- Step 2: Find max values for normalization ---
    max_views = max((v['view_count'] for v in videos), default=1) or 1
    max_likes = max((v['like_count'] for v in videos), default=1) or 1
    max_comments = max((v['comment_count'] for v in videos), default=1) or 1
    # Recency and sub_norm are already 0-1 range

    # --- Step 3: Compute weighted Power Score ---
    for v in videos:
        norm_views = v['view_count'] / max_views
        norm_likes = v['like_count'] / max_likes
        norm_comments = v['comment_count'] / max_comments
        recency = v['_recency']
        sub_norm = v['_sub_norm']

        power_score = (
            W_VIEWS * norm_views
            + W_LIKES * norm_likes
            + W_COMMENTS * norm_comments
            + W_RECENCY * recency
            + W_SUB_NORM * sub_norm
        )

        # Scale to 0-100 for readability
        v['power_score'] = round(power_score * 100, 2)

    # Sort by power_score descending
    videos.sort(key=lambda x: x['power_score'], reverse=True)
    return videos


def generate_chart(hub_conn, chart_date=None):
    """
    Generate the Power Charts for a given date.

    1. Fetches YouTube data
    2. Calculates Power Scores
    3. Determines week-over-week movement vs previous chart
    4. Saves chart_entries and chart_history to the hub DB
    5. Returns the chart entries as a list of dicts

    Args:
        hub_conn: Platform hub database connection
        chart_date: ISO date string (default: today)

    Returns:
        List of chart entry dicts, or empty list on failure
    """
    if chart_date is None:
        chart_date = datetime.now().strftime('%Y-%m-%d')

    log.info(f"Generating Power Charts for {chart_date}...")

    # Fetch and score
    videos = _fetch_youtube_data()
    if not videos:
        log.warning("No video data available — chart generation aborted")
        return []

    scored = calculate_power_scores(videos)
    chart = scored[:CHART_SIZE]

    # Get previous chart for movement tracking
    prev_chart = get_previous_chart(hub_conn, chart_date)

    # Build chart entries with movement
    entries = []
    for rank_idx, v in enumerate(chart):
        rank = rank_idx + 1
        video_id = v['video_id']
        prev = prev_chart.get(video_id)

        if prev is None:
            movement = 'NEW'
            previous_rank = None
            weeks_on_chart = 1
        else:
            previous_rank = prev['rank']
            weeks_on_chart = prev['weeks_on_chart'] + 1
            if rank < previous_rank:
                movement = 'UP'
            elif rank > previous_rank:
                movement = 'DOWN'
            else:
                movement = 'STABLE'

        entry = {
            'chart_date': chart_date,
            'rank': rank,
            'previous_rank': previous_rank,
            'video_id': video_id,
            'title': v['title'],
            'artist': v['channel_title'],
            'power_score': v['power_score'],
            'views': v['view_count'],
            'likes': v['like_count'],
            'comments': v['comment_count'],
            'subscriber_count': v['subscriber_count'],
            'movement': movement,
            'weeks_on_chart': weeks_on_chart,
        }
        entries.append(entry)

        # Persist to DB
        save_chart_entry(
            hub_conn, chart_date, rank, previous_rank, video_id,
            v['title'], v['channel_title'], v['power_score'],
            v['view_count'], v['like_count'], v['comment_count'],
            v['subscriber_count'], movement, weeks_on_chart,
        )
        save_chart_history(
            hub_conn, chart_date, video_id, rank,
            v['power_score'], v['view_count'], v['like_count'], v['comment_count'],
        )

    hub_conn.commit()
    log.info(f"Power Charts saved: {len(entries)} entries for {chart_date}")
    return entries


def _movement_arrow(movement, previous_rank, current_rank):
    """Return a text movement indicator for the chart display."""
    if movement == 'NEW':
        return 'NEW'
    elif movement == 'UP':
        diff = previous_rank - current_rank
        return f'^{diff}'  # up arrow with positions gained
    elif movement == 'DOWN':
        diff = current_rank - previous_rank
        return f'v{diff}'  # down arrow with positions lost
    else:
        return '='


def _format_number(n):
    """Format large numbers with K/M suffixes."""
    if n is None:
        return '0'
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def generate_chart_report(hub_conn, chart_date=None):
    """
    Generate the Power Charts and write a markdown report.

    Returns the path to the generated report file.
    """
    if chart_date is None:
        chart_date = datetime.now().strftime('%Y-%m-%d')

    # Generate the chart (or re-read if already generated today)
    entries = generate_chart(hub_conn, chart_date)
    if not entries:
        log.warning("No chart entries to report")
        return None

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f'power_charts_{chart_date}.md')

    # --- Compute highlight stats ---
    biggest_mover = None
    biggest_mover_diff = 0
    highest_new_entry = None
    longest_running = None
    longest_weeks = 0

    for e in entries:
        # Biggest Mover: largest rank improvement (UP only)
        if e['movement'] == 'UP' and e['previous_rank'] is not None:
            diff = e['previous_rank'] - e['rank']
            if diff > biggest_mover_diff:
                biggest_mover_diff = diff
                biggest_mover = e

        # Highest New Entry: NEW entry with lowest rank number (highest position)
        if e['movement'] == 'NEW':
            if highest_new_entry is None or e['rank'] < highest_new_entry['rank']:
                highest_new_entry = e

        # Longest Running: most weeks on chart
        if e['weeks_on_chart'] > longest_weeks:
            longest_weeks = e['weeks_on_chart']
            longest_running = e

    # --- Build report ---
    lines = [
        f"# POWER FM -- POWER CHARTS",
        f"## Week of {chart_date}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]

    # Chart table header
    lines.append("| # | Mv | Title | Artist | Power Score | Views | Likes | Comments | Wks |")
    lines.append("|---|----|-------|--------|-------------|-------|-------|----------|-----|")

    for e in entries:
        rank_str = f"**{e['rank']}**"
        mv = _movement_arrow(e['movement'], e['previous_rank'], e['rank'])
        title = e['title']
        # Truncate very long titles for table readability
        if len(title) > 55:
            title = title[:52] + '...'
        artist = e['artist']
        score_str = f"{e['power_score']:.1f}"
        views_str = _format_number(e['views'])
        likes_str = _format_number(e['likes'])
        comments_str = _format_number(e['comments'])
        weeks_str = str(e['weeks_on_chart'])

        lines.append(
            f"| {rank_str} | {mv} | {title} | {artist} | {score_str} | "
            f"{views_str} | {likes_str} | {comments_str} | {weeks_str} |"
        )

    # --- Highlights section ---
    lines.extend(["", "---", "", "## Chart Highlights", ""])

    if biggest_mover:
        diff = biggest_mover['previous_rank'] - biggest_mover['rank']
        lines.append(
            f"**Biggest Mover:** \"{biggest_mover['title']}\" by {biggest_mover['artist']} "
            f"-- UP {diff} positions (#{biggest_mover['previous_rank']} -> #{biggest_mover['rank']})"
        )
    else:
        lines.append("**Biggest Mover:** No upward movers this week")

    lines.append("")

    if highest_new_entry:
        lines.append(
            f"**Highest New Entry:** \"{highest_new_entry['title']}\" by {highest_new_entry['artist']} "
            f"-- enters at #{highest_new_entry['rank']} with Power Score {highest_new_entry['power_score']:.1f}"
        )
    else:
        lines.append("**Highest New Entry:** No new entries this week")

    lines.append("")

    if longest_running:
        lines.append(
            f"**Longest Running:** \"{longest_running['title']}\" by {longest_running['artist']} "
            f"-- {longest_running['weeks_on_chart']} week{'s' if longest_running['weeks_on_chart'] != 1 else ''} "
            f"on chart at #{longest_running['rank']}"
        )
    else:
        lines.append("**Longest Running:** N/A")

    # --- Summary stats ---
    total_views = sum(e['views'] for e in entries)
    total_likes = sum(e['likes'] for e in entries)
    total_comments = sum(e['comments'] for e in entries)
    unique_artists = len(set(e['artist'] for e in entries))
    new_entries_count = sum(1 for e in entries if e['movement'] == 'NEW')

    lines.extend([
        "",
        "---",
        "",
        "## Chart Summary",
        "",
        f"- **Total tracks charted:** {len(entries)}",
        f"- **Unique artists:** {unique_artists}",
        f"- **New entries:** {new_entries_count}",
        f"- **Combined views:** {_format_number(total_views)}",
        f"- **Combined likes:** {_format_number(total_likes)}",
        f"- **Combined comments:** {_format_number(total_comments)}",
        "",
        "---",
        "",
        "## Scoring Methodology",
        "",
        "Power Score (0-100) is calculated from five weighted components:",
        "",
        "| Component | Weight | Description |",
        "|-----------|--------|-------------|",
        "| Views | 40% | Normalized view count across all tracked videos |",
        "| Likes | 20% | Normalized like count |",
        "| Comments | 15% | Normalized comment count (engagement signal) |",
        "| Recency | 15% | Bonus for newer content, 30-day decay window |",
        "| Sub-Normalized Views | 10% | Views relative to channel subscriber count |",
        "",
        "Movement is tracked week-over-week: NEW = first appearance, UP/DOWN = rank change, = = stable.",
        "",
        f"*Power FM Platform Hub -- {chart_date}*",
        "",
    ])

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Power Charts report saved: {report_path}")
    return report_path
