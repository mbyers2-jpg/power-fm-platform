"""Ribbon — Configuration"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
DB_PATH = os.path.join(DATA_DIR, 'secure_call.db')
SFU_SOCKET = os.path.join(BASE_DIR, 'sfu', 'mediasoup.sock')

# Flask
PORT = 5558
HOST = '0.0.0.0'
SECRET_KEY = os.environ.get('RIBBON_SECRET', secrets.token_hex(32))
MAX_UPLOAD_MB = 100
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

# mediasoup SFU
SFU_LISTEN_IP = '0.0.0.0'
SFU_ANNOUNCED_IP = os.environ.get('RIBBON_PUBLIC_IP', '127.0.0.1')
SFU_RTP_MIN_PORT = 40000
SFU_RTP_MAX_PORT = 40200
SFU_NUM_WORKERS = int(os.environ.get('RIBBON_SFU_WORKERS', '1'))

# TURN / STUN
TURN_SECRET = os.environ.get('RIBBON_TURN_SECRET', secrets.token_hex(32))
TURN_REALM = os.environ.get('RIBBON_TURN_REALM', 'ribbon.local')
TURN_HOST = os.environ.get('RIBBON_TURN_HOST', '127.0.0.1')
TURN_PORT = int(os.environ.get('RIBBON_TURN_PORT', '3478'))
TURN_TLS_PORT = int(os.environ.get('RIBBON_TURN_TLS_PORT', '5349'))
TURN_CREDENTIAL_TTL = 86400  # 24 hours

# ICE servers sent to clients
ICE_SERVERS = [
    {'urls': 'stun:stun.l.google.com:19302'},
    {'urls': f'stun:{TURN_HOST}:{TURN_PORT}'},
]

# Room defaults
DEFAULT_MAX_PARTICIPANTS = 15
ROOM_EXPIRY_HOURS = 24
INVITE_LINK_TTL = 3600  # 1 hour

# Amadeus API (Travel search — free tier)
AMADEUS_API_KEY = os.environ.get('AMADEUS_API_KEY', '')
AMADEUS_API_SECRET = os.environ.get('AMADEUS_API_SECRET', '')
AMADEUS_BASE_URL = 'https://test.api.amadeus.com'  # test env (free tier)
AMADEUS_TOKEN_URL = AMADEUS_BASE_URL + '/v1/security/oauth2/token'
AMADEUS_FLIGHT_URL = AMADEUS_BASE_URL + '/v2/shopping/flight-offers'
AMADEUS_HOTEL_URL = AMADEUS_BASE_URL + '/v1/reference-data/locations/hotels/by-city'
AMADEUS_CACHE_TTL = 900  # 15 minutes

# OpenStreetMap / Nominatim (Nearby — free, no API key)
OSM_OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
OSM_NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
OSM_USER_AGENT = 'Ribbon/1.0'

# Media codecs (used by SFU config)
MEDIA_CODECS = [
    {
        'kind': 'audio',
        'mimeType': 'audio/opus',
        'clockRate': 48000,
        'channels': 2,
    },
    {
        'kind': 'video',
        'mimeType': 'video/VP8',
        'clockRate': 90000,
        'parameters': {'x-google-start-bitrate': 1000},
    },
    {
        'kind': 'video',
        'mimeType': 'video/VP9',
        'clockRate': 90000,
        'parameters': {
            'profile-id': 2,
            'x-google-start-bitrate': 1000,
        },
    },
    {
        'kind': 'video',
        'mimeType': 'video/H264',
        'clockRate': 90000,
        'parameters': {
            'packetization-mode': 1,
            'profile-level-id': '4d0032',
            'level-asymmetry-allowed': 1,
            'x-google-start-bitrate': 1000,
        },
    },
]
