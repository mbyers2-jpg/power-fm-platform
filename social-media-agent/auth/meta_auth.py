#!/usr/bin/env python3
"""
Meta (Facebook + Instagram) OAuth 2.0 authentication.
Shared auth flow — one token covers both Facebook Page and Instagram Business account.
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
META_CONFIG = os.path.join(CONFIG_DIR, 'meta_config.json')
META_TOKEN = os.path.join(CONFIG_DIR, 'meta_token.json')

REDIRECT_URI = 'http://localhost:8339/callback'
AUTH_URL = 'https://www.facebook.com/v19.0/dialog/oauth'
TOKEN_URL = 'https://graph.facebook.com/v19.0/oauth/access_token'
GRAPH_API = 'https://graph.facebook.com/v19.0'
SCOPES = 'pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish,pages_show_list'


def setup_meta():
    """
    Interactive setup for Meta (Facebook + Instagram) API credentials.
    """
    print("\n=== Meta (Facebook + Instagram) Setup ===\n")
    print("Prerequisites:")
    print("  1. Go to https://developers.facebook.com/apps/")
    print("  2. Create a Business app")
    print("  3. Add 'Facebook Login for Business' product")
    print("  4. Under Settings > Basic, get App ID and App Secret")
    print("  5. Under Facebook Login > Settings, add redirect URL:")
    print(f"     {REDIRECT_URI}")
    print("  6. Your Facebook Page must be connected to an Instagram Business account")
    print()

    app_id = input("App ID: ").strip()
    app_secret = input("App Secret: ").strip()

    if not app_id or not app_secret:
        print("ERROR: Both App ID and App Secret are required.")
        return False

    # Save config
    os.makedirs(CONFIG_DIR, exist_ok=True)
    config = {
        'app_id': app_id,
        'app_secret': app_secret,
    }
    with open(META_CONFIG, 'w') as f:
        json.dump(config, f, indent=2)
    os.chmod(META_CONFIG, 0o600)

    # Start OAuth flow
    print("\nOpening browser for Facebook authorization...")

    auth_params = {
        'client_id': app_id,
        'redirect_uri': REDIRECT_URI,
        'scope': SCOPES,
        'response_type': 'code',
        'state': 'social-media-agent',
    }
    auth_url = f"{AUTH_URL}?{'&'.join(f'{k}={v}' for k, v in auth_params.items())}"

    auth_code = [None]

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            auth_code[0] = query.get('code', [None])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>Facebook authorized! You can close this tab.</h1>')

        def log_message(self, format, *args):
            pass

    server = HTTPServer(('localhost', 8339), CallbackHandler)
    webbrowser.open(auth_url)

    print("Waiting for authorization callback...")
    server.handle_request()

    if not auth_code[0]:
        print("ERROR: Did not receive authorization code.")
        return False

    # Exchange for short-lived token
    print("Exchanging code for access token...")
    resp = requests.get(TOKEN_URL, params={
        'client_id': app_id,
        'client_secret': app_secret,
        'redirect_uri': REDIRECT_URI,
        'code': auth_code[0],
    })

    if resp.status_code != 200:
        print(f"ERROR: Token exchange failed: {resp.text}")
        return False

    short_token = resp.json().get('access_token')

    # Exchange for long-lived token (60 days)
    print("Exchanging for long-lived token...")
    ll_resp = requests.get(TOKEN_URL, params={
        'grant_type': 'fb_exchange_token',
        'client_id': app_id,
        'client_secret': app_secret,
        'fb_exchange_token': short_token,
    })

    if ll_resp.status_code == 200:
        access_token = ll_resp.json().get('access_token', short_token)
        print("Got long-lived token (valid ~60 days)")
    else:
        access_token = short_token
        print("WARNING: Could not get long-lived token, using short-lived token")

    # Get user's Pages
    print("\nFetching your Facebook Pages...")
    pages_resp = requests.get(f'{GRAPH_API}/me/accounts', params={
        'access_token': access_token,
    })

    page_id = ''
    page_token = ''
    ig_user_id = ''

    if pages_resp.status_code == 200:
        pages = pages_resp.json().get('data', [])
        if not pages:
            print("WARNING: No Facebook Pages found. You need a Page to post.")
        elif len(pages) == 1:
            page = pages[0]
            page_id = page['id']
            page_token = page['access_token']
            print(f"Using Page: {page['name']} (ID: {page_id})")
        else:
            print("\nAvailable Pages:")
            for i, page in enumerate(pages):
                print(f"  {i + 1}. {page['name']} (ID: {page['id']})")
            choice = input(f"\nSelect page (1-{len(pages)}): ").strip()
            try:
                idx = int(choice) - 1
                page = pages[idx]
                page_id = page['id']
                page_token = page['access_token']
            except (ValueError, IndexError):
                print("Invalid selection, using first page.")
                page = pages[0]
                page_id = page['id']
                page_token = page['access_token']

        # Get Instagram Business account connected to this page
        if page_id:
            ig_resp = requests.get(f'{GRAPH_API}/{page_id}', params={
                'fields': 'instagram_business_account',
                'access_token': page_token,
            })
            if ig_resp.status_code == 200:
                ig_data = ig_resp.json().get('instagram_business_account', {})
                ig_user_id = ig_data.get('id', '')
                if ig_user_id:
                    # Get Instagram username
                    ig_user_resp = requests.get(f'{GRAPH_API}/{ig_user_id}', params={
                        'fields': 'username',
                        'access_token': page_token,
                    })
                    if ig_user_resp.status_code == 200:
                        ig_username = ig_user_resp.json().get('username', '')
                        print(f"Instagram Business account: @{ig_username} (ID: {ig_user_id})")
                else:
                    print("WARNING: No Instagram Business account connected to this Page.")
                    print("Connect one in Facebook Page Settings > Instagram.")

    # Save token data
    saved = {
        'access_token': access_token,
        'facebook_page_id': page_id,
        'page_access_token': page_token,
        'instagram_user_id': ig_user_id,
    }
    with open(META_TOKEN, 'w') as f:
        json.dump(saved, f, indent=2)
    os.chmod(META_TOKEN, 0o600)

    print("\nMeta setup complete!")
    if page_id:
        print(f"  Facebook Page: {page_id}")
    if ig_user_id:
        print(f"  Instagram: {ig_user_id}")
    return True
