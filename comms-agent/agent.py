#!/usr/bin/env python3
"""
Comms Agent for Marc Byers
Manages email communications — detects unanswered emails, generates follow-up
queues, drafts responses, summarizes threads, and produces comms reports.

Usage:
    venv/bin/python agent.py              # Full scan + comms report
    venv/bin/python agent.py --daemon     # Scan every 15 minutes
    venv/bin/python agent.py --drafts     # Generate drafts for pending follow-ups
    venv/bin/python agent.py --report     # Just generate report
"""

import sys
import os
import signal
import time
import logging
from datetime import datetime, timedelta

from database import (
    get_connection, add_follow_up, save_draft, save_thread_summary,
    get_pending_follow_ups, get_pending_drafts, get_overdue_follow_ups
)
from analyzer import (
    detect_follow_ups_needed, get_unanswered_emails, get_stale_threads,
    summarize_thread, generate_draft_response
)

POLL_INTERVAL = 900  # 15 minutes
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
DRAFTS_DIR = os.path.join(os.path.dirname(__file__), 'drafts')
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'data')

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DRAFTS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'comms-agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('comms-agent')

running = True

def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received.")
    running = False

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def scan_follow_ups(conn):
    """Detect and store new follow-up needs."""
    log.info("Scanning for follow-up needs...")
    follow_ups = detect_follow_ups_needed()

    new_count = 0
    for fu in follow_ups:
        # Skip if we already have a pending follow-up for this contact+subject
        existing = conn.execute("""
            SELECT id FROM follow_ups
            WHERE contact_email = ? AND subject = ? AND status = 'pending'
        """, (fu.get('contact_email', ''), fu.get('subject', ''))).fetchone()

        if not existing:
            add_follow_up(conn,
                contact_email=fu.get('contact_email', ''),
                contact_name=fu.get('contact_name', ''),
                subject=fu.get('subject', ''),
                reason=fu.get('reason', ''),
                priority=fu.get('priority', 'medium'),
                email_id=fu.get('email_id'),
                thread_id=fu.get('thread_id'),
            )
            new_count += 1

    log.info(f"Found {len(follow_ups)} follow-up needs, {new_count} new")
    return new_count


