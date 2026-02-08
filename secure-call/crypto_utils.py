"""Ribbon — Server-side crypto utilities"""
import hashlib
import hmac
import time
import base64
import secrets
import bcrypt
from config import TURN_SECRET, TURN_HOST, TURN_PORT, TURN_TLS_PORT, TURN_CREDENTIAL_TTL


def hash_passphrase(passphrase):
    """Hash a room passphrase with bcrypt."""
    return bcrypt.hashpw(passphrase.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_passphrase(passphrase, hashed):
    """Verify a passphrase against its bcrypt hash."""
    return bcrypt.checkpw(passphrase.encode('utf-8'), hashed.encode('utf-8'))


def generate_room_id():
    """Generate a short random room ID (e.g., 'abc-def-ghi')."""
    chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
    parts = []
    for _ in range(3):
        part = ''.join(secrets.choice(chars) for _ in range(3))
        parts.append(part)
    return '-'.join(parts)


def generate_invite_token():
    """Generate a URL-safe invite token."""
    return secrets.token_urlsafe(24)


def generate_turn_credentials(username=None):
    """Generate time-limited TURN credentials using the shared secret.

    coturn validates these via the TURN REST API mechanism:
    - username = expiry_timestamp:label
    - credential = HMAC-SHA1(secret, username)
    """
    expiry = int(time.time()) + TURN_CREDENTIAL_TTL
    label = username or secrets.token_hex(8)
    turn_username = f"{expiry}:{label}"
    turn_credential = base64.b64encode(
        hmac.new(
            TURN_SECRET.encode('utf-8'),
            turn_username.encode('utf-8'),
            hashlib.sha1
        ).digest()
    ).decode('utf-8')

    return {
        'username': turn_username,
        'credential': turn_credential,
        'ttl': TURN_CREDENTIAL_TTL,
    }


def get_ice_server_list(username=None):
    """Get full ICE server list including TURN with fresh credentials."""
    creds = generate_turn_credentials(username)

    servers = [
        {'urls': 'stun:stun.l.google.com:19302'},
        {'urls': f'stun:{TURN_HOST}:{TURN_PORT}'},
        {
            'urls': [
                f'turn:{TURN_HOST}:{TURN_PORT}?transport=udp',
                f'turn:{TURN_HOST}:{TURN_PORT}?transport=tcp',
                f'turns:{TURN_HOST}:{TURN_TLS_PORT}?transport=tcp',
            ],
            'username': creds['username'],
            'credential': creds['credential'],
        },
    ]

    return servers


def generate_peer_id():
    """Generate a unique peer ID."""
    return secrets.token_hex(16)
