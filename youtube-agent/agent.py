#!/usr/bin/env python3
"""
YouTube Agent for Power FM Platform
Layer 2 (Distribution) + Layer 3 (YouTube-to-FM Bridge)

Monitor channel analytics, manage uploads, extract audio from videos
for FM bridge, and track performance metrics.

Usage:
    venv/bin/python agent.py --scan                  # Pull data for all tracked channels
    venv/bin/python agent.py --add-channel UC...      # Add a channel to track
    venv/bin/python agent.py --extract VIDEO_ID       # Queue audio extraction
    venv/bin/python agent.py --analytics              # Pull latest video metrics
    venv/bin/python agent.py --report                 # Generate report
    venv/bin/python agent.py --daemon                 # Run continuously (hourly polls)
"""

import os
import sys
import json
import time
import signal
import logging
import argparse
import subprocess
import shutil
from datetime import datetime, timedelta

# Setup paths
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import (
    get_connection, save_channel, save_video, save_analytics,
    save_playlist, save_comment, create_extraction, update_extraction,
    get_all_channels, get_channel_by_id, get_video_by_id,
    get_top_videos, get_recent_videos, get_all_extractions,
    get_pending_extractions, get_channel_videos_db,
    get_agent_state, set_agent_state, get_stats,
)
from api_client import YouTubeClient

# --- Configuration ---
POLL_INTERVAL = 3600  # 1 hour
LOG_DIR = os.path.join(AGENT_DIR, 'logs')
REPORTS_DIR = os.path.join(AGENT_DIR, 'reports')
EXTRACTIONS_DIR = os.path.join(AGENT_DIR, 'extractions')

# --- Logging Setup ---
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('youtube-agent')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


# --- Core Operations ---

def init_client():
    """Initialize YouTube API client, handling missing credentials gracefully."""
    try:
        client = YouTubeClient()
        if not client.api_key:
            log.warning(
                "YouTube API key not configured. "
                "Run with --add-channel after setting up config/youtube_config.json. "
                "See SETUP.md for instructions."
            )
            return None
        return client
    except Exception as e:
        log.error(f"Failed to initialize YouTube client: {e}")
        return None


def scan_channels(client, conn):
    """Pull latest data for all tracked channels."""
    channels = get_all_channels(conn)
    if not channels:
        log.info("No channels tracked. Use --add-channel to add one.")
        return 0

    total_videos = 0
    for ch in channels:
        channel_id = ch['channel_id']
        log.info(f"Scanning channel: {ch['title'] or channel_id}")

        try:
            # Update channel info
            channel_data = client.get_channel(channel_id)
            if channel_data:
                save_channel(conn, channel_data)
                log.info(
                    f"  Channel updated: {channel_data['title']} "
                    f"({channel_data['subscriber_count']:,} subs, "
                    f"{channel_data['view_count']:,} views)"
                )

            # Fetch recent videos
            videos = client.get_channel_videos(channel_id, max_results=50)
            for video in videos:
                save_video(conn, video)
                total_videos += 1

            log.info(f"  Saved {len(videos)} videos from {channel_data.get('title', channel_id) if channel_data else channel_id}")

            # Fetch playlists
            playlists = client.get_playlists(channel_id)
            for pl in playlists:
                save_playlist(conn, pl)
            if playlists:
                log.info(f"  Saved {len(playlists)} playlists")

        except RuntimeError as e:
            log.error(f"  Error scanning channel {channel_id}: {e}")
            continue

    set_agent_state(conn, 'last_scan', datetime.utcnow().isoformat())
    log.info(f"Scan complete: {len(channels)} channels, {total_videos} videos updated")
    return total_videos


def add_channel(client, conn, channel_id):
    """Add a new channel to track."""
    # Check if already tracked
    existing = get_channel_by_id(conn, channel_id)
    if existing:
        log.info(f"Channel already tracked: {existing['title']} ({channel_id})")
        return existing

    if not client:
        # If no API key, still add the channel ID for later scanning
        save_channel(conn, {
            'channel_id': channel_id,
            'title': '(pending API scan)',
        })
        log.info(f"Channel {channel_id} added (will be populated on next scan with API key)")
        return None

    # Fetch channel info
    channel_data = client.get_channel(channel_id)
    if not channel_data:
        log.error(f"Channel not found: {channel_id}")
        return None

    save_channel(conn, channel_data)
    log.info(
        f"Added channel: {channel_data['title']} "
        f"({channel_data['subscriber_count']:,} subscribers)"
    )

    # Initial video fetch
    videos = client.get_channel_videos(channel_id, max_results=50)
    for video in videos:
        save_video(conn, video)
    log.info(f"  Imported {len(videos)} videos")

    # Fetch playlists
    playlists = client.get_playlists(channel_id)
    for pl in playlists:
        save_playlist(conn, pl)
    if playlists:
        log.info(f"  Imported {len(playlists)} playlists")

    return channel_data


