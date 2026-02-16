#!/usr/bin/env python3
"""
Report generator for Social Media Agent.
Produces markdown engagement reports.
"""

import os
import logging
from datetime import datetime

from database import (
    get_latest_campaign, get_campaign, get_posts_by_campaign,
    get_post_counts_by_status, get_campaign_metrics, get_all_platform_auth,
    get_recent_activity, log_activity,
)

log = logging.getLogger('social-media-agent')

REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')


def generate_engagement_report(conn, campaign_id=None):
    """
    Generate a comprehensive engagement report for a campaign.

    Args:
        conn: Database connection
        campaign_id: Campaign to report on (default: latest)

    Returns:
        Path to generated report file
    """
    os.makedirs(REPORT_DIR, exist_ok=True)

    if not campaign_id:
        campaign = get_latest_campaign(conn)
        if not campaign:
            log.warning("No campaign found for report.")
            return None
        campaign_id = campaign['id']
    else:
        campaign = get_campaign(conn, campaign_id)

    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'engagement_{today}.md')

    # Gather data
    status_counts = get_post_counts_by_status(conn, campaign_id)
    metrics_data = get_campaign_metrics(conn, campaign_id)
    auth_status = get_all_platform_auth(conn)

    lines = [
        f"# Social Media Engagement Report",
        f"**Campaign:** {campaign['name']}",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Start Date:** {campaign['start_date'] or 'Not set'}",
        f"**Status:** {campaign['status']}",
        "",
        "---",
        "",
        "## Campaign Overview",
        "",
    ]

    # Status summary
    total = sum(status_counts.values())
    lines.append(f"| Status | Count |")
    lines.append(f"|--------|-------|")
    for status in ['posted', 'scheduled', 'draft', 'failed']:
        count = status_counts.get(status, 0)
        lines.append(f"| {status.capitalize()} | {count} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")

    # Platform auth status
    lines.append("## Platform Connections")
    lines.append("")
    if auth_status:
        lines.append("| Platform | Status | Account |")
        lines.append("|----------|--------|---------|")
        for auth in auth_status:
            status_icon = 'ACTIVE' if auth['auth_status'] == 'active' else 'NOT CONFIGURED'
            account = auth['account_name'] or '-'
            lines.append(f"| {auth['platform'].capitalize()} | {status_icon} | {account} |")
    else:
        lines.append("No platforms configured yet.")
    lines.append("")

    # Engagement metrics
    if metrics_data:
        lines.append("## Engagement Metrics")
        lines.append("")

        # Summary totals
        total_likes = sum(m['likes'] or 0 for m in metrics_data)
        total_shares = sum(m['shares'] or 0 for m in metrics_data)
        total_comments = sum(m['comments'] or 0 for m in metrics_data)
        total_impressions = sum(m['impressions'] or 0 for m in metrics_data)
        total_clicks = sum(m['clicks'] or 0 for m in metrics_data)
        total_engagement = total_likes + total_shares + total_comments
        avg_rate = (total_engagement / total_impressions * 100) if total_impressions > 0 else 0

        lines.append("### Totals")
        lines.append("")
        lines.append(f"- **Total Impressions:** {total_impressions:,}")
        lines.append(f"- **Total Likes:** {total_likes:,}")
        lines.append(f"- **Total Shares/Retweets:** {total_shares:,}")
        lines.append(f"- **Total Comments:** {total_comments:,}")
        lines.append(f"- **Total Clicks:** {total_clicks:,}")
        lines.append(f"- **Average Engagement Rate:** {avg_rate:.2f}%")
        lines.append("")

        # Per-platform breakdown
        platform_stats = {}
        for m in metrics_data:
            p = m['platform']
            if p not in platform_stats:
                platform_stats[p] = {
                    'likes': 0, 'shares': 0, 'comments': 0,
                    'impressions': 0, 'clicks': 0, 'posts': 0,
                }
            platform_stats[p]['likes'] += m['likes'] or 0
            platform_stats[p]['shares'] += m['shares'] or 0
            platform_stats[p]['comments'] += m['comments'] or 0
            platform_stats[p]['impressions'] += m['impressions'] or 0
            platform_stats[p]['clicks'] += m['clicks'] or 0
            platform_stats[p]['posts'] += 1

        lines.append("### By Platform")
        lines.append("")
        lines.append("| Platform | Posts | Impressions | Likes | Shares | Comments | Eng. Rate |")
        lines.append("|----------|-------|-------------|-------|--------|----------|-----------|")
        for p, stats in sorted(platform_stats.items()):
            eng = stats['likes'] + stats['shares'] + stats['comments']
            rate = (eng / stats['impressions'] * 100) if stats['impressions'] > 0 else 0
            lines.append(
                f"| {p.capitalize()} | {stats['posts']} | {stats['impressions']:,} | "
                f"{stats['likes']:,} | {stats['shares']:,} | {stats['comments']:,} | {rate:.1f}% |"
            )
        lines.append("")

        # Top performing posts
        sorted_posts = sorted(metrics_data, key=lambda m: (m['likes'] or 0) + (m['shares'] or 0) + (m['comments'] or 0), reverse=True)
        top_posts = sorted_posts[:5]

        if top_posts:
            lines.append("### Top Performing Posts")
            lines.append("")
            for i, m in enumerate(top_posts, 1):
                title = m['title'] or '(untitled)'
                eng = (m['likes'] or 0) + (m['shares'] or 0) + (m['comments'] or 0)
                lines.append(
                    f"{i}. **[{m['platform'].upper()}]** {title} — "
                    f"{m['likes'] or 0} likes, {m['shares'] or 0} shares, "
                    f"{m['comments'] or 0} comments ({eng} total)"
                )
            lines.append("")
    else:
        lines.append("## Engagement Metrics")
        lines.append("")
        lines.append("No metrics data yet. Posts must be published and metrics fetched.")
        lines.append("")

    # Recent activity
    activity = get_recent_activity(conn, limit=10)
    if activity:
        lines.append("## Recent Activity")
        lines.append("")
        for a in activity:
            ts = a['timestamp'][:16] if a['timestamp'] else ''
            lines.append(f"- `{ts}` {a['action']}: {a['details'] or ''}")
        lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log_activity(conn, 'report_generated', f'Report saved to {report_path}')
    log.info(f"Engagement report generated: {report_path}")
    return report_path
