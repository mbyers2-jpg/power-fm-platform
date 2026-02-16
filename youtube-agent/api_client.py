"""
YouTube Data API v3 Client
Read-only API client using an API key (no OAuth required).
Handles quota tracking, error handling, and response normalization.
"""

import os
import json
import logging
import requests
from datetime import datetime

log = logging.getLogger('youtube-agent')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'youtube_config.json')
BASE_URL = 'https://www.googleapis.com/youtube/v3'

# YouTube Data API v3 quota costs (units per request)
QUOTA_COSTS = {
    'channels.list': 1,
    'search.list': 100,
    'videos.list': 1,
    'commentThreads.list': 1,
    'playlists.list': 1,
}


class YouTubeClient:
    """YouTube Data API v3 client using API key authentication."""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.api_key = None
        self.quota_used = 0
        self.quota_limit = 10000  # Default daily quota
        self._load_config()

    def _load_config(self):
        """Load API key from config file."""
        if not os.path.exists(self.config_path):
            log.warning(
                f"Config not found at {self.config_path}. "
                "YouTube API calls will fail. See SETUP.md for instructions."
            )
            return

        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            self.api_key = config.get('api_key', '').strip()
            self.quota_limit = config.get('quota_limit', 10000)
            if not self.api_key:
                log.warning("api_key is empty in config file. API calls will fail.")
        except (json.JSONDecodeError, IOError) as e:
            log.error(f"Failed to load config: {e}")

    def _check_ready(self):
        """Check if the client is configured and ready."""
        if not self.api_key:
            raise RuntimeError(
                "YouTube API key not configured. "
                "Create config/youtube_config.json with your api_key. "
                "See SETUP.md for instructions."
            )

    def _request(self, endpoint, params, quota_cost_key=None):
        """
        Make a GET request to the YouTube Data API.
        Injects API key, tracks quota, handles errors.
        """
        self._check_ready()

        # Track quota
        cost = QUOTA_COSTS.get(quota_cost_key, 1)
        if self.quota_used + cost > self.quota_limit:
            log.warning(
                f"Quota limit approaching: {self.quota_used}/{self.quota_limit} units used. "
                f"Request would cost {cost} units."
            )

        params['key'] = self.api_key
        url = f"{BASE_URL}/{endpoint}"

        try:
            resp = requests.get(url, params=params, timeout=30)

            # Track quota usage
            self.quota_used += cost

            if resp.status_code == 403:
                error_data = resp.json().get('error', {})
                errors = error_data.get('errors', [])
                for err in errors:
                    if err.get('reason') == 'quotaExceeded':
                        log.error("YouTube API daily quota exceeded!")
                        raise RuntimeError("YouTube API quota exceeded for today.")
                    if err.get('reason') == 'forbidden':
                        log.error(f"YouTube API forbidden: {error_data.get('message', '')}")
                        raise RuntimeError(f"YouTube API access forbidden: {error_data.get('message', '')}")
                log.error(f"YouTube API 403: {resp.text}")
                raise RuntimeError(f"YouTube API error 403: {resp.text[:200]}")

            if resp.status_code == 404:
                log.warning(f"YouTube API 404 for {endpoint}: resource not found")
                return None

            if resp.status_code != 200:
                log.error(f"YouTube API error {resp.status_code}: {resp.text[:200]}")
                raise RuntimeError(f"YouTube API error {resp.status_code}: {resp.text[:200]}")

            return resp.json()

        except requests.exceptions.Timeout:
            log.error(f"YouTube API timeout for {endpoint}")
            raise RuntimeError(f"YouTube API request timed out: {endpoint}")
        except requests.exceptions.ConnectionError as e:
            log.error(f"YouTube API connection error: {e}")
            raise RuntimeError(f"YouTube API connection error: {e}")

    # --- Channel Operations ---

    def get_channel(self, channel_id):
        """
        Get channel details by channel ID.
        Returns normalized channel dict or None.
        """
        data = self._request('channels', {
            'part': 'snippet,statistics',
            'id': channel_id,
        }, quota_cost_key='channels.list')

        if not data or not data.get('items'):
            log.warning(f"Channel not found: {channel_id}")
            return None

        item = data['items'][0]
        snippet = item.get('snippet', {})
        stats = item.get('statistics', {})

        return {
            'channel_id': item['id'],
            'title': snippet.get('title', ''),
            'description': snippet.get('description', ''),
            'subscriber_count': int(stats.get('subscriberCount', 0)),
            'video_count': int(stats.get('videoCount', 0)),
            'view_count': int(stats.get('viewCount', 0)),
            'custom_url': snippet.get('customUrl', ''),
            'thumbnail_url': snippet.get('thumbnails', {}).get('default', {}).get('url', ''),
        }

    # --- Video Operations ---

    def get_channel_videos(self, channel_id, max_results=50):
        """
        Get recent videos from a channel.
        Uses search endpoint to find video IDs, then videos endpoint for full stats.
        Returns list of normalized video dicts.
        """
        # Step 1: Search for channel's videos (costs 100 quota units)
        search_data = self._request('search', {
            'part': 'snippet',
            'channelId': channel_id,
            'type': 'video',
            'order': 'date',
            'maxResults': min(max_results, 50),
        }, quota_cost_key='search.list')

        if not search_data or not search_data.get('items'):
            return []

        # Collect video IDs
        video_ids = [
            item['id']['videoId']
            for item in search_data['items']
            if item.get('id', {}).get('videoId')
        ]

        if not video_ids:
            return []

        # Step 2: Get full video details (costs 1 quota unit)
        return self._get_videos_by_ids(video_ids, channel_id)

    def _get_videos_by_ids(self, video_ids, default_channel_id=''):
        """Fetch full video details for a list of video IDs."""
        # YouTube API allows up to 50 IDs per request
        videos = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            data = self._request('videos', {
                'part': 'snippet,statistics,contentDetails',
                'id': ','.join(batch),
            }, quota_cost_key='videos.list')

            if not data or not data.get('items'):
                continue

            for item in data['items']:
                snippet = item.get('snippet', {})
                stats = item.get('statistics', {})
                content = item.get('contentDetails', {})

                videos.append({
                    'video_id': item['id'],
                    'channel_id': snippet.get('channelId', default_channel_id),
                    'title': snippet.get('title', ''),
                    'description': snippet.get('description', ''),
                    'published_at': snippet.get('publishedAt', ''),
                    'duration': content.get('duration', ''),
                    'view_count': int(stats.get('viewCount', 0)),
                    'like_count': int(stats.get('likeCount', 0)),
                    'comment_count': int(stats.get('commentCount', 0)),
                    'thumbnail_url': snippet.get('thumbnails', {}).get('medium', {}).get('url', ''),
                    'tags': snippet.get('tags', []),
                    'category_id': snippet.get('categoryId', ''),
                    'status': 'active',
                })

        return videos

    def get_video(self, video_id):
        """
        Get full details for a single video.
        Returns normalized video dict or None.
        """
        videos = self._get_videos_by_ids([video_id])
        return videos[0] if videos else None

    # --- Comments ---

    def get_video_comments(self, video_id, max_results=20):
        """
        Get top-level comments for a video.
        Returns list of normalized comment dicts.
        """
        try:
            data = self._request('commentThreads', {
                'part': 'snippet',
                'videoId': video_id,
                'order': 'relevance',
                'maxResults': min(max_results, 100),
                'textFormat': 'plainText',
            }, quota_cost_key='commentThreads.list')
        except RuntimeError as e:
            # Comments may be disabled on some videos
            if '403' in str(e) or 'disabled' in str(e).lower():
                log.info(f"Comments disabled or restricted for video {video_id}")
                return []
            raise

        if not data or not data.get('items'):
            return []

        comments = []
        for item in data['items']:
            top = item.get('snippet', {}).get('topLevelComment', {})
            snippet = top.get('snippet', {})
            comments.append({
                'comment_id': top.get('id', ''),
                'video_id': video_id,
                'author': snippet.get('authorDisplayName', ''),
                'text': snippet.get('textDisplay', ''),
                'like_count': int(snippet.get('likeCount', 0)),
                'published_at': snippet.get('publishedAt', ''),
            })

        return comments

    # --- Playlists ---

    def get_playlists(self, channel_id, max_results=25):
        """
        Get playlists for a channel.
        Returns list of normalized playlist dicts.
        """
        data = self._request('playlists', {
            'part': 'snippet,contentDetails',
            'channelId': channel_id,
            'maxResults': min(max_results, 50),
        }, quota_cost_key='playlists.list')

        if not data or not data.get('items'):
            return []

        playlists = []
        for item in data['items']:
            snippet = item.get('snippet', {})
            content = item.get('contentDetails', {})
            playlists.append({
                'playlist_id': item['id'],
                'channel_id': channel_id,
                'title': snippet.get('title', ''),
                'description': snippet.get('description', ''),
                'item_count': int(content.get('itemCount', 0)),
            })

        return playlists

    # --- Search ---

    def search(self, query, max_results=10):
        """
        Search YouTube for videos matching a query.
        Returns list of normalized video dicts (basic info only from search).
        """
        data = self._request('search', {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'maxResults': min(max_results, 50),
        }, quota_cost_key='search.list')

        if not data or not data.get('items'):
            return []

        # Get full video details for search results
        video_ids = [
            item['id']['videoId']
            for item in data['items']
            if item.get('id', {}).get('videoId')
        ]

        if not video_ids:
            return []

        return self._get_videos_by_ids(video_ids)

    # --- Quota ---

    def get_quota_usage(self):
        """Return current quota tracking info."""
        return {
            'used': self.quota_used,
            'limit': self.quota_limit,
            'remaining': max(0, self.quota_limit - self.quota_used),
            'pct_used': round(self.quota_used / self.quota_limit * 100, 1) if self.quota_limit else 0,
        }

    def reset_quota(self):
        """Reset quota counter (call at start of new day)."""
        self.quota_used = 0


if __name__ == '__main__':
    client = YouTubeClient()
    print(f"Config loaded: {'Yes' if client.api_key else 'No'}")
    print(f"Quota: {client.get_quota_usage()}")
