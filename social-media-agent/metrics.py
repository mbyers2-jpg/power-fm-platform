#!/usr/bin/env python3
"""
Metrics fetcher for Social Media Agent.
Pulls engagement data from platform APIs and stores in database.
"""

import logging
from datetime import datetime

from database import (
    get_posts_by_campaign, get_latest_campaign, save_metrics,
    get_platform_auth, log_activity,
)

log = logging.getLogger('social-media-agent')


def fetch_all_metrics(conn, campaign_id=None):
    """
    Fetch engagement metrics for all posted content in a campaign.

    Args:
        conn: Database connection
        campaign_id: Campaign to fetch metrics for (default: latest)

    Returns:
        dict with counts: {fetched, skipped, failed}
    """
    if not campaign_id:
        campaign = get_latest_campaign(conn)
        if not campaign:
            log.warning("No campaign found.")
            return {'fetched': 0, 'skipped': 0, 'failed': 0}
        campaign_id = campaign['id']

    posts = get_posts_by_campaign(conn, campaign_id, status='posted')
    results = {'fetched': 0, 'skipped': 0, 'failed': 0}

    for post in posts:
        if not post['platform_post_id']:
            results['skipped'] += 1
            continue

        # Check platform auth
        auth = get_platform_auth(conn, post['platform'])
        if not auth or auth['auth_status'] != 'active':
            results['skipped'] += 1
            continue

        try:
            metrics = fetch_post_metrics(post['platform'], post['platform_post_id'])
            if metrics:
                # Calculate engagement rate
                total_engagement = metrics['likes'] + metrics['shares'] + metrics['comments']
                impressions = metrics['impressions'] or 1
                engagement_rate = (total_engagement / impressions) * 100 if impressions > 0 else 0.0

                save_metrics(
                    conn, post['id'],
                    likes=metrics['likes'],
                    shares=metrics['shares'],
                    comments=metrics['comments'],
                    impressions=metrics['impressions'],
                    clicks=metrics['clicks'],
                    engagement_rate=round(engagement_rate, 2),
                )
                results['fetched'] += 1
                log.info(
                    f"Metrics for post {post['id']} [{post['platform']}]: "
                    f"{metrics['likes']} likes, {metrics['shares']} shares, "
                    f"{metrics['comments']} comments, {metrics['impressions']} impressions"
                )
        except Exception as e:
            log.error(f"Failed to fetch metrics for post {post['id']}: {e}")
            results['failed'] += 1

    log_activity(
        conn, 'metrics_fetched',
        f"Fetched: {results['fetched']}, Skipped: {results['skipped']}, Failed: {results['failed']}"
    )
    return results


def fetch_post_metrics(platform, platform_post_id):
    """
    Fetch metrics for a single post from its platform.

    Args:
        platform: Platform name
        platform_post_id: The platform's ID for the post

    Returns:
        dict with likes, shares, comments, impressions, clicks
    """
    try:
        from platforms import get_platform
        p = get_platform(platform)
        return p.get_metrics(platform_post_id)
    except Exception as e:
        log.error(f"Failed to fetch metrics from {platform}: {e}")
        return None
