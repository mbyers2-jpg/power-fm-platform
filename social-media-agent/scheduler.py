#!/usr/bin/env python3
"""
Scheduler for Social Media Agent.
Maps calendar days to real dates/times with platform-optimal posting times.
Handles thread staggering and retry scheduling.
"""

import logging
from datetime import datetime, timedelta
from dateutil import tz

log = logging.getLogger('social-media-agent')

# Optimal posting times per platform (Eastern Time)
OPTIMAL_TIMES = {
    'instagram': (12, 0),   # 12:00 PM ET
    'twitter': (9, 0),      # 9:00 AM ET
    'linkedin': (7, 30),    # 7:30 AM ET
    'facebook': (14, 0),    # 2:00 PM ET
}

# Thread tweet stagger interval (minutes)
THREAD_STAGGER_MINUTES = 2

# Retry backoff intervals (minutes)
RETRY_BACKOFF = [30, 60, 120]

ET = tz.gettz('America/New_York')
UTC = tz.UTC


def calculate_schedule(start_date_str, calendar_day, platform, thread_position=None):
    """
    Calculate the scheduled datetime for a post.

    Args:
        start_date_str: Campaign start date as 'YYYY-MM-DD'
        calendar_day: Day number from content calendar (1-based)
        platform: Platform name (instagram, twitter, linkedin, facebook)
        thread_position: Position in thread (1-based), None for non-thread posts

    Returns:
        ISO format datetime string in UTC
    """
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d')

    # Calculate the target date: start_date + (calendar_day - 1)
    target_date = start_date + timedelta(days=calendar_day - 1)

    # Get optimal time for platform
    hour, minute = OPTIMAL_TIMES.get(platform, (12, 0))

    # Create datetime in Eastern Time
    target_dt = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target_et = target_dt.replace(tzinfo=ET)

    # Apply thread stagger
    if thread_position and thread_position > 1:
        stagger = timedelta(minutes=THREAD_STAGGER_MINUTES * (thread_position - 1))
        target_et += stagger

    # Convert to UTC for storage
    target_utc = target_et.astimezone(UTC)
    return target_utc.strftime('%Y-%m-%dT%H:%M:%SZ')


def schedule_campaign_posts(conn, campaign_id, start_date_str):
    """
    Schedule all draft posts in a campaign based on their calendar_day.
    Posts without a calendar_day are left as draft.

    Args:
        conn: Database connection
        campaign_id: Campaign ID
        start_date_str: Start date as 'YYYY-MM-DD'

    Returns:
        Number of posts scheduled
    """
    from database import get_posts_by_campaign, update_post, update_campaign

    posts = get_posts_by_campaign(conn, campaign_id, status='draft')
    scheduled = 0

    for post in posts:
        if not post['calendar_day']:
            log.debug(f"Post {post['id']} has no calendar_day, skipping")
            continue

        scheduled_at = calculate_schedule(
            start_date_str,
            post['calendar_day'],
            post['platform'],
            thread_position=post['thread_position'],
        )

        update_post(conn, post['id'], status='scheduled', scheduled_at=scheduled_at)
        scheduled += 1
        log.info(f"Scheduled post {post['id']} [{post['platform']}] for {scheduled_at}")

    # Update campaign
    update_campaign(conn, campaign_id, start_date=start_date_str, status='active')

    log.info(f"Scheduled {scheduled} posts for campaign {campaign_id} starting {start_date_str}")
    return scheduled


def get_retry_time(retry_count):
    """
    Calculate next retry time based on exponential backoff.

    Args:
        retry_count: Current retry count (0-based)

    Returns:
        ISO format datetime string in UTC for next retry
    """
    if retry_count >= len(RETRY_BACKOFF):
        return None  # Max retries exceeded

    delay_minutes = RETRY_BACKOFF[retry_count]
    retry_time = datetime.utcnow() + timedelta(minutes=delay_minutes)
    return retry_time.strftime('%Y-%m-%dT%H:%M:%SZ')


def format_schedule_display(posts, start_date_str=None):
    """
    Format posts into a readable schedule display.

    Args:
        posts: List of post rows from database
        start_date_str: Optional start date for reference

    Returns:
        Formatted string for display
    """
    if not posts:
        return "No posts scheduled."

    lines = []
    if start_date_str:
        lines.append(f"Campaign Start: {start_date_str}")
        lines.append("")

    current_day = None
    for post in sorted(posts, key=lambda p: (p['scheduled_at'] or '', p['id'])):
        day = post['calendar_day']

        if day != current_day:
            current_day = day
            if day:
                # Calculate actual date
                if start_date_str:
                    actual_date = datetime.strptime(start_date_str, '%Y-%m-%d') + timedelta(days=day - 1)
                    lines.append(f"\n--- Day {day} ({actual_date.strftime('%a %b %d')}) ---")
                else:
                    lines.append(f"\n--- Day {day} ---")

        platform_tag = f"[{post['platform'].upper():10s}]"
        status_tag = f"({post['status']:9s})"
        time_str = ''
        if post['scheduled_at']:
            try:
                dt = datetime.strptime(post['scheduled_at'], '%Y-%m-%dT%H:%M:%SZ')
                dt_et = dt.replace(tzinfo=UTC).astimezone(ET)
                time_str = dt_et.strftime('%I:%M %p ET')
            except ValueError:
                time_str = post['scheduled_at']

        title = post['title'][:50] if post['title'] else post['body'][:50]
        thread_info = ''
        if post['content_type'] == 'thread' and post['thread_position']:
            thread_info = f" [{post['thread_position']}/{post['thread_position']}]"

        lines.append(f"  {platform_tag} {status_tag} {time_str:12s} {title}{thread_info}")

    return '\n'.join(lines)
