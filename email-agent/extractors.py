"""
Structured data extractors for the five communications pillars.
Extracts actionable info from email metadata (subject + snippet + sender).
"""

import re
import email.utils
from datetime import datetime


# --- Sender-based identification (transactional senders only, not marketing) ---

SHOPPING_SENDERS = {
    'auto-confirm@amazon.com': 'Amazon',
    'ship-confirm@amazon.com': 'Amazon',
    'shipment-tracking@amazon.com': 'Amazon',
    'order-update@amazon.com': 'Amazon',
    'digital-no-reply@amazon.com': 'Amazon',
    'invoice+statements@mail.anthropic.com': 'Anthropic',
    'customerservice@socalgas.com': 'SoCalGas',
    'noreply@doordash.com': 'DoorDash',
    'no-reply@instacart.com': 'Instacart',
}

MAPPING_SENDERS = {
    'noreply@uber.com': 'Uber',
    'receipts@uber.com': 'Uber',
    'noreply@lyft.com': 'Lyft',
    'ride-receipts@lyft.com': 'Lyft',
}

TRAVEL_SENDERS = {
    'loyalty@loyalty.ms.aa.com': 'American Airlines',
    'unitedairlines@enews.united.com': 'United',
    'deltacommunications@t.delta.com': 'Delta',
    'JetBlueAirways@email.jetblue.com': 'JetBlue',
    'donotreply@spirit.com': 'Spirit',
    'noreply@m.airbnb.com': 'Airbnb',
    'no-reply@hotels.com': 'Hotels.com',
    'noreply@hilton.com': 'Hilton',
    'reservations@marriott.com': 'Marriott',
}

MEDICAL_SENDERS = {
    'mychart@': 'MyChart',
    'noreply@myquest.questdiagnostics.com': 'Quest',
    'noreply@messaging.cvs.com': 'CVS Pharmacy',
    'walgreens@e.walgreens.com': 'Walgreens',
    'no-reply@solvhealth.com': 'Solv Health',
}


def _match_sender(sender_email, sender_map):
    """Check if sender matches known transactional senders. Returns merchant name or None."""
    sender_email = sender_email.lower()
    for pattern, name in sender_map.items():
        if pattern.endswith('@'):
            # Prefix match (e.g. 'mychart@' matches 'mychart@anything.com')
            if sender_email.startswith(pattern):
                return name
        elif sender_email == pattern:
            return name
    return None


def _parse_email_date(date_str):
    """Parse RFC 2822 email date to ISO format. Returns '' on failure."""
    if not date_str:
        return ''
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.strftime('%Y-%m-%d')
    except Exception:
        return date_str[:10] if date_str else ''


def _extract_amount(text):
    """Extract the first dollar amount from text."""
    # Match patterns like $46.98, $1,234.56
    m = re.search(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2}))', text)
    if m:
        return float(m.group(1).replace(',', ''))
    return None


def _extract_date_from_snippet(text):
    """Try to extract a date from snippet text. Returns ISO date string or ''."""
    # Pattern: "Feb 5, 2026" or "February 5, 2026"
    m = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})', text)
    if m:
        try:
            month_str = m.group(1)[:3]
            day = int(m.group(2))
            year = int(m.group(3))
            month_num = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                         'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}[month_str]
            return f"{year}-{month_num:02d}-{day:02d}"
        except (ValueError, KeyError):
            pass

    # Pattern: "2026-02-05" ISO
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)

    return ''


def _extract_order_number(text):
    """Extract order/receipt number from text."""
    # Anthropic receipt: #2849-4574-7646
    m = re.search(r'#(\d{4}-\d{4}-\d{4})', text)
    if m:
        return m.group(1)

    # Amazon order: 123-4567890-1234567
    m = re.search(r'(\d{3}-\d{7}-\d{7})', text)
    if m:
        return m.group(1)

    # Generic: #12345 or Order #12345
    m = re.search(r'(?:order|#)\s*#?(\d{4,})', text, re.IGNORECASE)
    if m:
        return m.group(1)

    return ''


# --- Per-pillar extractors ---

