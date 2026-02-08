"""
Microsoft 365 OAuth2 Authentication Module
Uses MSAL with interactive browser flow for first auth, then silent token refresh.
Token cache persisted to disk for daemon operation.
"""

import os
import sys
import json
import msal

CONFIG_DIR = os.path.join(os.path.dirname(__file__), 'config')
MS_CONFIG_PATH = os.path.join(CONFIG_DIR, 'microsoft_config.json')
MS_TOKEN_CACHE_PATH = os.path.join(CONFIG_DIR, 'microsoft_token_cache.json')

GRAPH_SCOPES = [
    'https://graph.microsoft.com/Mail.Read',
    'https://graph.microsoft.com/Mail.ReadBasic',
    'https://graph.microsoft.com/User.Read',
]


def _load_config():
    """Load Azure AD app configuration."""
    if not os.path.exists(MS_CONFIG_PATH):
        print(f"ERROR: {MS_CONFIG_PATH} not found.")
        print("Create it with your Azure AD client_id and tenant_id.")
        raise FileNotFoundError(MS_CONFIG_PATH)

    with open(MS_CONFIG_PATH) as f:
        config = json.load(f)

    if config.get('client_id', '').startswith('YOUR_'):
        print("ERROR: Update microsoft_config.json with your actual Azure AD credentials.")
        print(f"Edit: {MS_CONFIG_PATH}")
        raise ValueError("Placeholder credentials in microsoft_config.json")

    return config


def _build_app(config):
    """Create an MSAL PublicClientApplication with persistent token cache."""
    cache = msal.SerializableTokenCache()

    if os.path.exists(MS_TOKEN_CACHE_PATH):
        with open(MS_TOKEN_CACHE_PATH) as f:
            cache.deserialize(f.read())

    authority = f"https://login.microsoftonline.com/{config['tenant_id']}"

    app = msal.PublicClientApplication(
        config['client_id'],
        authority=authority,
        token_cache=cache,
    )
    return app, cache


def _save_cache(cache):
    """Persist the token cache to disk."""
    if cache.has_state_changed:
        with open(MS_TOKEN_CACHE_PATH, 'w') as f:
            f.write(cache.serialize())


def get_microsoft_token():
    """
    Get a valid Microsoft Graph access token.
    Uses cached refresh token if available, otherwise runs interactive browser auth.
    Returns (access_token, account_email) tuple.
    """
    config = _load_config()
    app, cache = _build_app(config)

    accounts = app.get_accounts()
    result = None

    if accounts:
        # Try silent token acquisition with cached refresh token
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])

    if not result:
        # Interactive browser flow (first run or expired refresh token)
        print("Opening browser for Microsoft 365 login...")
        print("Sign in with: m.byers@2035ventures.co")
        result = app.acquire_token_interactive(
            scopes=GRAPH_SCOPES,
            prompt='select_account',
        )

    _save_cache(cache)

    if 'access_token' in result:
        return result['access_token'], config.get('account_email', 'm.byers@2035ventures.co')
    else:
        error = result.get('error', 'unknown')
        desc = result.get('error_description', '')
        raise RuntimeError(f"Microsoft auth failed: {error} — {desc}")


if __name__ == '__main__':
    print("Running Microsoft 365 authentication flow...")
    try:
        token, email = get_microsoft_token()
        print(f"Authenticated as: {email}")
        print(f"Token length: {len(token)} chars")
        print("Token cache saved. Agent can now use silent auth.")

        # Quick test — fetch user profile
        import requests
        resp = requests.get(
            'https://graph.microsoft.com/v1.0/me',
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        if resp.ok:
            profile = resp.json()
            print(f"Profile: {profile.get('displayName')} ({profile.get('mail', profile.get('userPrincipalName'))})")
        else:
            print(f"Profile fetch failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"Auth failed: {e}")
        sys.exit(1)
