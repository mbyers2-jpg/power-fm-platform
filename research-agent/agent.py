#!/usr/bin/env python3
"""
Research Agent for Marc Byers
OSINT and research automation — profiles contacts, researches companies,
monitors industry news, and generates intelligence reports.

Usage:
    venv/bin/python agent.py                          # Research all deal counterparties
    venv/bin/python agent.py --person "Name"          # Research a specific person
    venv/bin/python agent.py --company "Name"         # Research a specific company
    venv/bin/python agent.py --deal "Deal Name"       # Research a deal's counterparties
    venv/bin/python agent.py --profile-marc           # Build Marc's OSINT profile
    venv/bin/python agent.py --daemon                 # Continuous research (every 6 hours)
"""

import sys
import os
import signal
import time
import logging
import sqlite3
from datetime import datetime

from database import (
    get_connection, upsert_entity, upsert_person, save_report,
    save_intel, search_entities, search_people
)
from scraper import (
    research_person, research_company, research_deal_counterparty, search_web
)

POLL_INTERVAL = 21600  # 6 hours
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')
DEALS_DB_PATH = os.path.expanduser('~/Agents/deal-tracker/data/deals.db')
EMAIL_DB_PATH = os.path.expanduser('~/Agents/email-agent/data/email_agent.db')

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'research-agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('research-agent')

running = True