def pull_analytics(client, conn):
    """Pull latest metrics for all tracked videos."""
    channels = get_all_channels(conn)
    if not channels:
        log.info("No channels tracked.")
        return 0

    today = datetime.utcnow().strftime('%Y-%m-%d')
    updated = 0

    for ch in channels:
        videos = get_channel_videos_db(conn, ch['channel_id'], limit=100)
        video_ids = [v['video_id'] for v in videos]

        if not video_ids:
            continue

        log.info(f"Pulling analytics for {len(video_ids)} videos from {ch['title'] or ch['channel_id']}")

        # Fetch fresh stats in batches of 50
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            try:
                for vid_id in batch:
                    video_data = client.get_video(vid_id)
                    if video_data:
                        # Update video record with latest counts
                        save_video(conn, video_data)

                        # Save analytics snapshot for today
                        save_analytics(conn, {
                            'video_id': vid_id,
                            'date': today,
                            'views': video_data.get('view_count', 0),
                            'likes': video_data.get('like_count', 0),
                            'comments': video_data.get('comment_count', 0),
                        })
                        updated += 1
            except RuntimeError as e:
                log.error(f"  Error pulling analytics: {e}")
                break

    set_agent_state(conn, 'last_analytics', datetime.utcnow().isoformat())
    log.info(f"Analytics updated for {updated} videos")
    return updated


def queue_extraction(conn, video_id, client=None):
    """Queue a video for audio extraction."""
    # Check if video exists in DB
    video = get_video_by_id(conn, video_id)

    if not video and client:
        # Try to fetch video info from API
        video_data = client.get_video(video_id)
        if video_data:
            save_video(conn, video_data)
            video = get_video_by_id(conn, video_id)

    source_url = f"https://www.youtube.com/watch?v={video_id}"

    # Create extraction record regardless of whether yt-dlp is available
    create_extraction(conn, video_id, source_url=source_url, fmt='mp3')

    title = video['title'] if video else video_id
    log.info(f"Audio extraction queued: {title}")
    log.info(f"  Source: {source_url}")

    # Attempt extraction if yt-dlp is available
    _attempt_extraction(conn, video_id, source_url)

    return True


