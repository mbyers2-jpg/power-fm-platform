#!/usr/bin/env python3
"""
Email Monitoring Agent for Marc Byers
Persistent agent that polls Gmail, categorizes emails, tracks contacts,
surfaces action items, and generates daily briefings.

Usage:
    venv/bin/python agent.py              # Run once (scan + briefing)
    venv/bin/python agent.py --daemon     # Run continuously (polls every 5 min)
"""

import sys
import os
import re
import json
import time
import signal
import logging
import email.utils
from datetime import datetime, timedelta
from base64 import urlsafe_b64decode

from auth import get_gmail_service
from database import (
    get_connection, save_email, update_contact, add_action_item,
    get_pending_actions, get_agent_state, set_agent_state,
    get_email_stats, get_top_contacts,
    save_shopping_item, save_travel_item, save_medical_item, save_mapping_item,
    get_active_orders, get_upcoming_travel, get_upcoming_medical, get_recent_rides,
)
from extractors import extract_structured_data

# Microsoft 365 support (optional — only active if config exists)
MS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'microsoft_config.json')
MS_AVAILABLE = os.path.exists(MS_CONFIG_PATH)

# --- Configuration ---
POLL_INTERVAL = 300  # 5 minutes
MAX_RESULTS_PER_POLL = 50
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
BRIEFING_DIR = os.path.join(os.path.dirname(__file__), 'briefings')

# Known categories for auto-classification
COMMS_KEYWORDS = [
    'meeting', 'call', 'zoom', 'calendar', 'invite', 'rsvp', 'confirm',
    'follow up', 'follow-up', 'reply', 'response', 'schedule', 'agenda',
    'check in', 'check-in', 'catch up', 'touch base', 'introduction',
    'linkedin', 'connect', 'networking'
]
MAPPING_KEYWORDS = [
    'directions', 'location', 'address', 'map', 'navigate', 'route',
    'uber', 'lyft', 'rideshare', 'parking', 'venue', 'reservation',
    'booking confirm', 'your ride', 'trip detail', 'eta', 'arrive'
]
SHOPPING_KEYWORDS = [
    'order', 'shipped', 'delivery', 'tracking', 'purchase', 'receipt',
    'cart', 'checkout', 'sale', 'discount', 'coupon', 'promo',
    'shop', 'store', 'buy', 'price drop', 'back in stock',
    'return', 'refund', 'exchange', 'invoice', 'payment'
]
MEDICAL_KEYWORDS = [
    'doctor', 'appointment', 'prescription', 'pharmacy', 'health',
    'medical', 'clinic', 'hospital', 'insurance', 'copay', 'lab result',
    'test result', 'dental', 'vision', 'therapy', 'telehealth',
    'patient', 'wellness', 'vaccine', 'immunization'
]
TRAVEL_KEYWORDS = [
    'flight', 'hotel', 'airbnb', 'boarding pass', 'itinerary',
    'check-in', 'check in', 'airport', 'airline', 'travel',
    'reservation', 'booking', 'passport', 'visa', 'layover',
    'rental car', 'cruise', 'destination', 'departure', 'arrival'
]

# --- Logging Setup ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('email-agent')

# --- Graceful Shutdown ---
running = True

def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def parse_sender(sender_raw):
    """Extract name and email from a sender string like 'John Doe <john@example.com>'."""
    name, addr = email.utils.parseaddr(sender_raw)
    return name.strip(), addr.strip().lower()


def get_header(headers, name):
    """Extract a header value from Gmail message headers."""
    for h in headers:
        if h['name'].lower() == name.lower():
            return h['value']
    return ''


def categorize_email(subject, snippet, labels):
    """Auto-categorize an email based on content keywords."""
    text = f"{subject} {snippet}".lower()

    if any(kw in text for kw in MEDICAL_KEYWORDS):
        return 'medical'
    if any(kw in text for kw in TRAVEL_KEYWORDS):
        return 'travel'
    if any(kw in text for kw in COMMS_KEYWORDS):
        return 'comms'
    if any(kw in text for kw in MAPPING_KEYWORDS):
        return 'mapping'
    if any(kw in text for kw in SHOPPING_KEYWORDS):
        return 'shopping'
    return 'general'


def assess_importance(category, is_read, labels):
    """Determine email importance level."""
    if category == 'medical':
        return 'high'
    if category == 'comms':
        return 'high'
    if 'IMPORTANT' in labels:
        return 'high'
    if category in ('shopping', 'general'):
        return 'low'
    return 'normal'