def generate_drafts(conn):
    """Generate draft responses for pending follow-ups."""
    log.info("Generating draft responses...")
    follow_ups = get_pending_follow_ups(conn)

    draft_count = 0
    for fu in follow_ups:
        # Check if we already have a draft for this
        existing = conn.execute("""
            SELECT id FROM drafts
            WHERE to_address = ? AND subject = ? AND status = 'pending_review'
        """, (fu['contact_email'], fu['subject'])).fetchone()

        if existing:
            continue

        draft_data = generate_draft_response(dict(fu))
        draft_id = save_draft(conn,
            to_address=draft_data['to'],
            subject=draft_data['subject'],
            body=draft_data['body'],
            draft_type=draft_data['draft_type'],
            thread_id=fu['thread_id'],
            reply_to=fu['email_id'],
        )

        # Also save as markdown file for easy review
        today = datetime.now().strftime('%Y-%m-%d')
        safe_subject = ''.join(c for c in fu['subject'][:50] if c.isalnum() or c in ' -_').strip()
        draft_path = os.path.join(DRAFTS_DIR, f"{today}_{safe_subject}.md")

        with open(draft_path, 'w') as f:
            f.write(f"# Draft Response\n\n")
            f.write(f"**To:** {draft_data['to']}\n")
            f.write(f"**Subject:** {draft_data['subject']}\n")
            f.write(f"**Reason:** {fu['reason']}\n")
            f.write(f"**Priority:** {fu['priority']}\n\n")
            f.write(f"---\n\n")
            f.write(draft_data['body'])
            f.write(f"\n\n---\n*Draft generated {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
            f.write(f"*Review and edit before sending. Replace [BRACKETED] sections.*\n")

        draft_count += 1
        log.info(f"Draft created: {draft_path}")

    log.info(f"Generated {draft_count} new drafts")
    return draft_count


def generate_comms_report(conn):
    """Generate a communications status report."""
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'comms_report_{today}.md')

    pending = get_pending_follow_ups(conn)
    overdue = get_overdue_follow_ups(conn)
    drafts = get_pending_drafts(conn)

    # Stats
    total_pending = len(pending)
    total_overdue = len(overdue)
    total_drafts = len(drafts)
    critical = [f for f in pending if f['priority'] == 'critical']
    high = [f for f in pending if f['priority'] == 'high']

    lines = [
        f"# Communications Report — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Pending Follow-ups | {total_pending} |",
        f"| Overdue | {total_overdue} |",
        f"| Critical Priority | {len(critical)} |",
        f"| High Priority | {len(high)} |",
        f"| Draft Responses Ready | {total_drafts} |",
        "",
    ]

    if overdue:
        lines.append("## OVERDUE Follow-ups")
        lines.append("")
        for fu in overdue:
            lines.append(f"- **[OVERDUE]** {fu['subject']}")
            lines.append(f"  Contact: {fu['contact_name']} ({fu['contact_email']})")
            lines.append(f"  Due: {fu['due_date']} | Reason: {fu['reason']}")
            lines.append("")

    if critical or high:
        lines.append("## Priority Follow-ups")
        lines.append("")
        for fu in critical + high:
            tag = fu['priority'].upper()
            lines.append(f"- **[{tag}]** {fu['subject']}")
            lines.append(f"  Contact: {fu['contact_name']} ({fu['contact_email']})")
            lines.append(f"  Reason: {fu['reason']}")
            lines.append("")

    if pending:
        lines.append("## All Pending Follow-ups")
        lines.append("")
        lines.append("| Priority | Contact | Subject | Reason |")
        lines.append("|----------|---------|---------|--------|")
        for fu in pending[:30]:
            lines.append(f"| {fu['priority']} | {fu['contact_name'] or fu['contact_email']} | {fu['subject'][:40]} | {fu['reason'][:50]} |")
        lines.append("")

    if drafts:
        lines.append("## Draft Responses (Pending Review)")
        lines.append("")
        lines.append(f"Review drafts in: `~/Agents/comms-agent/drafts/`")
        lines.append("")
        for d in drafts[:10]:
            lines.append(f"- **To:** {d['to_address']} — {d['subject'][:50]}")
        lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Comms report generated: {report_path}")
    return report_path


def run_once(conn):
    """Full scan cycle."""
    scan_follow_ups(conn)
    generate_drafts(conn)
    report_path = generate_comms_report(conn)
    return report_path


def run_daemon(conn):
    """Continuous polling."""
    log.info(f"Comms agent starting in daemon mode (polling every {POLL_INTERVAL}s)")

    run_once(conn)

    while running:
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        run_once(conn)

    log.info("Comms agent stopped.")


def main():
    daemon_mode = '--daemon' in sys.argv
    report_only = '--report' in sys.argv
    drafts_only = '--drafts' in sys.argv

    log.info("Initializing comms agent...")
    conn = get_connection()

    if report_only:
        report = generate_comms_report(conn)
        print(f"Report: {report}")
    elif drafts_only:
        count = generate_drafts(conn)
        print(f"Drafts generated: {count}")
        print(f"Review in: ~/Agents/comms-agent/drafts/")
    elif daemon_mode:
        run_daemon(conn)
    else:
        report = run_once(conn)
        pending = get_pending_follow_ups(conn)
        drafts = get_pending_drafts(conn)
        print(f"\nComms report: {report}")
        print(f"Pending follow-ups: {len(pending)}")
        print(f"Draft responses: {len(drafts)}")
        print(f"Review drafts: ~/Agents/comms-agent/drafts/")

    conn.close()


if __name__ == '__main__':
    main()
