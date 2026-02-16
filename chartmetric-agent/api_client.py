"""
Chartmetric API client.
Handles authentication, rate limiting, and data fetching from the Chartmetric API.
"""

import os
import json
import time
import logging

import requests

log = logging.getLogger('chartmetric-agent')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'chartmetric_config.json')
BASE_URL = 'https://api.chartmetric.com/api'
TOKEN_URL = 'https://api.chartmetric.com/api/token'

# Rate limiting
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
RATE_LIMIT_DELAY = 1  # seconds between requests


class ChartmetricClient:
    """Client for the Chartmetric API with auth, rate limiting, and error handling."""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        self.access_token = None
        self.token_expiry = 0
        self._last_request_time = 0

    def _load_config(self):
        """Load API configuration from JSON file."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(
                f"Config not found: {self.config_path}\n"
                f"Create config/chartmetric_config.json with your API credentials.\n"
                f"See SETUP.md for instructions."
            )

        with open(self.config_path, 'r') as f:
            config = json.load(f)

        if not config.get('refresh_token'):
            raise ValueError(
                "refresh_token is required in chartmetric_config.json.\n"
                "Get your refresh token from https://app.chartmetric.com → Account → API"
            )

        return config

    def _authenticate(self):
        """Authenticate with Chartmetric API using refresh token to get access token."""
        log.info("Authenticating with Chartmetric API...")

        try:
            resp = requests.post(TOKEN_URL, json={
                'refreshtoken': self.config['refresh_token']
            }, timeout=30)

            if resp.status_code == 401:
                raise ValueError(
                    "Authentication failed: Invalid refresh token. "
                    "Generate a new one at https://app.chartmetric.com → Account → API"
                )

            resp.raise_for_status()
            data = resp.json()

            self.access_token = data.get('token')
            # Token typically expires in 3600 seconds; refresh 5 min early
            expires_in = data.get('expires_in', 3600)
            self.token_expiry = time.time() + expires_in - 300

            log.info("Chartmetric authentication successful")

        except requests.exceptions.RequestException as e:
            log.error(f"Authentication request failed: {e}")
            raise

    def _ensure_auth(self):
        """Ensure we have a valid access token, refreshing if needed."""
        if not self.access_token or time.time() >= self.token_expiry:
            self._authenticate()

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _request(self, method, endpoint, params=None, data=None):
        """
        Make an authenticated API request with rate limiting and retries.
        Handles 429 (rate limit) by backing off and retrying.
        """
        self._ensure_auth()
        self._rate_limit()

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
        }

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.request(
                    method, url,
                    headers=headers,
                    params=params,
                    json=data,
                    timeout=30
                )

                # Rate limited — back off and retry
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('Retry-After', RETRY_DELAY * (attempt + 1)))
                    log.warning(f"Rate limited (429). Retrying in {retry_after}s... (attempt {attempt + 1}/{MAX_RETRIES})")
                    time.sleep(retry_after)
                    continue

                # Token expired mid-session — re-auth and retry
                if resp.status_code == 401:
                    log.warning("Token expired, re-authenticating...")
                    self._authenticate()
                    headers['Authorization'] = f'Bearer {self.access_token}'
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.exceptions.Timeout:
                log.warning(f"Request timeout for {endpoint} (attempt {attempt + 1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                continue

            except requests.exceptions.RequestException as e:
                log.error(f"Request failed for {endpoint}: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                else:
                    raise

        log.error(f"All {MAX_RETRIES} attempts failed for {endpoint}")
        return None

    # --- Artist Endpoints ---

    def search_artist(self, name):
        """
        Search for an artist by name.
        Returns list of matching artists with chartmetric_id, name, etc.
        """
        result = self._request('GET', '/artist/search', params={'q': name})
        if not result:
            return []

        artists = result.get('obj', {}).get('artists', [])
        return [
            {
                'chartmetric_id': a.get('id'),
                'name': a.get('name', ''),
                'spotify_id': (a.get('spotify_artist_ids') or [None])[0],
                'apple_music_id': a.get('apple_music_artist_id'),
                'image_url': a.get('image_url', ''),
                'genres': ','.join(a.get('genres', [])) if a.get('genres') else '',
            }
            for a in artists
        ]

    def get_artist(self, chartmetric_id):
        """
        Get artist profile by Chartmetric ID.
        Returns parsed artist data dict.
        """
        result = self._request('GET', f'/artist/{chartmetric_id}')
        if not result:
            return None

        a = result.get('obj', {})
        return {
            'chartmetric_id': a.get('id', chartmetric_id),
            'name': a.get('name', ''),
            'spotify_id': (a.get('spotify_artist_ids') or [None])[0],
            'apple_music_id': a.get('apple_music_artist_id'),
            'image_url': a.get('image_url', ''),
            'genres': ','.join(a.get('genres', [])) if a.get('genres') else '',
        }

    def get_artist_charts(self, chartmetric_id, chart_type='spotify_viral_daily'):
        """
        Get chart entries for an artist.
        chart_type: spotify_viral_daily, spotify_top_daily, apple_music_daily,
                    shazam_city_top, itunes_top, billboard_200, etc.
        Returns list of chart entry dicts.
        """
        result = self._request('GET', f'/artist/{chartmetric_id}/charts', params={
            'type': chart_type,
            'limit': 50,
        })
        if not result:
            return []

        entries = result.get('obj', [])
        if isinstance(entries, dict):
            entries = entries.get('data', [])

        return [
            {
                'chart_name': e.get('chart_name', chart_type),
                'chart_type': chart_type,
                'position': e.get('position') or e.get('rank'),
                'previous_position': e.get('previous_position') or e.get('prev_rank'),
                'peak_position': e.get('peak_position') or e.get('peak_rank'),
                'weeks_on_chart': e.get('weeks_on_chart', 0),
                'date': e.get('date', ''),
            }
            for e in entries
        ]

    def get_streaming_stats(self, chartmetric_id, platform='spotify'):
        """
        Get streaming statistics for an artist on a given platform.
        platform: spotify, apple_music, deezer, amazon, youtube, soundcloud, tiktok
        Returns dict with streams, listeners, followers.
        """
        result = self._request('GET', f'/artist/{chartmetric_id}/stat/{platform}', params={
            'latest': 'true',
        })
        if not result:
            return None

        obj = result.get('obj', {})
        # Handle both list and dict response formats
        if isinstance(obj, list) and obj:
            stat = obj[0]
        elif isinstance(obj, dict):
            stat = obj
        else:
            return None

        return {
            'platform': platform,
            'streams': stat.get('streams') or stat.get('plays') or 0,
            'listeners': stat.get('listeners') or stat.get('monthly_listeners') or 0,
            'followers': stat.get('followers') or stat.get('subscriber_count') or 0,
        }

    def get_radio_spins(self, chartmetric_id):
        """
        Get radio airplay data for an artist.
        Returns list of radio spin dicts.
        """
        result = self._request('GET', f'/artist/{chartmetric_id}/radio-spins', params={
            'limit': 100,
        })
        if not result:
            return []

        spins = result.get('obj', [])
        if isinstance(spins, dict):
            spins = spins.get('data', [])

        return [
            {
                'track_name': s.get('track_name', ''),
                'station': s.get('station_name', '') or s.get('station', ''),
                'market': s.get('market', '') or s.get('country', ''),
                'spins': s.get('spins', 0) or s.get('spin_count', 0),
                'date': s.get('date', '') or s.get('timestamp', ''),
            }
            for s in spins
        ]

    def get_social_metrics(self, chartmetric_id, platform='instagram'):
        """
        Get social media metrics for an artist.
        platform: instagram, twitter, facebook, tiktok, youtube
        Returns dict with followers, engagement_rate, posts.
        """
        result = self._request('GET', f'/artist/{chartmetric_id}/stat/{platform}', params={
            'latest': 'true',
        })
        if not result:
            return None

        obj = result.get('obj', {})
        if isinstance(obj, list) and obj:
            stat = obj[0]
        elif isinstance(obj, dict):
            stat = obj
        else:
            return None

        return {
            'platform': platform,
            'followers': stat.get('followers') or stat.get('subscriber_count') or 0,
            'engagement_rate': stat.get('engagement_rate', 0.0),
            'posts': stat.get('posts') or stat.get('media_count') or 0,
        }

    def get_playlist_placements(self, chartmetric_id):
        """
        Get playlist placements for an artist.
        Returns list of playlist dicts.
        """
        result = self._request('GET', f'/artist/{chartmetric_id}/playlists', params={
            'platform': 'spotify',
            'status': 'current',
            'limit': 100,
        })
        if not result:
            return []

        playlists = result.get('obj', [])
        if isinstance(playlists, dict):
            playlists = playlists.get('data', [])

        return [
            {
                'playlist_name': p.get('name', ''),
                'platform': 'spotify',
                'playlist_id': p.get('playlist_id', '') or p.get('id', ''),
                'followers': p.get('followers', 0) or p.get('follower_count', 0),
                'position': p.get('position') or p.get('track_position'),
                'added_date': p.get('added_at', '') or p.get('date', ''),
            }
            for p in playlists
        ]
