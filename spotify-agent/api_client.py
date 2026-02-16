"""
Spotify Web API client with OAuth user auth (refresh token) and rate limiting.
Uses Authorization Code flow with PKCE — run auth.py once to get tokens.
Falls back to client_credentials if no user token is available.
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timedelta

log = logging.getLogger('spotify-agent')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'spotify_config.json')
TOKEN_PATH = os.path.join(os.path.dirname(__file__), 'config', 'spotify_token.json')

BASE_URL = 'https://api.spotify.com/v1'
AUTH_URL = 'https://accounts.spotify.com/api/token'


class SpotifyAuthError(Exception):
    """Raised when Spotify authentication fails."""
    pass


class SpotifyAPIError(Exception):
    """Raised when a Spotify API request fails."""
    pass


class SpotifyClient:
    """Spotify Web API client using OAuth user token (preferred) or client_credentials."""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self._load_config()
        self._load_user_token()

    def _load_config(self):
        """Load client_id and client_secret from config JSON."""
        if not os.path.exists(self.config_path):
            raise SpotifyAuthError(
                f"Config file not found: {self.config_path}\n"
                "Create config/spotify_config.json with your Spotify Developer credentials.\n"
                "See SETUP.md for instructions."
            )

        with open(self.config_path, 'r') as f:
            config = json.load(f)

        self.client_id = config.get('client_id', '').strip()
        self.client_secret = config.get('client_secret', '').strip()

        if not self.client_id or not self.client_secret:
            raise SpotifyAuthError(
                "client_id and client_secret must be set in config/spotify_config.json"
            )

    def _load_user_token(self):
        """Load user OAuth token from auth.py output."""
        if not os.path.exists(TOKEN_PATH):
            log.info("No user token found. Run 'venv/bin/python auth.py' to authenticate.")
            return

        try:
            with open(TOKEN_PATH, 'r') as f:
                data = json.load(f)
            self.access_token = data.get('access_token')
            self.refresh_token = data.get('refresh_token')
            log.info("Loaded Spotify user token (will refresh automatically)")
        except (json.JSONDecodeError, KeyError) as e:
            log.warning(f"Could not load user token: {e}")

    def _save_user_token(self, token_data):
        """Save updated token data to disk."""
        # Merge with existing data to preserve refresh_token if not returned
        existing = {}
        if os.path.exists(TOKEN_PATH):
            try:
                with open(TOKEN_PATH, 'r') as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        existing.update(token_data)
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, 'w') as f:
            json.dump(existing, f, indent=2)

    def _authenticate(self):
        """Obtain a new access token — uses refresh token if available, else client_credentials."""
        if self.refresh_token:
            self._refresh_user_token()
        else:
            self._client_credentials_auth()

    def _refresh_user_token(self):
        """Refresh the user access token using the refresh token."""
        log.info("Refreshing Spotify user token...")
        try:
            resp = requests.post(AUTH_URL, data={
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
                'client_id': self.client_id,
            }, timeout=30)
        except requests.RequestException as e:
            raise SpotifyAuthError(f"Failed to reach Spotify auth server: {e}")

        if resp.status_code != 200:
            log.error(f"Token refresh failed ({resp.status_code}): {resp.text}")
            # If refresh fails, clear the bad token and fall back
            self.refresh_token = None
            self._client_credentials_auth()
            return

        data = resp.json()
        self.access_token = data['access_token']
        # Spotify may return a new refresh token
        if 'refresh_token' in data:
            self.refresh_token = data['refresh_token']
        expires_in = data.get('expires_in', 3600)
        self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        self._save_user_token(data)
        log.info(f"Spotify user token refreshed, expires in {expires_in}s")

    def _client_credentials_auth(self):
        """Fallback: obtain token using client_credentials grant (limited in dev mode)."""
        log.info("Authenticating with Spotify API (client_credentials)...")
        try:
            resp = requests.post(AUTH_URL, data={
                'grant_type': 'client_credentials',
            }, auth=(self.client_id, self.client_secret), timeout=30)
        except requests.RequestException as e:
            raise SpotifyAuthError(f"Failed to reach Spotify auth server: {e}")

        if resp.status_code != 200:
            raise SpotifyAuthError(
                f"Spotify auth failed (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        self.access_token = data['access_token']
        expires_in = data.get('expires_in', 3600)
        self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        log.info(f"Spotify client_credentials auth OK, expires in {expires_in}s")

    def _ensure_authenticated(self):
        """Ensure we have a valid access token, refreshing if needed."""
        if self.access_token and self.token_expires_at:
            if self.token_expires_at > datetime.utcnow() + timedelta(seconds=60):
                return
            log.info("Spotify token expiring soon, refreshing...")

        self._authenticate()

    def _request(self, method, endpoint, params=None, retries=2):
        """
        Make an authenticated request to the Spotify API.
        Handles token refresh on 401 and rate limiting on 429.
        """
        self._ensure_authenticated()

        url = f"{BASE_URL}{endpoint}" if endpoint.startswith('/') else f"{BASE_URL}/{endpoint}"

        for attempt in range(retries + 1):
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json',
            }

            try:
                resp = requests.request(
                    method, url, headers=headers, params=params, timeout=30
                )
            except requests.RequestException as e:
                log.error(f"Request failed: {method} {url} - {e}")
                if attempt < retries:
                    time.sleep(2)
                    continue
                raise SpotifyAPIError(f"Request failed after {retries + 1} attempts: {e}")

            # Success
            if resp.status_code == 200:
                return resp.json()

            # No content (some endpoints)
            if resp.status_code == 204:
                return {}

            # Token expired or dev mode restriction — re-authenticate and retry
            if resp.status_code in (401, 403):
                if attempt == 0:
                    log.warning(f"Got {resp.status_code}, refreshing token...")
                    self._authenticate()
                    continue

            # Rate limited — respect Retry-After header
            if resp.status_code == 429:
                retry_after = int(resp.headers.get('Retry-After', 5))
                log.warning(f"Rate limited (429). Waiting {retry_after}s...")
                time.sleep(retry_after)
                continue

            # Not found
            if resp.status_code == 404:
                log.warning(f"Not found: {endpoint}")
                return None

            # Other errors
            log.error(f"Spotify API error {resp.status_code}: {resp.text[:500]}")
            if attempt < retries:
                time.sleep(2)
                continue
            raise SpotifyAPIError(f"API error {resp.status_code}: {resp.text[:200]}")

        return None

    # --- Artist Endpoints ---

    def get_artist(self, artist_id):
        """GET /artists/{id} - Get an artist's profile."""
        return self._request('GET', f'/artists/{artist_id}')

    def get_artist_top_tracks(self, artist_id, market='US'):
        """GET /artists/{id}/top-tracks - Get an artist's top tracks."""
        return self._request('GET', f'/artists/{artist_id}/top-tracks', params={'market': market})

    def get_artist_albums(self, artist_id, include_groups='album,single', limit=50):
        """GET /artists/{id}/albums - Get an artist's albums."""
        return self._request('GET', f'/artists/{artist_id}/albums', params={
            'include_groups': include_groups,
            'limit': limit,
            'market': 'US',
        })

    # --- Track Endpoints ---

    def get_track(self, track_id):
        """GET /tracks/{id} - Get a track."""
        return self._request('GET', f'/tracks/{track_id}')

    def get_audio_features(self, track_id):
        """GET /audio-features/{id} - Get audio features for a track."""
        return self._request('GET', f'/audio-features/{track_id}')

    def get_several_tracks(self, track_ids):
        """GET /tracks?ids= - Get several tracks (max 50 per request)."""
        results = []
        # Batch into groups of 50
        for i in range(0, len(track_ids), 50):
            batch = track_ids[i:i + 50]
            ids_str = ','.join(batch)
            data = self._request('GET', '/tracks', params={'ids': ids_str})
            if data and 'tracks' in data:
                results.extend([t for t in data['tracks'] if t])
        return results

    # --- Playlist Endpoints ---

    def get_playlist(self, playlist_id):
        """GET /playlists/{id} - Get a playlist."""
        return self._request('GET', f'/playlists/{playlist_id}', params={
            'fields': 'id,name,description,owner(display_name),followers(total),tracks(total),snapshot_id,external_urls',
        })

    def get_playlist_tracks(self, playlist_id, limit=100):
        """GET /playlists/{id}/tracks - Get playlist tracks with pagination."""
        all_items = []
        offset = 0

        while True:
            data = self._request('GET', f'/playlists/{playlist_id}/tracks', params={
                'limit': min(limit, 100),
                'offset': offset,
                'fields': 'items(added_at,track(id,name,artists(id,name),album(id,name))),next,total',
            })
            if not data or 'items' not in data:
                break

            all_items.extend(data['items'])

            if not data.get('next') or len(all_items) >= limit:
                break

            offset += 100

        return all_items

    # --- Search ---

    def search(self, query, type='artist', limit=10):
        """GET /search - Search for items."""
        return self._request('GET', '/search', params={
            'q': query,
            'type': type,
            'limit': limit,
            'market': 'US',
        })
