"""
Email thread analyzer — summarizes threads, detects unanswered emails,
identifies follow-up needs, and generates draft responses.
"""

import re
import email.utils
from datetime import datetime, timedelta
from database import get_email_db, get_deals_db


# Patterns that suggest an email needs a response
NEEDS_RESPONSE_PATTERNS = [
    r'\?',  # Questions
    r'(?:please|kindly)\s+(?:reply|respond|confirm|let\s+(?:me|us)\s+know)',
    r'(?:waiting|await)(?:ing)?\s+(?:your|for\s+your)',
    r'(?:get\s+back|hear\s+from\s+you)',
    r'(?:at\s+your\s+earliest|asap|urgently)',
    r'(?:can\s+you|could\s+you|would\s+you)',
    r'(?:thoughts|feedback|input|review)\?',
    r'(?:schedule|book|set\s+up)\s+(?:a\s+)?(?:call|meeting|time)',
]

# Patterns that suggest a follow-up is needed
FOLLOW_UP_PATTERNS = [
    r'(?:follow\s*up|check\s*in|circle\s*back|touch\s*base)',
    r'(?:next\s+steps|moving\s+forward|go\s+from\s+here)',
    r'(?:deadline|due\s+(?:date|by)|expires?)',
    r'(?:contract|agreement|sign)',
]

# Marc's known email
MARC_EMAIL = 'm.byers2@gmail.com'


def get_unanswered_emails(limit=50):
    """Find emails that likely need a response from Marc."""
    email_conn = get_email_db()
    if not email_conn:
        return []

    # Get recent unread or important emails not from Marc — comms only
    emails = email_conn.execute("""
        SELECT id, thread_id, subject, sender, sender_email, snippet, date, labels, category
        FROM emails
        WHERE sender_email != ?
        AND (is_read = 0 OR importance IN ('critical', 'high'))
        AND category = 'comms'
        ORDER BY
            CASE importance WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
            first_seen DESC
        LIMIT ?
    """, (MARC_EMAIL, limit)).fetchall()

    unanswered = []
    for em in emails:
        text = f"{em['subject']} {em['snippet']}".lower()
        needs_response = any(re.search(p, text, re.IGNORECASE) for p in NEEDS_RESPONSE_PATTERNS)
        if needs_response:
            unanswered.append(dict(em))

    email_conn.close()
    return unanswered


def get_stale_threads(days=7):
    """Find threads where Marc was the last to receive (no reply sent in X days)."""
    email_conn = get_email_db()
    if not email_conn:
        return []

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    stale = email_conn.execute("""
        SELECT thread_id, subject, sender, sender_email,
               MAX(date) as last_date, COUNT(*) as msg_count
        FROM emails
        WHERE sender_email != ?
        AND category = 'comms'
        AND first_seen < ?
        GROUP BY thread_id
        HAVING msg_count >= 1
        ORDER BY last_date DESC
        LIMIT 30
    """, (MARC_EMAIL, cutoff)).fetchall()

    email_conn.close()
    return [dict(s) for s in stale]


def get_thread_messages(thread_id):
    """Get all messages in a thread."""
    email_conn = get_email_db()
    if not email_conn:
        return []

    messages = email_conn.execute("""
        SELECT * FROM emails
        WHERE thread_id = ?
        ORDER BY date ASC
    """, (thread_id,)).fetchall()

    email_conn.close()
    return [dict(m) for m in messages]


def summarize_thread(thread_id):
    """Generate a summary of an email thread."""
    messages = get_thread_messages(thread_id)
    if not messages:
        return None

    participants = set()
    subjects = set()
    snippets = []

    for msg in messages:
        if msg['sender_email']:
            participants.add(msg['sender_email'])
        if msg['subject']:
            subjects.add(msg['subject'])
        if msg['snippet']:
            snippets.append(f"[{msg['sender']}]: {msg['snippet']}")

    subject = messages[0]['subject'] or '(no subject)'
    participant_str = ', '.join(sorted(participants))

    # Build summary from snippets
    summary_parts = []
    for s in snippets[-5:]:  # Last 5 messages
        summary_parts.append(s[:200])

    summary = '\n'.join(summary_parts)

    # Detect action items from the thread
    action_items = []
    for msg in messages[-3:]:
        text = f"{msg['subject']} {msg['snippet']}".lower()
        if any(re.search(p, text) for p in FOLLOW_UP_PATTERNS):
            action_items.append(f"Follow-up needed: {msg['snippet'][:100]}")
        if any(re.search(p, text) for p in NEEDS_RESPONSE_PATTERNS):
            action_items.append(f"Response needed: {msg['snippet'][:100]}")

    return {
        'thread_id': thread_id,
        'subject': subject,
        'participants': participant_str,
        'message_count': len(messages),
        'summary': summary,
        'action_items': '\n'.join(action_items),
    }