def detect_action_items(subject, snippet):
    """Detect if an email likely requires action."""
    text = f"{subject} {snippet}".lower()
    actions = []

    if any(kw in text for kw in ['sign', 'signature', 'docusign', 'please sign']):
        actions.append(('Document requires signature', 'high'))
    if any(kw in text for kw in ['invoice', 'payment due', 'amount due', 'pay']):
        actions.append(('Payment/invoice needs attention', 'high'))
    if any(kw in text for kw in ['reply', 'respond', 'get back to', 'waiting on', 'follow up']):
        actions.append(('Response needed', 'medium'))
    if any(kw in text for kw in ['deadline', 'due date', 'expires', 'by end of']):
        actions.append(('Time-sensitive deadline', 'high'))
    if any(kw in text for kw in ['review', 'feedback', 'take a look', 'attached']):
        actions.append(('Review requested', 'medium'))
    if any(kw in text for kw in ['meeting', 'call', 'zoom', 'schedule']):
        actions.append(('Meeting/call to schedule or join', 'medium'))

    return actions


def fetch_emails(service, query='', max_results=MAX_RESULTS_PER_POLL):
    """Fetch emails from Gmail API."""
    try:
        results = service.users().messages().list(
            userId='me', q=query, maxResults=max_results
        ).execute()
        return results.get('messages', [])
    except Exception as e:
        log.error(f"Failed to fetch emails: {e}")
        return []


def process_message(service, conn, msg_id):
    """Fetch full message details and store in database."""
    try:
        msg = service.users().messages().get(
            userId='me', id=msg_id, format='metadata',
            metadataHeaders=['From', 'To', 'Subject', 'Date']
        ).execute()

        headers = msg.get('payload', {}).get('headers', [])
        labels = msg.get('labelIds', [])

        subject = get_header(headers, 'Subject')
        sender_raw = get_header(headers, 'From')
        recipients = get_header(headers, 'To')
        date_str = get_header(headers, 'Date')
        snippet = msg.get('snippet', '')

        sender_name, sender_email = parse_sender(sender_raw)
        label_str = ','.join(labels)
        is_read = 'UNREAD' not in labels
        has_attachment = any(
            part.get('filename')
            for part in msg.get('payload', {}).get('parts', [])
        ) if 'parts' in msg.get('payload', {}) else False

        category = categorize_email(subject, snippet, label_str)
        importance = assess_importance(category, is_read, label_str)

        email_data = {
            'id': msg_id,
            'thread_id': msg.get('threadId', ''),
            'subject': subject,
            'sender': sender_name or sender_email,
            'sender_email': sender_email,
            'recipients': recipients,
            'date': date_str,
            'snippet': snippet,
            'labels': label_str,
            'category': category,
            'is_read': int(is_read),
            'has_attachment': int(has_attachment),
            'importance': importance,
            'source': 'gmail',
            'account_email': 'm.byers2@gmail.com',
        }

        save_email(conn, email_data)
        update_contact(conn, sender_email, name=sender_name)

        # Detect and store action items
        actions = detect_action_items(subject, snippet)
        for desc, priority in actions:
            add_action_item(conn, msg_id, f"{desc} — {subject}", priority)

        # Extract structured pillar data
        _save_pillar_data(conn, email_data)

        return email_data

    except Exception as e:
        log.error(f"Failed to process message {msg_id}: {e}")
        return None


def scan_emails(service, conn, mode='recent'):
    """
    Scan emails from Gmail.
    mode='recent': Only new emails since last scan
    mode='full': Full historical scan (last 90 days)
    """
    last_scan = get_agent_state(conn, 'last_scan_timestamp')

    if mode == 'full' or not last_scan:
        query = 'newer_than:90d'
        log.info("Running full scan (last 90 days)...")
    else:
        query = f'after:{last_scan[:10]}'
        log.info(f"Scanning emails since {last_scan[:10]}...")

    messages = fetch_emails(service, query=query, max_results=500 if mode == 'full' else MAX_RESULTS_PER_POLL)
    log.info(f"Found {len(messages)} emails to process")

    processed = 0
    for msg in messages:
        result = process_message(service, conn, msg['id'])
        if result:
            processed += 1

    set_agent_state(conn, 'last_scan_timestamp', datetime.utcnow().isoformat())
    log.info(f"Processed {processed} emails")
    return processed


