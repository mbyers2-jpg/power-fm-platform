#!/usr/bin/env python3
"""
Social Media Posting Agent for Marc Byers
Automated social media campaign management — loads content packages,
schedules posts, publishes to platforms, tracks engagement, generates reports.

Usage:
    python agent.py                          # Check schedule, post due items
    python agent.py --daemon                 # Continuous 60s polling
    python agent.py --schedule               # Show upcoming schedule
    python agent.py --status                 # Show posted/pending/failed
    python agent.py --post-now <post_id>     # Force-post immediately
    python agent.py --metrics                # Fetch engagement data
    python agent.py --report                 # Generate engagement report
    python agent.py --load-content <file>    # Load content from markdown
    python agent.py --set-start-date YYYY-MM-DD
    python agent.py --dry-run                # Preview without posting
    python agent.py --setup <platform>       # Run auth setup wizard
"""

import sys
import os
import time
import signal
import logging
import argparse
from datetime import datetime

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('social-media-agent')

# --- Graceful Shutdown ---
running = True

def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

# --- Configuration ---
POLL_INTERVAL = 60  # seconds
MAX_RETRIES = 3


def parse_args():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description='Social Media Posting Agent')
    parser.add_argument('--daemon', action='store_true', help='Run continuously (polls every 60s)')
    parser.add_argument('--schedule', action='store_true', help='Show upcoming schedule')
    parser.add_argument('--status', action='store_true', help='Show posted/pending/failed counts')
    parser.add_argument('--post-now', type=int, metavar='POST_ID', help='Force-post a specific post immediately')
    parser.add_argument('--metrics', action='store_true', help='Fetch engagement data from platforms')
    parser.add_argument('--report', action='store_true', help='Generate engagement report')
    parser.add_argument('--load-content', metavar='FILE', help='Load content from markdown file')
    parser.add_argument('--set-start-date', metavar='YYYY-MM-DD', help='Set campaign start date')
    parser.add_argument('--dry-run', action='store_true', help='Preview what would be posted without posting')
    parser.add_argument('--setup', metavar='PLATFORM', help='Run auth setup wizard for a platform')
    return parser.parse_args()


def cmd_load_content(conn, filepath):
    """Load a content package into the database."""
    from content_parser import load_content_to_db

    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        return

    campaign_id, post_count = load_content_to_db(conn, filepath)
    print(f"\nContent loaded successfully!")
    print(f"  Campaign ID: {campaign_id}")
    print(f"  Posts loaded: {post_count}")
    print(f"\nNext steps:")
    print(f"  Set start date: python agent.py --set-start-date YYYY-MM-DD")
    print(f"  View schedule:  python agent.py --schedule")
    print(f"  Dry run:        python agent.py --dry-run")


def cmd_set_start_date(conn, date_str):
    """Set the start date for the latest campaign and schedule posts."""
    from database import get_latest_campaign, update_campaign
    from scheduler import schedule_campaign_posts

    # Validate date format
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        print(f"ERROR: Invalid date format. Use YYYY-MM-DD.")
        return

    campaign = get_latest_campaign(conn)
    if not campaign:
        print("ERROR: No campaign found. Load content first.")
        return

    scheduled = schedule_campaign_posts(conn, campaign['id'], date_str)
    print(f"\nStart date set to: {date_str}")
    print(f"Posts scheduled: {scheduled}")
    print(f"\nView schedule: python agent.py --schedule")


def cmd_schedule(conn):
    """Display the posting schedule."""
    from database import get_latest_campaign, get_posts_by_campaign
    from scheduler import format_schedule_display

    campaign = get_latest_campaign(conn)
    if not campaign:
        print("No campaign found. Load content first.")
        return

    posts = get_posts_by_campaign(conn, campaign['id'])
    if not posts:
        print("No posts found in campaign.")
        return

    print(f"\n=== Schedule: {campaign['name']} ===")
    print(format_schedule_display(posts, campaign['start_date']))