def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received.")
    running = False

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def get_deals_db():
    if not os.path.exists(DEALS_DB_PATH):
        return None
    conn = sqlite3.connect(f'file:{DEALS_DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_email_db():
    if not os.path.exists(EMAIL_DB_PATH):
        return None
    conn = sqlite3.connect(f'file:{EMAIL_DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def profile_marc(conn):
    """Build an OSINT profile on Marc Byers from public sources."""
    log.info("Building OSINT profile for Marc Byers...")

    results = research_person('Marc Byers', conn=conn)

    # Additional targeted searches
    music_results = search_web('"Marc Byers" "Protect The Culture" music', conn=conn)
    entertainment_results = search_web('"Marc Byers" entertainment executive', conn=conn)

    report_lines = [
        "# OSINT Profile: Marc Byers",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Web Presence",
        "",
    ]

    for r in results['web_results']:
        report_lines.append(f"- [{r['title']}]({r['url']})")
        if r['snippet']:
            report_lines.append(f"  {r['snippet'][:200]}")
        report_lines.append("")

    if results['social_profiles']:
        report_lines.append("## Social Profiles")
        report_lines.append("")
        for sp in results['social_profiles']:
            report_lines.append(f"- **{sp['platform'].title()}**: [{sp['title']}]({sp['url']})")
        report_lines.append("")

    report_lines.append("## Music/Entertainment Results")
    report_lines.append("")
    for r in music_results[:5]:
        report_lines.append(f"- [{r['title']}]({r['url']})")
        if r['snippet']:
            report_lines.append(f"  {r['snippet'][:200]}")
        report_lines.append("")

    for r in entertainment_results[:5]:
        if r['url'] not in [mr['url'] for mr in music_results[:5]]:
            report_lines.append(f"- [{r['title']}]({r['url']})")
            if r['snippet']:
                report_lines.append(f"  {r['snippet'][:200]}")
            report_lines.append("")

    content = '\n'.join(report_lines)
    report_path = os.path.join(REPORT_DIR, 'marc_byers_osint_profile.md')
    with open(report_path, 'w') as f:
        f.write(content)

    save_report(conn, "Marc Byers OSINT Profile", "osint_profile", "Marc Byers", content, report_path)
    log.info(f"OSINT profile saved: {report_path}")
    return report_path


def research_all_contacts(conn):
    """Research contacts from the email database."""
    email_conn = get_email_db()
    if not email_conn:
        log.warning("Email database not available")
        return 0

    # Get top contacts by email frequency
    contacts = email_conn.execute("""
        SELECT email, name, email_count
        FROM contacts
        WHERE email_count >= 3
        AND email NOT LIKE '%noreply%'
        AND email NOT LIKE '%no-reply%'
        AND email NOT LIKE '%notifications%'
        AND email NOT LIKE '%mailer-daemon%'
        ORDER BY email_count DESC
        LIMIT 25
    """).fetchall()

    email_conn.close()
    researched = 0

    for contact in contacts:
        if not contact['name'] or len(contact['name']) < 3:
            continue

        log.info(f"Researching contact: {contact['name']} ({contact['email']})")
        results = research_person(contact['name'], conn=conn)

        upsert_person(conn, contact['name'],
            email=contact['email'],
            bio=results.get('summary', ''),
            source='email_contacts + web_search',
        )

        for sp in results.get('social_profiles', []):
            if sp['platform'] == 'linkedin':
                upsert_person(conn, contact['name'], linkedin=sp['url'])

        researched += 1
        if not running:
            break

    return researched


def research_all_deals(conn):
    """Research all active deal counterparties."""
    deals_conn = get_deals_db()
    if not deals_conn:
        log.warning("Deals database not available")
        return 0

    deals = deals_conn.execute("""
        SELECT name, counterparty, entity, deal_type
        FROM deals
        WHERE status = 'active'
    """).fetchall()
    deals_conn.close()

    researched = 0
    for deal in deals:
        if deal['counterparty']:
            log.info(f"Researching deal counterparty: {deal['counterparty']} ({deal['name']})")
            results = research_company(deal['counterparty'], conn=conn)
            upsert_entity(conn, deal['counterparty'],
                description=results.get('description', ''),
                website=results.get('website', ''),
                source='deal_counterparty + web_search',
            )
            researched += 1

        if not running:
            break

    return researched


def generate_intelligence_report(conn):
    """Generate a comprehensive intelligence report."""
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'intel_report_{today}.md')

    entities = conn.execute("SELECT * FROM entities ORDER BY last_updated DESC LIMIT 20").fetchall()
    people = conn.execute("SELECT * FROM people ORDER BY last_updated DESC LIMIT 20").fetchall()
    reports = conn.execute("SELECT * FROM research_reports ORDER BY created_at DESC LIMIT 10").fetchall()
    intel = conn.execute("SELECT * FROM industry_intel ORDER BY collected_at DESC LIMIT 20").fetchall()

    lines = [
        f"# Intelligence Report — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Database Summary",
        "",
        f"- Entities tracked: {conn.execute('SELECT COUNT(*) FROM entities').fetchone()[0]}",
        f"- People profiled: {conn.execute('SELECT COUNT(*) FROM people').fetchone()[0]}",
        f"- Research reports: {conn.execute('SELECT COUNT(*) FROM research_reports').fetchone()[0]}",
        f"- Intel items: {conn.execute('SELECT COUNT(*) FROM industry_intel').fetchone()[0]}",
        "",
    ]

    if people:
        lines.append("## Profiled Contacts")
        lines.append("")
        for p in people:
            lines.append(f"**{p['name']}**")
            details = []
            if p['title']:
                details.append(f"Title: {p['title']}")
            if p['organization']:
                details.append(f"Org: {p['organization']}")
            if p['email']:
                details.append(f"Email: {p['email']}")
            if p['linkedin']:
                details.append(f"LinkedIn: {p['linkedin']}")
            if p['bio']:
                details.append(f"Bio: {p['bio'][:150]}")
            for d in details:
                lines.append(f"  - {d}")
            lines.append("")

    if entities:
        lines.append("## Tracked Entities")
        lines.append("")
        for e in entities:
            lines.append(f"**{e['name']}**")
            if e['website']:
                lines.append(f"  - Website: {e['website']}")
            if e['industry']:
                lines.append(f"  - Industry: {e['industry']}")
            if e['description']:
                lines.append(f"  - {e['description'][:200]}")
            lines.append("")

    if intel:
        lines.append("## Recent Intelligence")
        lines.append("")
        for i in intel:
            lines.append(f"- **[{i['category'] or 'General'}]** {i['topic']}")
            if i['summary']:
                lines.append(f"  {i['summary'][:200]}")
            if i['source_url']:
                lines.append(f"  Source: {i['source_url']}")
            lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    save_report(conn, f"Intelligence Report {today}", "intel_report", "all", content, report_path)
    log.info(f"Intelligence report: {report_path}")
    return report_path


def run_once(conn):
    """Full research cycle."""
    profile_marc(conn)
    research_all_contacts(conn)
    research_all_deals(conn)
    report = generate_intelligence_report(conn)
    return report


def run_daemon(conn):
    """Continuous research loop."""
    log.info(f"Research agent starting in daemon mode (cycle every {POLL_INTERVAL}s)")

    run_once(conn)

    while running:
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        run_once(conn)

    log.info("Research agent stopped.")


def main():
    daemon_mode = '--daemon' in sys.argv
    person = None
    company = None
    deal = None
    marc_profile = '--profile-marc' in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == '--person' and i + 1 < len(sys.argv):
            person = sys.argv[i + 1]
        elif arg == '--company' and i + 1 < len(sys.argv):
            company = sys.argv[i + 1]
        elif arg == '--deal' and i + 1 < len(sys.argv):
            deal = sys.argv[i + 1]

    log.info("Initializing research agent...")
    conn = get_connection()

    if person:
        results = research_person(person, conn=conn)
        upsert_person(conn, person, bio=results.get('summary', ''), source='manual_search')
        print(f"\nResearch on: {person}")
        print(f"Web results: {len(results['web_results'])}")
        for r in results['web_results']:
            print(f"  - {r['title']}: {r['snippet'][:100]}")

    elif company:
        results = research_company(company, conn=conn)
        upsert_entity(conn, company, description=results.get('description', ''),
                      website=results.get('website', ''), source='manual_search')
        print(f"\nResearch on: {company}")
        print(f"Website: {results.get('website', 'Not found')}")
        for r in results['web_results']:
            print(f"  - {r['title']}: {r['snippet'][:100]}")

    elif deal:
        results = research_deal_counterparty(deal, conn=conn)
        print(f"\nDeal research: {deal}")
        for r in results.get('news', []):
            print(f"  - {r['title']}: {r['snippet'][:100]}")

    elif marc_profile:
        report = profile_marc(conn)
        print(f"OSINT profile saved: {report}")

    elif daemon_mode:
        run_daemon(conn)

    else:
        report = run_once(conn)
        print(f"\nIntelligence report: {report}")

    conn.close()


if __name__ == '__main__':
    main()
