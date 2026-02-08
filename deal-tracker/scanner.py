"""
Deal Scanner — discovers deals from the local file system and email database.
Scans Documents/Projects, Documents/Artists, and Documents/Business for deal artifacts.
Cross-references with email agent data to detect activity and contacts.
"""

import os
import re
from datetime import datetime
from database import (
    get_connection, get_email_db, upsert_deal, link_document,
    link_contact, get_active_deals
)

HOME = os.path.expanduser('~')
PROJECTS_DIR = os.path.join(HOME, 'Documents', 'Projects')
ARTISTS_DIR = os.path.join(HOME, 'Documents', 'Artists')
BUSINESS_DIR = os.path.join(HOME, 'Documents', 'Business')

# File extensions that indicate deal documents
DEAL_EXTENSIONS = {
    '.pdf', '.docx', '.doc', '.pages', '.xlsx', '.xlsm',
    '.pptx', '.key', '.html',
}

# Keywords that indicate deal stage
STAGE_KEYWORDS = {
    'prospect': ['proposal', 'pitch', 'deck', 'overview', 'brief', 'one-sheet', 'teaser'],
    'negotiation': ['nda', 'term sheet', 'redline', 'markup', 'draft', 'comments', 'edits'],
    'contract': ['agreement', 'contract', 'msa', 'sow', 'execution', 'final'],
    'signed': ['signed', 'docusign', 'signature', 'executed', 'complete'],
    'active': ['invoice', 'payment', 'deliverable', 'milestone'],
}

# Deal type detection
TYPE_KEYWORDS = {
    'partnership': ['partnership', 'partner', 'collaboration', 'joint'],
    'artist_deal': ['artist', 'producer', 'recording', 'publishing', 'master'],
    'brand_deal': ['brand', 'sponsor', 'ambassador', 'endorsement', 'influencer'],
    'investment': ['investment', 'investor', 'fund', 'capital', 'equity', 'memorandum'],
    'licensing': ['license', 'licensing', 'rights', 'sync', 'clearance'],
    'nda': ['nda', 'non-disclosure', 'confidential'],
    'services': ['services', 'consulting', 'advisory', 'retainer'],
}


def detect_deal_type(filenames):
    """Detect deal type from file names."""
    text = ' '.join(filenames).lower()
    for dtype, keywords in TYPE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return dtype
    return 'general'


def detect_stage(filenames):
    """Detect the most advanced deal stage from file names."""
    text = ' '.join(filenames).lower()
    # Check from most advanced to least
    for stage in ['signed', 'active', 'contract', 'negotiation', 'prospect']:
        if any(kw in text for kw in STAGE_KEYWORDS[stage]):
            return stage
    return 'prospect'


def get_latest_modified(folder_path):
    """Get the most recent file modification time in a folder."""
    latest = None
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            if f.startswith('.'):
                continue
            fp = os.path.join(root, f)
            mtime = os.path.getmtime(fp)
            if latest is None or mtime > latest:
                latest = mtime
    if latest:
        return datetime.fromtimestamp(latest).isoformat()
    return None


def scan_folder_for_documents(folder_path):
    """Scan a folder and return all deal-relevant documents."""
    docs = []
    if not os.path.isdir(folder_path):
        return docs
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            if f.startswith('.'):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in DEAL_EXTENSIONS:
                docs.append({
                    'path': os.path.join(root, f),
                    'name': f,
                    'ext': ext,
                    'size': os.path.getsize(os.path.join(root, f)),
                    'modified': datetime.fromtimestamp(
                        os.path.getmtime(os.path.join(root, f))
                    ).isoformat()
                })
    return docs


def folder_name_to_deal_name(folder_name):
    """Convert a kebab-case folder name to a readable deal name."""
    return folder_name.replace('-', ' ').title()


def scan_projects(conn):
    """Scan Documents/Projects for deals."""
    if not os.path.isdir(PROJECTS_DIR):
        return 0

    count = 0
    for folder in os.listdir(PROJECTS_DIR):
        folder_path = os.path.join(PROJECTS_DIR, folder)
        if not os.path.isdir(folder_path) or folder.startswith('.'):
            continue

        deal_name = folder_name_to_deal_name(folder)
        docs = scan_folder_for_documents(folder_path)
        filenames = [d['name'] for d in docs]

        deal_type = detect_deal_type(filenames)
        stage = detect_stage(filenames)
        last_activity = get_latest_modified(folder_path)

        deal_id = upsert_deal(conn, deal_name,
            entity='Protect The Culture',
            deal_type=deal_type,
            stage=stage,
            last_activity=last_activity,
            folder_path=folder_path,
        )

        for doc in docs:
            link_document(conn, deal_id, doc['path'],
                file_type=doc['ext'],
                description=doc['name']
            )

        count += 1

    return count


