#!/usr/bin/env python3
"""
Deal Tracker Agent for Marc Byers
Scans the file system and email database to track all active deals,
flag stale ones, and generate pipeline reports.

Usage:
    venv/bin/python agent.py              # Full scan + report
    venv/bin/python agent.py --daemon     # Scan every 30 minutes
    venv/bin/python agent.py --report     # Just generate report from existing data
"""

import sys
import os
import signal
import time
import logging
from datetime import datetime, timedelta

from database import (
    get_connection, get_deal_stats, get_active_deals,
    get_stale_deals, get_deal_with_details, get_upcoming_milestones
)
from scanner import run_full_scan

POLL_INTERVAL = 1800  # 30 minutes
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'deal-tracker.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('deal-tracker')

running = True

def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received.")
    running = False

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


STAGE_ORDER = {
    'prospect': 1,
    'negotiation': 2,
    'contract': 3,
    'signed': 4,
    'active': 5,
}

PRIORITY_LABELS = {
    'critical': 'CRITICAL',
    'high': 'HIGH',
    'medium': 'MEDIUM',
    'low': 'LOW',
}


def generate_pipeline_report(conn):
    """Generate a comprehensive deal pipeline report."""
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'pipeline_{today}.md')

    stats = get_deal_stats(conn)
    active_deals = get_active_deals(conn)
    stale_deals = get_stale_deals(conn, days=30)
    upcoming = get_upcoming_milestones(conn, days=14)

    lines = [
        f"# Deal Pipeline Report — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Pipeline Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total Deals | {stats['total']} |",
        f"| Active | {stats['active']} |",
        f"| Closed (Won) | {stats['closed_won']} |",
        f"| Closed (Lost) | {stats['closed_lost']} |",
        f"| Stale (30+ days) | {stats['stale_30d']} |",
        f"| Pending Milestones | {stats['pending_milestones']} |",
        f"| Linked Documents | {stats['total_documents']} |",
        "",
    ]

    # Active deals by stage
    if active_deals:
        # Group by stage
        by_stage = {}
        for d in active_deals:
            stage = d['stage'] or 'unknown'
            by_stage.setdefault(stage, []).append(d)

        lines.append("## Active Deals by Stage")
        lines.append("")

        for stage in sorted(by_stage.keys(), key=lambda s: STAGE_ORDER.get(s, 99)):
            deals = by_stage[stage]
            lines.append(f"### {stage.upper()} ({len(deals)})")
            lines.append("")

            for d in deals:
                priority_tag = f"[{PRIORITY_LABELS.get(d['priority'], 'MEDIUM')}]" if d['priority'] != 'medium' else ''
                type_tag = f"({d['deal_type']})" if d['deal_type'] else ''

                lines.append(f"**{d['name']}** {priority_tag} {type_tag}")

                details = []
                if d['counterparty']:
                    details.append(f"Counterparty: {d['counterparty']}")
                if d['last_activity']:
                    try:
                        activity_date = d['last_activity'][:10]
                        details.append(f"Last activity: {activity_date}")
                    except (IndexError, TypeError):
                        pass
                if d['next_action']:
                    details.append(f"Next: {d['next_action']}")
                if d['folder_path']:
                    folder_short = d['folder_path'].replace(os.path.expanduser('~'), '~')
                    details.append(f"Files: {folder_short}")

                for detail in details:
                    lines.append(f"  - {detail}")

                # Get linked documents count
                doc_count = conn.execute(
                    "SELECT COUNT(*) FROM deal_documents WHERE deal_id = ?",
                    (d['id'],)
                ).fetchone()[0]
                if doc_count:
                    lines.append(f"  - Documents: {doc_count} files")

                # Get linked contacts
                contacts = conn.execute(
                    "SELECT * FROM deal_contacts WHERE deal_id = ?",
                    (d['id'],)
                ).fetchall()
                if contacts:
                    names = [c['contact_name'] or c['contact_email'] for c in contacts[:5]]
                    lines.append(f"  - Contacts: {', '.join(names)}")

                lines.append("")

    # Stale deals alert
    if stale_deals:
        lines.append("## STALE DEALS — No Activity 30+ Days")
        lines.append("")
        lines.append("These deals have had no file or email activity in over 30 days.")
        lines.append("Action needed: follow up, close out, or archive.")
        lines.append("")

        for d in stale_deals:
            last = d['last_activity'][:10] if d['last_activity'] else 'Unknown'
            lines.append(f"- **{d['name']}** — Last activity: {last}")
            if d['next_action']:
                lines.append(f"  Pending action: {d['next_action']}")
        lines.append("")

    # Upcoming milestones
    if upcoming:
        lines.append("## Upcoming Milestones (Next 14 Days)")
        lines.append("")
        for m in upcoming:
            status_icon = "OVERDUE" if m['due_date'] < today else m['due_date']
            lines.append(f"- **[{status_icon}]** {m['title']}")
            lines.append(f"  Deal: {m['deal_name']}")
            if m['description']:
                lines.append(f"  {m['description']}")
        lines.append("")

    # Gap analysis
    lines.append("## Gap Analysis")
    lines.append("")

    gaps = []
    for d in active_deals:
        detail = get_deal_with_details(conn, d['id'])
        if not detail:
            continue

        deal = detail['deal']
        docs = detail['documents']
        contacts = detail['contacts']
        milestones = detail['milestones']

        deal_gaps = []
        if not docs:
            deal_gaps.append("No documents linked")
        if not contacts:
            deal_gaps.append("No contacts associated")
        if not milestones:
            deal_gaps.append("No milestones set")
        if not deal['next_action']:
            deal_gaps.append("No next action defined")
        if deal['stage'] in ('prospect', 'negotiation') and not deal['value_estimate']:
            deal_gaps.append("No value estimate")

        if deal_gaps:
            gaps.append((deal['name'], deal_gaps))

    if gaps:
        for deal_name, deal_gaps in gaps:
            lines.append(f"**{deal_name}**")
            for g in deal_gaps:
                lines.append(f"  - {g}")
            lines.append("")
    else:
        lines.append("No critical gaps detected.")
        lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Pipeline report generated: {report_path}")
    return report_path


def run_once(conn):
    """Single scan cycle + report."""
    log.info("Starting full deal scan...")
    results = run_full_scan(conn)
    log.info(f"Scan complete: {results}")
    report_path = generate_pipeline_report(conn)
    return report_path


def run_daemon(conn):
    """Continuous scanning loop."""
    log.info(f"Deal tracker starting in daemon mode (polling every {POLL_INTERVAL}s)")

    run_once(conn)

    while running:
        log.info(f"Sleeping {POLL_INTERVAL}s until next scan...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        run_once(conn)

    log.info("Deal tracker stopped.")


def main():
    daemon_mode = '--daemon' in sys.argv
    report_only = '--report' in sys.argv

    log.info("Initializing deal tracker...")
    conn = get_connection()

    if report_only:
        report = generate_pipeline_report(conn)
        print(f"Report saved to: {report}")
    elif daemon_mode:
        run_daemon(conn)
    else:
        report = run_once(conn)
        stats = get_deal_stats(conn)
        print(f"\nPipeline report: {report}")
        print(f"Active deals: {stats['active']}")
        print(f"Stale deals (30d): {stats['stale_30d']}")
        print(f"Linked documents: {stats['total_documents']}")

    conn.close()


if __name__ == '__main__':
    main()