def _attempt_extraction(conn, video_id, source_url):
    """Try to extract audio using yt-dlp Python API."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        log.warning(
            "yt-dlp is not installed. Audio extraction queued but cannot proceed. "
            "Install with: venv/bin/pip install yt-dlp"
        )
        return False

    os.makedirs(EXTRACTIONS_DIR, exist_ok=True)

    # Get the pending extraction record
    row = conn.execute(
        "SELECT id FROM audio_extractions WHERE video_id = ? AND status = 'pending' "
        "ORDER BY created_at DESC LIMIT 1",
        (video_id,)
    ).fetchone()

    if not row:
        return False

    extraction_id = row['id']
    output_template = os.path.join(EXTRACTIONS_DIR, f"{video_id}.%(ext)s")

    ffmpeg_path = shutil.which('ffmpeg') or os.path.expanduser('~/.local/bin/ffmpeg')
    has_ffmpeg = os.path.isfile(ffmpeg_path)

    ydl_opts = {
        'format': 'bestaudio/best/18',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['android_vr']}},
    }

    if has_ffmpeg:
        ydl_opts['ffmpeg_location'] = os.path.dirname(ffmpeg_path)

    if has_ffmpeg:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        expected_ext = 'mp3'
    else:
        log.warning("ffmpeg not found — downloading video+audio (install ffmpeg for MP3 extraction)")
        expected_ext = None

    try:
        log.info(f"Extracting audio from {source_url}...")
        update_extraction(conn, extraction_id, status='downloading')

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(source_url, download=True)

        # Find the output file
        if expected_ext:
            output_path = os.path.join(EXTRACTIONS_DIR, f"{video_id}.{expected_ext}")
        else:
            output_path = None
            for ext in ['m4a', 'webm', 'opus', 'mp3', 'ogg', 'mp4']:
                candidate = os.path.join(EXTRACTIONS_DIR, f"{video_id}.{ext}")
                if os.path.exists(candidate):
                    output_path = candidate
                    break

        if output_path and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            update_extraction(conn, extraction_id,
                              output_path=output_path,
                              file_size_bytes=file_size,
                              status='complete')
            log.info(f"  Extraction complete: {output_path} ({file_size:,} bytes)")
            return True
        else:
            log.error(f"  Output file not found after extraction")
            update_extraction(conn, extraction_id, status='failed')
            return False

    except Exception as e:
        log.error(f"  Extraction error: {e}")
        update_extraction(conn, extraction_id, status='failed')
        return False


def process_pending_extractions(conn):
    """Process any pending audio extractions."""
    pending = get_pending_extractions(conn)
    if not pending:
        return 0

    processed = 0
    for ext in pending:
        success = _attempt_extraction(conn, ext['video_id'], ext['source_url'] or '')
        if success:
            processed += 1

    return processed


# --- Report Generation ---

def generate_report(conn):
    """Generate a YouTube report markdown file."""
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    report_path = os.path.join(REPORTS_DIR, f'youtube_{today}.md')

    stats = get_stats(conn)
    channels = get_all_channels(conn)
    top_videos = get_top_videos(conn, limit=15)
    recent = get_recent_videos(conn, days=7, limit=15)
    extractions = get_all_extractions(conn)
    last_scan = get_agent_state(conn, 'last_scan', 'Never')

    lines = []

    # Header
    lines.append(f"# YouTube Report — {today}")
    lines.append(f"Generated: {now}")
    lines.append("")

    # Channel Overview
    lines.append("## Channel Overview")
    lines.append("")
    if channels:
        lines.append("| Channel | Subscribers | Videos | Total Views |")
        lines.append("|---------|-------------|--------|-------------|")
        for ch in channels:
            title = ch['title'] or ch['channel_id']
            subs = f"{ch['subscriber_count']:,}" if ch['subscriber_count'] else '0'
            vids = f"{ch['video_count']:,}" if ch['video_count'] else '0'
            views = f"{ch['view_count']:,}" if ch['view_count'] else '0'
            lines.append(f"| {title} | {subs} | {vids} | {views} |")
    else:
        lines.append("*No channels tracked. Use `--add-channel` to add one.*")
    lines.append("")

    # Top Videos
    lines.append("## Top Videos (by views)")
    lines.append("")
    if top_videos:
        lines.append("| Video | Channel | Views | Likes | Comments | Published |")
        lines.append("|-------|---------|-------|-------|----------|-----------|")
        for v in top_videos:
            title = (v['title'][:40] + '...') if len(v['title'] or '') > 40 else (v['title'] or '')
            ch_title = (v['channel_title'][:20] + '...') if len(v['channel_title'] or '') > 20 else (v['channel_title'] or v['channel_id'][:15])
            views = f"{v['view_count']:,}" if v['view_count'] else '0'
            likes = f"{v['like_count']:,}" if v['like_count'] else '0'
            comments = f"{v['comment_count']:,}" if v['comment_count'] else '0'
            published = (v['published_at'] or '')[:10]
            lines.append(f"| {title} | {ch_title} | {views} | {likes} | {comments} | {published} |")
    else:
        lines.append("*No videos tracked yet.*")
    lines.append("")

    # Recent Uploads
    lines.append("## Recent Uploads (Last 7 Days)")
    lines.append("")
    if recent:
        lines.append("| Video | Channel | Views | Published |")
        lines.append("|-------|---------|-------|-----------|")
        for v in recent:
            title = (v['title'][:45] + '...') if len(v['title'] or '') > 45 else (v['title'] or '')
            ch_title = (v['channel_title'][:20] + '...') if len(v['channel_title'] or '') > 20 else (v['channel_title'] or v['channel_id'][:15])
            views = f"{v['view_count']:,}" if v['view_count'] else '0'
            published = (v['published_at'] or '')[:10]
            lines.append(f"| {title} | {ch_title} | {views} | {published} |")
    else:
        lines.append("*No uploads in the last 7 days.*")
    lines.append("")

    # Audio Extractions
    lines.append("## Audio Extractions")
    lines.append("")
    if extractions:
        lines.append("| Video | Format | Duration | Status | Extracted |")
        lines.append("|-------|--------|----------|--------|-----------|")
        for ext in extractions:
            title = (ext['video_title'][:40] + '...') if len(ext['video_title'] or '') > 40 else (ext['video_title'] or ext['video_id'])
            fmt = ext['format'] or 'mp3'
            duration = f"{ext['duration_seconds']:.0f}s" if ext['duration_seconds'] else '-'
            status = ext['status'] or 'pending'
            extracted = (ext['extracted_at'] or '')[:10] if ext['extracted_at'] else '-'
            lines.append(f"| {title} | {fmt} | {duration} | {status} | {extracted} |")
    else:
        lines.append("*No audio extractions.*")
    lines.append("")

    # Stats
    lines.append("## Stats")
    lines.append(f"- Channels tracked: {stats['channels']}")
    lines.append(f"- Videos tracked: {stats['videos']}")
    lines.append(f"- Audio extractions: {stats['extractions']}")
    lines.append(f"- Last scan: {last_scan}")
    lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Report generated: {report_path}")
    return report_path


# --- Daemon Mode ---

def run_once(client, conn):
    """Single scan cycle."""
    if client:
        scan_channels(client, conn)
        pull_analytics(client, conn)
    else:
        log.warning("No API client available. Skipping API operations.")

    # Process any pending extractions
    processed = process_pending_extractions(conn)
    if processed:
        log.info(f"Processed {processed} pending audio extractions")

    # Generate report
    report_path = generate_report(conn)
    return report_path


def run_daemon(client, conn):
    """Continuous polling loop."""
    log.info(f"YouTube agent starting in daemon mode (polling every {POLL_INTERVAL}s)")

    # Check if quota should be reset (new day)
    last_quota_reset = get_agent_state(conn, 'last_quota_reset')
    today = datetime.utcnow().strftime('%Y-%m-%d')
    if last_quota_reset != today and client:
        client.reset_quota()
        set_agent_state(conn, 'last_quota_reset', today)

    # Initial scan
    run_once(client, conn)

    while running:
        log.info(f"Sleeping {POLL_INTERVAL}s until next scan...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        # Reset quota on new day
        current_day = datetime.utcnow().strftime('%Y-%m-%d')
        if current_day != get_agent_state(conn, 'last_quota_reset') and client:
            client.reset_quota()
            set_agent_state(conn, 'last_quota_reset', current_day)
            log.info("Daily quota counter reset")

        run_once(client, conn)

    log.info("YouTube agent stopped.")


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="YouTube Agent for Power FM")
    parser.add_argument('--scan', action='store_true',
                        help='Pull channel data for all tracked channels')
    parser.add_argument('--add-channel', type=str, metavar='CHANNEL_ID',
                        help='Add a YouTube channel to track')
    parser.add_argument('--extract', type=str, metavar='VIDEO_ID',
                        help='Queue a video for audio extraction')
    parser.add_argument('--extract-all', action='store_true', dest='extract_all',
                        help='Process all pending audio extractions')
    parser.add_argument('--analytics', action='store_true',
                        help='Pull latest metrics for tracked videos')
    parser.add_argument('--report', action='store_true',
                        help='Generate YouTube report')
    parser.add_argument('--daemon', action='store_true',
                        help='Run continuously (poll every hour)')
    args = parser.parse_args()

    log.info("Initializing YouTube agent...")
    conn = get_connection()

    # Initialize API client (may be None if no config)
    client = init_client()

    if args.add_channel:
        result = add_channel(client, conn, args.add_channel)
        if result:
            print(f"Channel added: {result.get('title', args.add_channel)}")
        else:
            if not client:
                print(f"Channel ID {args.add_channel} saved. Configure API key to fetch details.")
            else:
                print(f"Failed to add channel: {args.add_channel}")

    elif args.scan:
        if not client:
            print("ERROR: YouTube API key not configured. See SETUP.md")
            sys.exit(1)
        count = scan_channels(client, conn)
        print(f"Scan complete: {count} videos updated")

    elif args.extract:
        queue_extraction(conn, args.extract, client=client)
        video = get_video_by_id(conn, args.extract)
        title = video['title'] if video else args.extract
        print(f"Audio extraction queued: {title}")
        print(f"  Source: https://www.youtube.com/watch?v={args.extract}")

    elif args.extract_all:
        count = process_pending_extractions(conn)
        print(f"Processed {count} pending extractions")

    elif args.analytics:
        if not client:
            print("ERROR: YouTube API key not configured. See SETUP.md")
            sys.exit(1)
        count = pull_analytics(client, conn)
        print(f"Analytics updated for {count} videos")

    elif args.report:
        path = generate_report(conn)
        print(f"Report: {path}")

    elif args.daemon:
        run_daemon(client, conn)

    else:
        # Default: run once (scan + report)
        if client:
            run_once(client, conn)
        else:
            log.warning("No API key. Generating report from existing data only.")
            generate_report(conn)

        stats = get_stats(conn)
        print(f"\nYouTube Agent Summary:")
        print(f"  Channels tracked: {stats['channels']}")
        print(f"  Videos tracked: {stats['videos']}")
        print(f"  Audio extractions: {stats['extractions']}")

    conn.close()


if __name__ == '__main__':
    main()