def cmd_status(conn):
    """Display campaign and platform status."""
    from database import (
        get_latest_campaign, get_post_counts_by_status,
        get_all_platform_auth,
    )

    campaign = get_latest_campaign(conn)

    print("\n=== Social Media Agent Status ===\n")

    # Campaign info
    if campaign:
        print(f"Campaign: {campaign['name']}")
        print(f"Status:   {campaign['status']}")
        print(f"Start:    {campaign['start_date'] or 'Not set'}")
        print()

        counts = get_post_counts_by_status(conn, campaign['id'])
        total = sum(counts.values())
        print("Posts:")
        for status in ['posted', 'scheduled', 'draft', 'failed']:
            count = counts.get(status, 0)
            print(f"  {status:12s} {count}")
        print(f"  {'total':12s} {total}")
    else:
        print("No campaign loaded.")
        print("  Load content: python agent.py --load-content <file.md>")
    print()

    # Platform auth
    print("Platforms:")
    auth_list = get_all_platform_auth(conn)
    if auth_list:
        for auth in auth_list:
            status_icon = 'ACTIVE' if auth['auth_status'] == 'active' else 'NOT SET UP'
            account = f" ({auth['account_name']})" if auth['account_name'] else ''
            print(f"  {auth['platform']:12s} {status_icon}{account}")
    else:
        for p in ['twitter', 'linkedin', 'instagram', 'facebook']:
            print(f"  {p:12s} NOT SET UP")
    print()
    print("Setup:  python agent.py --setup <platform>")


