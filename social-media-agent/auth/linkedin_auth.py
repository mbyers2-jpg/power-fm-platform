#!/usr/bin/env python3
"""
LinkedIn OAuth 2.0 authentication.
Uses Authorization Code flow with browser-based consent.
"""

import os
import json
import logging
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

log = logging.getLogger('social-media-agent')

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
LINKEDIN_CONFIG = os.path.join(CONFIG_DIR, 'linkedin_config.json')
LINKEDIN_TOKEN = os.path.join(CONFIG_DIR, 'linkedin_token.json')

REDIRECT_URI = 'http://localhost:8338/callback'
AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
SCOPES = 'openid profile w_member_social'


def setup_linkedin():
    """
    Interactive setup for LinkedIn API credentials.
    """
    print("\n=== LinkedIn Setup ===\n")
    print("Prerequisites:")
    print("  1. Go to https://www.linkedin.com/developers/apps")
    print("  2. Create an app (or use existing)")
    print("  3. Under 'Auth' tab, add redirect URL: http://localhost:8338/callback")
    print("  4. Under 'Products' tab, request 'Share on LinkedIn' and 'Sign In with LinkedIn using OpenID Connect'")
    print("  5. Copy the Client ID and Client Secret from the 'Auth' tab")
    print()

    client_id = input("Client ID: ").strip()
    client_secret = input("Client Secret: ").strip()

    if not client_id or not client_secret:
        print("ERROR: Both Client ID and Client Secret are required.")
        return False

    # Save config
    os.makedirs(CONFIG_DIR, exist_ok=True)
    config = {
        'client_id': client_id,
        'client_secret': client_secret,
    }
    with open(LINKEDIN_CONFIG, 'w') as f:
        json.dump(config, f, indent=2)
    os.chmod(LINKEDIN_CONFIG, 0o600)

    # Start OAuth flow
    print("\nOpening browser for LinkedIn authorization...")

    auth_params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'scope': SCOPES,
        'state': 'social-media-agent',
    }
    auth_url = f"{AUTH_URL}?{'&'.join(f'{k}={v}' for k, v in auth_params.items())}"

    # Capture the callback
    auth_code = [None]

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            auth_code[0] = query.get('code', [None])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>LinkedIn authorized! You can close this tab.</h1>')

        def log_message(self, format, *args):
            pass  # Suppress HTTP server logs

    server = HTTPServer(('localhost', 8338), CallbackHandler)
    webbrowser.open(auth_url)

    print("Waiting for authorization callback...")
    server.handle_request()  # Handle one request (the callback)

    if not auth_code[0]:
        print("ERROR: Did not receive authorization code.")
        return False

    # Exchange code for token
    print("Exchanging code for access token...")
    resp = requests.post(TOKEN_URL, data={
        'grant_type': 'authorization_code',
        'code': auth_code[0],
        'redirect_uri': REDIRECT_URI,
        'client_id': client_id,
        'client_secret': client_secret,
    })

    if resp.status_code != 200:
        print(f"ERROR: Token exchange failed: {resp.text}")
        return False

    token_data = resp.json()
    access_token = token_data.get('access_token')

    if not access_token:
        print("ERROR: No access token received.")
        return False

    # Get person ID
    me_resp = requests.get('https://api.linkedin.com/v2/me', headers={
        'Authorization': f'Bearer {access_token}',
    })

    person_id = ''
    if me_resp.status_code == 200:
        me_data = me_resp.json()
        person_id = me_data.get('id', '')
        name = f"{me_data.get('localizedFirstName', '')} {me_data.get('localizedLastName', '')}".strip()
        print(f"Authenticated as: {name} (ID: {person_id})")

    # Save token
    saved = {
        'access_token': access_token,
        'person_id': person_id,
        'expires_in': token_data.get('expires_in'),
    }
    with open(LINKEDIN_TOKEN, 'w') as f:
        json.dump(saved, f, indent=2)
    os.chmod(LINKEDIN_TOKEN, 0o600)

    print("LinkedIn setup complete!")
    return True
