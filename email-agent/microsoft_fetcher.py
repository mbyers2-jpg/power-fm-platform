"""
Microsoft Graph API Email Fetcher
Fetches emails from Microsoft 365 and normalizes them to match the email-agent schema.
"""

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests

log = logging.getLogger('email-agent')

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'


def fetch_recent_messages(token, since=None, max_results=50):
    """
    Fetch recent messages from Microsoft Graph API.
    Returns a list of raw Graph API message dicts.
    """
    headers = {'Authorization': f'Bearer {token}'}
    params = {
        '$top': max_results,
        '$orderby': 'receivedDateTime desc',
        '$select': (
            'id,conversationId,subject,from,toRecipients,receivedDateTime,'
            'bodyPreview,isRead,hasAttachments,categories,importance'
        ),
    }

    if since:
        params['$filter'] = f"receivedDateTime ge {since}"

    messages = []
    url = f'{GRAPH_BASE}/me/messages'

    try:
        while url and len(messages) < max_results:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 401:
                log.error("Microsoft Graph token expired or invalid")
                return []
            resp.raise_for_status()

            data = resp.json()
            messages.extend(data.get('value', []))

            # Follow pagination if needed
            url = data.get('@odata.nextLink')
            params = None  # nextLink includes params already

        return messages[:max_results]

    except requests.RequestException as e:
        log.error(f"Failed to fetch Microsoft emails: {e}")
        return []


def normalize_message(msg, account_email='m.byers@2035ventures.co'):
    """
    Convert a Microsoft Graph message dict to our email-agent schema.
    Matches the format used by Gmail process_message().
    """
    msg_id = f"ms_{msg['id']}"

    # Extract sender
    from_field = msg.get('from', {}).get('emailAddress', {})
    sender_name = from_field.get('name', '')
    sender_email = from_field.get('address', '').lower()

    # Extract recipients
    to_list = msg.get('toRecipients', [])
    recipients = ', '.join(
        r.get('emailAddress', {}).get('address', '')
        for r in to_list
    )

    # Parse date
    date_str = msg.get('receivedDateTime', '')

    # Categories → labels string (for compatibility)
    categories = msg.get('categories', [])
    labels = ','.join(categories) if categories else ''

    # Map Microsoft importance → our importance levels
    ms_importance = msg.get('importance', 'normal')
    importance_map = {'high': 'high', 'normal': 'normal', 'low': 'low'}

    return {
        'id': msg_id,
        'thread_id': msg.get('conversationId', ''),
        'subject': msg.get('subject', '(no subject)'),
        'sender': sender_name or sender_email,
        'sender_email': sender_email,
        'recipients': recipients,
        'date': date_str,
        'snippet': msg.get('bodyPreview', '')[:300],
        'labels': labels,
        'is_read': int(msg.get('isRead', False)),
        'has_attachment': int(msg.get('hasAttachments', False)),
        'source': 'microsoft365',
        'account_email': account_email,
        # category and importance will be set by categorize_email / assess_importance
        '_ms_importance': importance_map.get(ms_importance, 'normal'),
    }