def extract_shopping(email_data):
    """Extract shopping/order data from an email."""
    sender = email_data.get('sender_email', '')
    subject = email_data.get('subject', '')
    snippet = email_data.get('snippet', '')
    text = f"{subject} {snippet}"

    # Always require a transactional signal — known sender alone isn't enough
    transactional_patterns = [
        r'(?:your\s+)?order\s+(?:confirm|#|id)',
        r'(?:your\s+)?(?:receipt|invoice)\s+(?:from|#)',
        r'ordered:',
        r'shipped:',
        r'delivered:',
        r'your\s+(?:automatic\s+)?(?:monthly\s+)?payment\s+is',
        r'your\s+receipt\s+from',
        r'receipt\s+from\s+\w+',
    ]
    if not any(re.search(p, text, re.IGNORECASE) for p in transactional_patterns):
        return None

    merchant = _match_sender(sender, SHOPPING_SENDERS)
    if not merchant:
        # Try to identify merchant from sender domain or subject
        m = re.search(r'receipt\s+from\s+(\w[\w\s]*?)[\.,#]', text, re.IGNORECASE)
        if m:
            merchant = m.group(1).strip()
        else:
            domain = sender.split('@')[-1] if '@' in sender else ''
            merchant = domain.split('.')[0].title() if domain else 'Unknown'

    # Determine status from keywords
    status = 'ordered'
    text_lower = text.lower()
    if 'delivered' in text_lower:
        status = 'delivered'
    elif 'shipped' in text_lower or 'tracking' in text_lower:
        status = 'shipped'
    elif 'receipt' in text_lower or 'payment' in text_lower:
        status = 'paid'

    # Extract description from subject
    description = subject
    # For Amazon "Ordered: ..." emails, clean up
    m = re.search(r'Ordered:\s*["\u201c]?(.+?)["\u201d]?\.{0,3}$', subject)
    if m:
        description = m.group(1)

    return {
        'email_id': email_data['id'],
        'merchant': merchant,
        'order_number': _extract_order_number(text),
        'tracking_number': '',
        'amount': _extract_amount(text),
        'status': status,
        'order_date': _extract_date_from_snippet(snippet) or _parse_email_date(email_data.get('date', '')),
        'delivery_date': '',
        'description': description,
    }


def extract_travel(email_data):
    """Extract travel data from an email."""
    sender = email_data.get('sender_email', '')
    subject = email_data.get('subject', '')
    snippet = email_data.get('snippet', '')
    text = f"{subject} {snippet}"

    # Require a transactional signal — marketing from airlines doesn't count
    transactional = [
        r'(?:booking|reservation)\s+confirm',
        r'(?:boarding\s+pass|e-?ticket)',
        r'itinerary\s+(?:for|confirm|receipt)',
        r'check-?in\s+(?:is\s+)?(?:now\s+)?(?:open|available)',
        r'your\s+(?:flight|trip|booking)',
        r'confirmation\s+(?:code|number|#)',
    ]
    if not any(re.search(p, text, re.IGNORECASE) for p in transactional):
        return None

    carrier = _match_sender(sender, TRAVEL_SENDERS)
    if not carrier:
        domain = sender.split('@')[-1] if '@' in sender else ''
        carrier = domain.split('.')[0].title() if domain else 'Unknown'

    # Determine item type
    text_lower = text.lower()
    if any(kw in text_lower for kw in ['flight', 'boarding', 'airline', 'airport']):
        item_type = 'flight'
    elif any(kw in text_lower for kw in ['hotel', 'resort', 'check-in', 'room']):
        item_type = 'hotel'
    elif any(kw in text_lower for kw in ['rental car', 'car rental']):
        item_type = 'car'
    else:
        item_type = 'travel'

    # Flight number: UA1234, AA123, DL4567, etc.
    flight_m = re.search(r'\b([A-Z]{2}\d{1,4})\b', text)
    flight_number = flight_m.group(1) if flight_m else ''

    # Route: LAX to JFK, from LAX to JFK
    route_m = re.search(r'(?:from\s+)?([A-Z]{3})\s+(?:to|→|-)\s+([A-Z]{3})', text)
    departure = route_m.group(1) if route_m else ''
    arrival = route_m.group(2) if route_m else ''

    # Confirmation code: 6-char alphanumeric
    conf_m = re.search(r'(?:confirm|conf|PNR|record\s+locator)[:\s#]*([A-Z0-9]{5,8})', text, re.IGNORECASE)
    confirmation = conf_m.group(1) if conf_m else ''

    return {
        'email_id': email_data['id'],
        'item_type': item_type,
        'carrier': carrier,
        'confirmation_code': confirmation,
        'departure_location': departure,
        'arrival_location': arrival,
        'start_date': _extract_date_from_snippet(snippet),
        'end_date': '',
        'flight_number': flight_number,
        'amount': _extract_amount(text),
        'description': subject,
    }