def detect_follow_ups_needed():
    """Analyze emails to detect where follow-ups are needed."""
    follow_ups = []

    # 1. Unanswered emails needing response
    unanswered = get_unanswered_emails()
    for em in unanswered:
        follow_ups.append({
            'email_id': em['id'],
            'thread_id': em['thread_id'],
            'contact_email': em['sender_email'],
            'contact_name': em['sender'],
            'subject': em['subject'],
            'reason': 'Unanswered — appears to need a response',
            'priority': 'high' if em['category'] in ('urgent', 'deals') else 'medium',
        })

    # 2. Stale threads (no activity in 7+ days)
    stale = get_stale_threads(days=7)
    for s in stale:
        follow_ups.append({
            'thread_id': s['thread_id'],
            'contact_email': s['sender_email'],
            'contact_name': s['sender'],
            'subject': s['subject'],
            'reason': f"Stale thread — no activity since {s['last_date'][:10] if s['last_date'] else 'unknown'}",
            'priority': 'medium',
        })

    # 3. Cross-reference with deals for deal-related follow-ups
    deals_conn = get_deals_db()
    if deals_conn:
        stale_deals = deals_conn.execute("""
            SELECT d.name, dc.contact_email, dc.contact_name
            FROM deals d
            JOIN deal_contacts dc ON d.id = dc.deal_id
            WHERE d.status = 'active'
            AND (d.last_activity IS NULL OR d.last_activity < datetime('now', '-14 days'))
        """).fetchall()

        for sd in stale_deals:
            follow_ups.append({
                'contact_email': sd['contact_email'],
                'contact_name': sd['contact_name'] or sd['contact_email'],
                'subject': f"Re: {sd['name']}",
                'reason': f"Deal '{sd['name']}' has gone stale — no activity in 14+ days",
                'priority': 'high',
            })
        deals_conn.close()

    return follow_ups


def generate_draft_response(email_data):
    """Generate a draft response template based on email context."""
    subject = email_data.get('subject', '')
    sender = email_data.get('sender', email_data.get('contact_name', ''))
    snippet = email_data.get('snippet', '')
    category = email_data.get('category', '')
    first_name = sender.split()[0] if sender else 'there'

    # Determine response type
    text = f"{subject} {snippet}".lower()

    if any(kw in text for kw in ['meeting', 'call', 'zoom', 'schedule']):
        body = f"""Hi {first_name},

Thanks for reaching out. I'd be happy to connect.

[OPTION A] I'm available [DAY/TIME] — does that work for you?
[OPTION B] Here's my availability this week: [TIMES]
[OPTION C] Let me check my schedule and get back to you shortly.

Best,
Marc"""

    elif any(kw in text for kw in ['sign', 'signature', 'docusign', 'agreement', 'contract']):
        body = f"""Hi {first_name},

Thanks for sending this over. I've reviewed the document and [CHOOSE ONE]:
[OPTION A] everything looks good — I'll sign and return shortly.
[OPTION B] I have a few questions/edits before signing: [DETAILS]
[OPTION C] I need to have my counsel review before signing. I'll circle back by [DATE].

Best,
Marc"""

    elif any(kw in text for kw in ['invoice', 'payment', 'amount due']):
        body = f"""Hi {first_name},

Received — thanks for sending. [CHOOSE ONE]:
[OPTION A] Payment will be processed by [DATE].
[OPTION B] I need the following details before processing: [DETAILS]
[OPTION C] Can you resend with updated [AMOUNT/DETAILS]?

Best,
Marc"""

    elif any(kw in text for kw in ['proposal', 'pitch', 'deck', 'opportunity']):
        body = f"""Hi {first_name},

Thanks for sharing this. I've taken a look and [CHOOSE ONE]:
[OPTION A] I'm interested — let's set up a call to discuss further.
[OPTION B] I'd like to learn more about [SPECIFIC ASPECT]. Can you share additional details?
[OPTION C] The timing isn't right for us at this moment, but I'd like to revisit in [TIMEFRAME].

Best,
Marc"""

    else:
        body = f"""Hi {first_name},

Thanks for your email regarding {subject or '[TOPIC]'}.

[YOUR RESPONSE HERE]

Best,
Marc"""

    return {
        'to': email_data.get('sender_email', email_data.get('contact_email', '')),
        'subject': f"Re: {subject}" if not subject.startswith('Re:') else subject,
        'body': body,
        'draft_type': 'reply',
    }