def init_microsoft():
    """Initialize Microsoft 365 connection. Returns (token, account_email) or (None, None)."""
    if not MS_AVAILABLE:
        return None, None
    try:
        from auth_microsoft import get_microsoft_token
        token, account_email = get_microsoft_token()
        log.info(f"Microsoft 365 connected: {account_email}")
        return token, account_email
    except Exception as e:
        log.warning(f"Microsoft 365 not available: {e}")
        return None, None


def scan_microsoft_emails(token, account_email, conn, mode='recent'):
    """
    Scan emails from Microsoft 365 via Graph API.
    mode='recent': Only new emails since last scan
    mode='full': Last 90 days
    """
    from microsoft_fetcher import fetch_recent_messages, normalize_message

    last_scan = get_agent_state(conn, 'last_ms_scan_timestamp')

    since = None
    if mode == 'full' or not last_scan:
        # ISO 8601 date for 90 days ago
        from datetime import timezone
        since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
        log.info("Microsoft 365: Running full scan (last 90 days)...")
        max_results = 500
    else:
        since = last_scan[:19] + 'Z'  # Trim to ISO format
        log.info(f"Microsoft 365: Scanning since {since}...")
        max_results = MAX_RESULTS_PER_POLL

    messages = fetch_recent_messages(token, since=since, max_results=max_results)
    log.info(f"Microsoft 365: Found {len(messages)} emails to process")

    processed = 0
    for msg in messages:
        try:
            email_data = normalize_message(msg, account_email=account_email)

            # Apply our categorization
            category = categorize_email(
                email_data['subject'],
                email_data['snippet'],
                email_data['labels'],
            )
            email_data['category'] = category

            # Use Microsoft importance if higher than our assessment
            ms_importance = email_data.pop('_ms_importance', 'normal')
            our_importance = assess_importance(category, email_data['is_read'], email_data['labels'])
            # Pick the more urgent one
            rank = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3}
            email_data['importance'] = ms_importance if rank.get(ms_importance, 9) < rank.get(our_importance, 9) else our_importance

            save_email(conn, email_data)
            update_contact(conn, email_data['sender_email'], name=email_data['sender'])

            # Detect action items
            actions = detect_action_items(email_data['subject'], email_data['snippet'])
            for desc, priority in actions:
                add_action_item(conn, email_data['id'], f"{desc} — {email_data['subject']}", priority)

            processed += 1
        except Exception as e:
            log.error(f"Failed to process Microsoft message: {e}")

    set_agent_state(conn, 'last_ms_scan_timestamp', datetime.utcnow().isoformat())
    log.info(f"Microsoft 365: Processed {processed} emails")
    return processed


PILLAR_SAVERS = {
    'shopping': save_shopping_item,
    'travel': save_travel_item,
    'medical': save_medical_item,
    'mapping': save_mapping_item,
}


def _save_pillar_data(conn, email_data):
    """Run extractor on an email and save results to the appropriate pillar table."""
    try:
        pillar, data = extract_structured_data(email_data)
        if pillar and data:
            saver = PILLAR_SAVERS.get(pillar)
            if saver:
                saver(conn, data)
    except Exception as e:
        log.debug(f"Pillar extraction skipped for {email_data.get('id', '?')}: {e}")


def backfill_pillars(conn):
    """Re-process all pillar-category emails through extractors."""
    rows = conn.execute("""
        SELECT id, thread_id, subject, sender, sender_email, recipients,
               date, snippet, labels, category, is_read, has_attachment, importance
        FROM emails
        WHERE category IN ('shopping', 'travel', 'medical', 'mapping')
    """).fetchall()

    log.info(f"Backfilling {len(rows)} pillar-category emails...")
    extracted = 0
    for row in rows:
        email_data = dict(row)
        pillar, data = extract_structured_data(email_data)
        if pillar and data:
            saver = PILLAR_SAVERS.get(pillar)
            if saver:
                saver(conn, data)
                extracted += 1

    log.info(f"Backfill complete: {extracted} items extracted from {len(rows)} emails")
    return extracted


