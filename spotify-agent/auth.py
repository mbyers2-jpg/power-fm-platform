#!/usr/bin/env python3
"""
Spotify OAuth Authorization Code Flow with PKCE.
Opens browser for user login, captures callback, saves refresh token.
Run this once to authenticate, then the agent uses the refresh token automatically.

Usage:
    venv/bin/python auth.py
"""

import os
import sys
import json
import hashlib
import base64
import secrets
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'spotify_config.json')
TOKEN_PATH = os.path.join(os.path.dirname(__file__), 'config', 'spotify_token.json')

REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPES = 'user-read-private user-read-email user-library-read user-top-read playlist-read-private playlist-read-collaborative'
AUTH_BASE = 'https://accounts.spotify.com'


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def generate_pkce():
    """Generate PKCE code verifier and challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    return verifier, challenge


def get_auth_url(client_id, challenge, state):
    """Build the Spotify authorization URL."""
    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': REDIRECT_URI,
        'scope': SCOPES,
        'code_challenge_method': 'S256',
        'code_challenge': challenge,
        'state': state,
    }
    return f"{AUTH_BASE}/authorize?{urllib.parse.urlencode(params)}"


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to capture the OAuth callback."""
    auth_code = None
    auth_state = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if 'code' in params:
            CallbackHandler.auth_code = params['code'][0]
            CallbackHandler.auth_state = params.get('state', [None])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h2>Spotify connected! You can close this tab.</h2></body></html>')
        elif 'error' in params:
            self.send_response(400)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            error = params['error'][0]
            self.wfile.write(f'<html><body><h2>Error: {error}</h2></body></html>'.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress server logs


def exchange_code(client_id, code, verifier):
    """Exchange authorization code for access + refresh tokens."""
    resp = requests.post(f'{AUTH_BASE}/api/token', data={
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'client_id': client_id,
        'code_verifier': verifier,
    })

    if resp.status_code != 200:
        print(f"Token exchange failed: {resp.status_code} {resp.text}")
        return None

    return resp.json()


def save_token(token_data):
    """Save token data to disk."""
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, 'w') as f:
        json.dump(token_data, f, indent=2)
    print(f"Token saved to: {TOKEN_PATH}")


def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: {CONFIG_PATH} not found. Create it first.")
        sys.exit(1)

    config = load_config()
    client_id = config['client_id']

    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    auth_url = get_auth_url(client_id, challenge, state)

    print("Opening Spotify login in your browser...")
    print(f"If it doesn't open, go to:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Start local server to catch callback
    server = HTTPServer(('127.0.0.1', 8888), CallbackHandler)
    print("Waiting for Spotify callback on http://127.0.0.1:8888 ...")
    server.handle_request()  # Handle one request then stop

    if not CallbackHandler.auth_code:
        print("ERROR: No authorization code received.")
        sys.exit(1)

    if CallbackHandler.auth_state != state:
        print("ERROR: State mismatch — possible CSRF attack.")
        sys.exit(1)

    print("Authorization code received. Exchanging for tokens...")
    token_data = exchange_code(client_id, CallbackHandler.auth_code, verifier)

    if not token_data:
        print("ERROR: Token exchange failed.")
        sys.exit(1)

    save_token(token_data)
    print(f"\nSpotify authenticated successfully!")
    print(f"  Access token: {token_data['access_token'][:20]}...")
    print(f"  Refresh token: {token_data['refresh_token'][:20]}...")
    print(f"  Scopes: {token_data.get('scope', '')}")


if __name__ == '__main__':
    main()