def scan_artists(conn):
    """Scan Documents/Artists for artist deals."""
    if not os.path.isdir(ARTISTS_DIR):
        return 0

    count = 0
    for folder in os.listdir(ARTISTS_DIR):
        folder_path = os.path.join(ARTISTS_DIR, folder)
        if not os.path.isdir(folder_path) or folder.startswith('.'):
            continue

        artist_name = folder_name_to_deal_name(folder)
        deal_name = f"{artist_name} — Artist Deal"
        docs = scan_folder_for_documents(folder_path)
        filenames = [d['name'] for d in docs]

        stage = detect_stage(filenames)
        last_activity = get_latest_modified(folder_path)

        deal_id = upsert_deal(conn, deal_name,
            entity='Protect The Culture',
            deal_type='artist_deal',
            stage=stage,
            last_activity=last_activity,
            folder_path=folder_path,
        )

        for doc in docs:
            link_document(conn, deal_id, doc['path'],
                file_type=doc['ext'],
                description=doc['name']
            )

        count += 1

    return count


def scan_business_contracts(conn):
    """Scan Business/Contracts-Agreements for standalone deals."""
    contracts_dir = os.path.join(BUSINESS_DIR, 'Contracts-Agreements')
    if not os.path.isdir(contracts_dir):
        return 0

    count = 0
    for f in os.listdir(contracts_dir):
        if f.startswith('.'):
            continue
        fp = os.path.join(contracts_dir, f)
        if not os.path.isfile(fp):
            continue

        name_base = os.path.splitext(f)[0]
        deal_name = f"Contract: {name_base}"

        last_activity = datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()

        deal_id = upsert_deal(conn, deal_name,
            deal_type='contract',
            stage='contract',
            last_activity=last_activity,
            folder_path=contracts_dir,
        )

        link_document(conn, deal_id, fp,
            file_type=os.path.splitext(f)[1],
            description=f
        )
        count += 1

    return count


def cross_reference_emails(conn):
    """Cross-reference deals with email agent data to find related communications."""
    email_conn = get_email_db()
    if not email_conn:
        return 0

    deals = get_active_deals(conn)
    updates = 0

    for deal in deals:
        deal_name = deal['name'].lower()
        # Build search terms from deal name
        search_terms = [w for w in deal_name.split() if len(w) > 3]

        if not search_terms:
            continue

        # Search emails for matching subjects
        for term in search_terms[:3]:
            pattern = f'%{term}%'
            related_emails = email_conn.execute("""
                SELECT DISTINCT sender_email, sender, MAX(date) as last_date
                FROM emails
                WHERE subject LIKE ? OR snippet LIKE ?
                GROUP BY sender_email
                ORDER BY last_date DESC
                LIMIT 5
            """, (pattern, pattern)).fetchall()

            for em in related_emails:
                if em['sender_email'] and '@' in em['sender_email']:
                    link_contact(conn, deal['id'],
                        em['sender'] or em['sender_email'],
                        em['sender_email'],
                        role='email_contact'
                    )
                    updates += 1

        # Check for recent email activity related to this deal
        if deal['counterparty_email']:
            recent = email_conn.execute("""
                SELECT MAX(date) as latest
                FROM emails
                WHERE sender_email = ?
            """, (deal['counterparty_email'],)).fetchone()

            if recent and recent['latest']:
                conn.execute("""
                    UPDATE deals SET last_activity = MAX(COALESCE(last_activity, ''), ?)
                    WHERE id = ?
                """, (recent['latest'], deal['id']))

    conn.commit()
    email_conn.close()
    return updates


def run_full_scan(conn):
    """Run a complete scan of all deal sources."""
    results = {
        'projects': scan_projects(conn),
        'artists': scan_artists(conn),
        'contracts': scan_business_contracts(conn),
        'email_refs': cross_reference_emails(conn),
    }

    conn.execute("""
        INSERT INTO scan_log (scan_type, results_summary)
        VALUES ('full', ?)
    """, (str(results),))
    conn.commit()

    return results