def cmd_post_now(conn, post_id, dry_run=False):
    """Force-post a specific post immediately."""
    from database import get_post, update_post, log_activity

    post = get_post(conn, post_id)
    if not post:
        print(f"ERROR: Post {post_id} not found.")
        return

    if post['status'] == 'posted':
        print(f"Post {post_id} already posted (platform ID: {post['platform_post_id']})")
        return

    print(f"\n--- Post {post_id} ---")
    print(f"Platform: {post['platform']}")
    print(f"Type:     {post['content_type']}")
    print(f"Title:    {post['title']}")
    print(f"Body:     {post['body'][:200]}...")
    if post['hashtags']:
        print(f"Hashtags: {post['hashtags']}")
    print()

    if dry_run:
        print("[DRY RUN] Would post the above content.")
        return

    # Confirmation
    confirm = input("Post this now? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("Cancelled.")
        return

    # Post it
    result = execute_post(conn, post)
    if result:
        print(f"\nPosted successfully!")
        print(f"  Platform ID: {result.get('platform_post_id', 'N/A')}")
        print(f"  URL: {result.get('url', 'N/A')}")
    else:
        print(f"\nPost failed. Check logs for details.")


def cmd_dry_run(conn):
    """Preview what would be posted next."""
    from database import get_due_posts, get_latest_campaign, get_posts_by_campaign

    campaign = get_latest_campaign(conn)
    if not campaign:
        print("No campaign found.")
        return

    # Show due posts
    due = get_due_posts(conn)
    if due:
        print(f"\n=== Due for posting NOW ({len(due)} posts) ===\n")
        for post in due:
            _print_post_preview(post)
    else:
        print("\nNo posts due for posting right now.")

    # Show next upcoming
    all_posts = get_posts_by_campaign(conn, campaign['id'], status='scheduled')
    future = [p for p in all_posts if p['scheduled_at'] and p['scheduled_at'] > datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')]
    if future:
        print(f"\n=== Next {min(5, len(future))} upcoming posts ===\n")
        for post in future[:5]:
            _print_post_preview(post)


def _print_post_preview(post):
    """Print a formatted preview of a post."""
    print(f"  Post {post['id']} [{post['platform'].upper()}] ({post['content_type']})")
    print(f"  Scheduled: {post['scheduled_at']}")
    print(f"  Title: {post['title']}")
    body_preview = post['body'][:150].replace('\n', ' ')
    print(f"  Body: {body_preview}...")
    if post['hashtags']:
        print(f"  Tags: {post['hashtags'][:80]}")
    print()


def cmd_metrics(conn):
    """Fetch engagement metrics from platform APIs."""
    from metrics import fetch_all_metrics

    print("Fetching engagement metrics...")
    results = fetch_all_metrics(conn)
    print(f"\nMetrics fetch complete:")
    print(f"  Fetched: {results['fetched']}")
    print(f"  Skipped: {results['skipped']}")
    print(f"  Failed:  {results['failed']}")


def cmd_report(conn):
    """Generate engagement report."""
    from reporter import generate_engagement_report

    report_path = generate_engagement_report(conn)
    if report_path:
        print(f"\nReport generated: {report_path}")
        # Print report contents
        with open(report_path, 'r') as f:
            print(f.read())
    else:
        print("Failed to generate report.")


def cmd_setup(platform):
    """Run auth setup wizard for a platform."""
    from auth.setup_wizard import run_setup
    run_setup(platform)


def execute_post(conn, post):
    """
    Execute posting a single post to its platform.

    Args:
        conn: Database connection
        post: Post row from database

    Returns:
        Result dict on success, None on failure
    """
    from database import update_post, log_activity, set_platform_auth
    from platforms import get_platform, PlatformError, RateLimitError, AuthError
    from scheduler import get_retry_time

    platform_name = post['platform']

    try:
        platform = get_platform(platform_name)

        # Validate auth before posting
        auth_result = platform.validate_auth()
        if not auth_result.get('valid'):
            error = auth_result.get('error', 'Auth not configured')
            log.error(f"Auth invalid for {platform_name}: {error}")
            update_post(conn, post['id'], status='failed', last_error=f"Auth invalid: {error}")
            return None

        # Update auth status in DB
        set_platform_auth(
            conn, platform_name, 'active',
            account_name=auth_result.get('account_name'),
            account_id=auth_result.get('account_id'),
            token_expiry=auth_result.get('expires_at'),
        )

        # Handle thread posts
        if post['content_type'] == 'thread' and post['thread_position'] == 1:
            result = execute_thread(conn, post)
            return result

        # Determine reply_to for non-first thread tweets
        reply_to = None
        if post['content_type'] == 'thread' and post['thread_id'] and post['thread_position'] > 1:
            # Find the previous tweet in the thread
            prev = conn.execute(
                """SELECT platform_post_id FROM posts
                   WHERE thread_id = ? AND thread_position = ? AND status = 'posted'""",
                (post['thread_id'], post['thread_position'] - 1)
            ).fetchone()
            if prev:
                reply_to = prev['platform_post_id']
            else:
                # Previous tweet not posted, can't continue thread
                update_post(conn, post['id'], status='failed', last_error='Previous thread tweet not posted')
                return None

        # Post
        result = platform.post(
            body=post['body'],
            hashtags=post['hashtags'] or '',
            media_path=post['media_path'],
            reply_to=reply_to,
        )

        # Success
        update_post(
            conn, post['id'],
            status='posted',
            posted_at=datetime.utcnow().isoformat(),
            platform_post_id=result.get('platform_post_id', ''),
        )
        log_activity(
            conn, 'post_published',
            f"[{platform_name}] {post['title'][:50]} -> {result.get('platform_post_id', '')}",
            post_id=post['id']
        )
        log.info(f"Posted {post['id']} to {platform_name}: {result.get('url', '')}")
        return result

    except RateLimitError as e:
        log.warning(f"Rate limited on {platform_name}: {e}")
        retry_time = get_retry_time(post['retry_count'])
        if retry_time:
            update_post(
                conn, post['id'],
                status='scheduled', scheduled_at=retry_time,
                retry_count=post['retry_count'] + 1,
                last_error=str(e),
            )
        else:
            update_post(conn, post['id'], status='failed', last_error=str(e))
        return None

    except AuthError as e:
        log.error(f"Auth error on {platform_name}: {e}")
        update_post(conn, post['id'], status='failed', last_error=str(e))
        set_platform_auth(conn, platform_name, 'expired')
        return None

    except PlatformError as e:
        log.error(f"Platform error on {platform_name}: {e}")
        if e.retryable:
            retry_time = get_retry_time(post['retry_count'])
            if retry_time:
                update_post(
                    conn, post['id'],
                    status='scheduled', scheduled_at=retry_time,
                    retry_count=post['retry_count'] + 1,
                    last_error=str(e),
                )
            else:
                update_post(conn, post['id'], status='failed', last_error=str(e))
        else:
            update_post(conn, post['id'], status='failed', last_error=str(e))
        return None

    except Exception as e:
        log.error(f"Unexpected error posting {post['id']} to {platform_name}: {e}")
        update_post(conn, post['id'], status='failed', last_error=str(e))
        return None


def execute_thread(conn, first_post):
    """
    Execute posting an entire thread atomically.
    If any tweet fails, remaining tweets are marked failed.

    Args:
        conn: Database connection
        first_post: The first post in the thread

    Returns:
        Result of first post on success, None on failure
    """
    from database import update_post, log_activity
    from platforms import get_platform

    thread_id = first_post['id']  # thread_id references the first post's id
    thread_posts = conn.execute(
        """SELECT * FROM posts
           WHERE thread_id = ? OR id = ?
           ORDER BY thread_position""",
        (thread_id, thread_id)
    ).fetchall()

    if not thread_posts:
        thread_posts = [first_post]

    platform = get_platform(first_post['platform'])
    results = []
    reply_to = None

    for post in thread_posts:
        try:
            result = platform.post(
                body=post['body'],
                hashtags=post['hashtags'] or '',
                media_path=post['media_path'],
                reply_to=reply_to,
            )

            update_post(
                conn, post['id'],
                status='posted',
                posted_at=datetime.utcnow().isoformat(),
                platform_post_id=result.get('platform_post_id', ''),
            )
            results.append(result)
            reply_to = result.get('platform_post_id')

            log.info(f"Thread tweet {post['thread_position']}: {result.get('platform_post_id', '')}")

            # Stagger between tweets
            if post != thread_posts[-1]:
                time.sleep(5)

        except Exception as e:
            log.error(f"Thread tweet {post['thread_position']} failed: {e}")
            # Mark remaining tweets as failed
            for remaining in thread_posts[thread_posts.index(post):]:
                if dict(remaining)['id'] != post['id'] or not results:
                    update_post(conn, remaining['id'], status='failed', last_error=f"Thread broken: {e}")
            if not results:
                update_post(conn, post['id'], status='failed', last_error=str(e))
            break

    if results:
        log_activity(
            conn, 'thread_published',
            f"[{first_post['platform']}] Thread: {len(results)}/{len(thread_posts)} tweets posted",
            post_id=first_post['id']
        )
        return results[0]
    return None


def process_due_posts(conn, dry_run=False):
    """
    Check for and process any posts that are due.

    Args:
        conn: Database connection
        dry_run: If True, only preview without posting

    Returns:
        Number of posts processed
    """
    from database import get_due_posts

    due = get_due_posts(conn)
    if not due:
        return 0

    log.info(f"Found {len(due)} posts due for publishing")

    processed = 0
    for post in due:
        if dry_run:
            log.info(f"[DRY RUN] Would post {post['id']} [{post['platform']}]: {post['title'][:50]}")
            processed += 1
            continue

        result = execute_post(conn, post)
        if result:
            processed += 1

    return processed


def run_once(conn, dry_run=False):
    """Single check-and-post cycle."""
    processed = process_due_posts(conn, dry_run=dry_run)
    if processed:
        log.info(f"Processed {processed} posts")
    else:
        log.info("No posts due")
    return processed


def run_daemon(conn):
    """Continuous polling loop."""
    log.info("Social media agent starting in daemon mode")
    log.info(f"Polling every {POLL_INTERVAL} seconds")

    # Initial check
    process_due_posts(conn)

    while running:
        log.debug(f"Sleeping {POLL_INTERVAL}s until next check...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        process_due_posts(conn)

    log.info("Social media agent stopped.")


def main():
    args = parse_args()

    from database import get_connection
    conn = get_connection()

    try:
        if args.setup:
            cmd_setup(args.setup)
        elif args.load_content:
            cmd_load_content(conn, args.load_content)
        elif args.set_start_date:
            cmd_set_start_date(conn, args.set_start_date)
        elif args.schedule:
            cmd_schedule(conn)
        elif args.status:
            cmd_status(conn)
        elif args.post_now is not None:
            cmd_post_now(conn, args.post_now, dry_run=args.dry_run)
        elif args.metrics:
            cmd_metrics(conn)
        elif args.report:
            cmd_report(conn)
        elif args.dry_run:
            cmd_dry_run(conn)
        elif args.daemon:
            run_daemon(conn)
        else:
            # Default: single check-and-post cycle
            run_once(conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