def generate_briefing(conn):
    """Generate a daily briefing markdown file."""
    os.makedirs(BRIEFING_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    briefing_path = os.path.join(BRIEFING_DIR, f'briefing_{today}.md')

    stats = get_email_stats(conn)
    actions = get_pending_actions(conn)
    top_contacts = get_top_contacts(conn, limit=10)

    # Urgent/deal emails from last 24h
    recent_important = conn.execute("""
        SELECT * FROM emails
        WHERE importance IN ('critical', 'high')
        AND date(first_seen) >= date('now', '-1 day')
        ORDER BY
            CASE importance WHEN 'critical' THEN 1 WHEN 'high' THEN 2 END,
            first_seen DESC
        LIMIT 20
    """).fetchall()

    lines = [
        f"# Daily Briefing — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Overview",
        f"- Total emails tracked: **{stats['total_emails']}**",
        f"- Unread: **{stats['unread']}**",
        f"- Pending action items: **{stats['pending_actions']}**",
        f"- Contacts in database: **{stats['total_contacts']}**",
    ]

    # Per-account breakdown
    if stats.get('accounts'):
        lines.append("")
        lines.append("### Accounts")
        for acct, cnt in stats['accounts'].items():
            source, email_addr = acct.split(':', 1) if ':' in acct else (acct, '')
            label = f"{email_addr} ({source})" if email_addr else source
            lines.append(f"- {label}: **{cnt}** emails")

    lines.append("")

    # --- Pillar sections ---

    # Comms: read from comms-agent DB if available
    lines.append("## Comms")
    comms_db_path = os.path.join(os.path.dirname(__file__), '..', 'comms-agent', 'data', 'comms.db')
    if os.path.exists(comms_db_path):
        try:
            import sqlite3
            comms_conn = sqlite3.connect(comms_db_path)
            comms_conn.row_factory = sqlite3.Row
            pending = comms_conn.execute(
                "SELECT COUNT(*) FROM follow_ups WHERE status = 'pending'"
            ).fetchone()[0]
            drafts = comms_conn.execute(
                "SELECT COUNT(*) FROM drafts WHERE status = 'ready'"
            ).fetchone()[0]
            lines.append(f"- Pending follow-ups: {pending} | Drafts ready: {drafts}")
            comms_conn.close()
        except Exception:
            lines.append("- Comms agent data unavailable")
    else:
        lines.append("- Comms agent not configured")
    lines.append("")

    # Shopping
    lines.append("## Shopping")
    orders = get_active_orders(conn)
    if orders:
        lines.append(f"- {len(orders)} active order(s)")
        for o in orders[:10]:
            amt = f" — ${o['amount']:.2f}" if o['amount'] else ""
            status_str = f" ({o['status']})" if o['status'] else ""
            date_str = f" ({o['order_date'][:10]})" if o['order_date'] else ""
            lines.append(f"  - {o['merchant']}: {o['description']}{amt}{status_str}{date_str}")
    else:
        lines.append("- No active orders")
    lines.append("")

    # Travel
    lines.append("## Travel")
    travel = get_upcoming_travel(conn)
    if travel:
        lines.append(f"- {len(travel)} upcoming trip(s)")
        for t in travel[:10]:
            route = ''
            if t['departure_location'] and t['arrival_location']:
                route = f" {t['departure_location']} → {t['arrival_location']}"
            date_str = f" ({t['start_date']})" if t['start_date'] else ""
            lines.append(f"  - {t['carrier']}: {t['description']}{route}{date_str}")
    else:
        lines.append("- No upcoming trips")
    lines.append("")

    # Medical
    lines.append("## Medical")
    medical = get_upcoming_medical(conn)
    if medical:
        lines.append(f"- {len(medical)} upcoming item(s)")
        for m in medical[:10]:
            date_str = f" ({m['appointment_date']})" if m['appointment_date'] else ""
            lines.append(f"  - {m['provider']}: {m['description']}{date_str}")
    else:
        lines.append("- No upcoming appointments")
    lines.append("")

    # Mapping
    lines.append("## Mapping")
    rides = get_recent_rides(conn, days=7)
    if rides:
        total_cost = sum(r['amount'] for r in rides if r['amount'])
        lines.append(f"- {len(rides)} ride(s) this week (${total_cost:.2f} total)")
        for r in rides[:10]:
            amt = f" — ${r['amount']:.2f}" if r['amount'] else ""
            date_str = f"{r['ride_date'][:10]}: " if r['ride_date'] else ""
            lines.append(f"  - {date_str}{r['service']}{amt}")
    else:
        lines.append("- No recent rides")
    lines.append("")

    # --- Action Items ---
    if actions:
        lines.append("## Action Items")
        lines.append("")
        for a in actions[:15]:
            priority_tag = a['priority'].upper()
            lines.append(f"- **[{priority_tag}]** {a['description']}")
            if a['subject']:
                lines.append(f"  Re: {a['subject']} from {a['sender']}")
            lines.append("")

    # --- Top Contacts ---
    if top_contacts:
        lines.append("## Top Contacts")
        lines.append("")
        lines.append("| Name | Email | Emails |")
        lines.append("|------|-------|--------|")
        for c in top_contacts:
            lines.append(f"| {c['name'] or 'Unknown'} | {c['email']} | {c['email_count']} |")
        lines.append("")

    content = '\n'.join(lines)
    with open(briefing_path, 'w') as f:
        f.write(content)

    log.info(f"Briefing generated: {briefing_path}")
    return briefing_path


def run_once(service, conn, ms_token=None, ms_email=None):
    """Single scan cycle + briefing."""
    # Gmail
    last_scan = get_agent_state(conn, 'last_scan_timestamp')
    mode = 'full' if not last_scan else 'recent'
    scan_emails(service, conn, mode=mode)

    # Microsoft 365
    if ms_token:
        last_ms_scan = get_agent_state(conn, 'last_ms_scan_timestamp')
        ms_mode = 'full' if not last_ms_scan else 'recent'
        scan_microsoft_emails(ms_token, ms_email, conn, mode=ms_mode)

    briefing_path = generate_briefing(conn)
    return briefing_path


def run_daemon(service, conn, ms_token=None, ms_email=None):
    """Continuous polling loop."""
    log.info("Email agent starting in daemon mode (Ctrl+C to stop)")
    log.info(f"Polling every {POLL_INTERVAL} seconds")

    # Initial full scan — Gmail
    last_scan = get_agent_state(conn, 'last_scan_timestamp')
    if not last_scan:
        scan_emails(service, conn, mode='full')
    else:
        scan_emails(service, conn, mode='recent')

    # Initial scan — Microsoft 365
    if ms_token:
        last_ms_scan = get_agent_state(conn, 'last_ms_scan_timestamp')
        if not last_ms_scan:
            scan_microsoft_emails(ms_token, ms_email, conn, mode='full')
        else:
            scan_microsoft_emails(ms_token, ms_email, conn, mode='recent')

    generate_briefing(conn)

    while running:
        log.info(f"Sleeping {POLL_INTERVAL}s until next scan...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        scan_emails(service, conn, mode='recent')

        # Re-acquire Microsoft token (may need refresh)
        if MS_AVAILABLE:
            try:
                from auth_microsoft import get_microsoft_token
                ms_token, ms_email = get_microsoft_token()
                scan_microsoft_emails(ms_token, ms_email, conn, mode='recent')
            except Exception as e:
                log.warning(f"Microsoft 365 scan skipped: {e}")

        # Regenerate briefing every hour
        last_briefing = get_agent_state(conn, 'last_briefing_timestamp')
        if not last_briefing or (
            datetime.utcnow() - datetime.fromisoformat(last_briefing)
        ) > timedelta(hours=1):
            generate_briefing(conn)
            set_agent_state(conn, 'last_briefing_timestamp', datetime.utcnow().isoformat())

    log.info("Email agent stopped.")


def main():
    daemon_mode = '--daemon' in sys.argv
    backfill_mode = '--backfill' in sys.argv

    log.info("Initializing email agent...")
    conn = get_connection()

    if backfill_mode:
        log.info("Running pillar backfill...")
        extracted = backfill_pillars(conn)
        briefing = generate_briefing(conn)
        print(f"Backfill complete: {extracted} items extracted")
        print(f"Briefing saved to: {briefing}")
        conn.close()
        return

    service = get_gmail_service()

    profile = service.users().getProfile(userId='me').execute()
    log.info(f"Connected as: {profile['emailAddress']} (Gmail)")

    # Initialize Microsoft 365 (optional)
    ms_token, ms_email = init_microsoft()

    if daemon_mode:
        run_daemon(service, conn, ms_token=ms_token, ms_email=ms_email)
    else:
        briefing = run_once(service, conn, ms_token=ms_token, ms_email=ms_email)
        print(f"\nBriefing saved to: {briefing}")
        stats = get_email_stats(conn)
        print(f"Emails tracked: {stats['total_emails']}")
        if stats.get('accounts'):
            for acct, cnt in stats['accounts'].items():
                print(f"  {acct}: {cnt}")
        print(f"Pending actions: {stats['pending_actions']}")
        print(f"Contacts: {stats['total_contacts']}")

    conn.close()


if __name__ == '__main__':
    main()