def extract_medical(email_data):
    """Extract medical data from an email."""
    sender = email_data.get('sender_email', '')
    subject = email_data.get('subject', '')
    snippet = email_data.get('snippet', '')
    text = f"{subject} {snippet}"

    # Always require transactional signal — wellness marketing doesn't count
    transactional = [
        r'(?:appointment|visit)\s+(?:confirm|remind|schedul)',
        r'(?:prescription|rx)\s+(?:ready|filled|refill)',
        r'(?:lab|test)\s+results?\s+(?:are\s+)?(?:ready|available)',
        r'(?:your\s+)?(?:claim|eob|explanation\s+of\s+benefits)',
        r'message\s+from\s+(?:your\s+)?(?:doctor|provider|care\s+team)',
    ]
    if not any(re.search(p, text, re.IGNORECASE) for p in transactional):
        return None

    provider = _match_sender(sender, MEDICAL_SENDERS)
    if not provider:
        domain = sender.split('@')[-1] if '@' in sender else ''
        provider = domain.split('.')[0].title() if domain else 'Unknown'

    # Determine item type
    text_lower = text.lower()
    if any(kw in text_lower for kw in ['appointment', 'visit', 'schedul']):
        item_type = 'appointment'
    elif any(kw in text_lower for kw in ['prescription', 'refill', 'rx', 'pharmacy']):
        item_type = 'prescription'
    elif any(kw in text_lower for kw in ['lab result', 'test result']):
        item_type = 'lab_result'
    elif any(kw in text_lower for kw in ['claim', 'insurance', 'eob', 'copay']):
        item_type = 'insurance'
    else:
        item_type = 'medical'

    # Doctor name: "Dr. Smith" or "Dr Smith"
    dr_m = re.search(r'Dr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', text)
    if dr_m:
        provider = f"Dr. {dr_m.group(1)}" if not provider else provider

    return {
        'email_id': email_data['id'],
        'item_type': item_type,
        'provider': provider,
        'appointment_date': _extract_date_from_snippet(snippet),
        'description': subject,
        'location': '',
    }


def extract_mapping(email_data):
    """Extract ride/mapping data from an email."""
    sender = email_data.get('sender_email', '')
    subject = email_data.get('subject', '')
    snippet = email_data.get('snippet', '')
    text = f"{subject} {snippet}"

    service = _match_sender(sender, MAPPING_SENDERS)
    if not service:
        return None

    # Only extract from actual ride receipts, not marketing
    text_lower = text.lower()
    is_ride_receipt = any(kw in text_lower for kw in [
        'trip with uber', 'trip with lyft', 'your ride', 'thanks for riding',
        'trip receipt', 'ride receipt', 'charge summary',
    ])
    if not is_ride_receipt:
        return None

    # Determine item type
    if any(kw in text_lower for kw in ['trip', 'ride', 'riding']):
        item_type = 'ride'
    elif 'delivery' in text_lower or 'eats' in text_lower:
        item_type = 'delivery'
    else:
        item_type = 'ride'

    ride_date = _extract_date_from_snippet(snippet) or _parse_email_date(email_data.get('date', ''))

    return {
        'email_id': email_data['id'],
        'item_type': item_type,
        'service': service,
        'origin': '',
        'destination': '',
        'ride_date': ride_date,
        'amount': _extract_amount(text),
        'description': subject,
    }


# --- Main entry point ---

def extract_structured_data(email_data):
    """
    Dispatch to the appropriate extractor based on email category.
    Returns (pillar_name, extracted_data) or (None, None) if nothing extracted.
    """
    category = email_data.get('category', '')

    if category == 'shopping':
        data = extract_shopping(email_data)
        return ('shopping', data) if data else (None, None)

    elif category == 'travel':
        data = extract_travel(email_data)
        return ('travel', data) if data else (None, None)

    elif category == 'medical':
        data = extract_medical(email_data)
        return ('medical', data) if data else (None, None)

    elif category == 'mapping':
        data = extract_mapping(email_data)
        return ('mapping', data) if data else (None, None)

    # comms handled by comms-agent, general has no extraction
    return (None, None)
