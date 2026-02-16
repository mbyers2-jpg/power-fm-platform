#!/usr/bin/env python3
"""
Facebook platform implementation using Graph API.
Posts to a Facebook Page (requires Page access token).
"""

import os
import json
import logging
import requests

from platforms import PlatformBase, PlatformError, RateLimitError, AuthError, register_platform

log = logging.getLogger('social-media-agent')

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
META_TOKEN = os.path.join(CONFIG_DIR, 'meta_token.json')

GRAPH_API = 'https://graph.facebook.com/v19.0'


@register_platform('facebook')
class FacebookPlatform(PlatformBase):
    name = 'facebook'
    display_name = 'Facebook'
    max_post_length = 63206
    supports_threads = False
    supports_images = True

    def __init__(self):
        super().__init__()
        self._token = None
        self._page_id = None
        self._page_token = None

    def _load_token(self):
        """Load Meta OAuth token and page token."""
        if self._page_token:
            return self._page_token

        if not os.path.exists(META_TOKEN):
            raise AuthError("Facebook not authenticated. Run: python agent.py --setup facebook")

        with open(META_TOKEN, 'r') as f:
            data = json.load(f)

        self._token = data.get('access_token')
        self._page_id = data.get('facebook_page_id')
        self._page_token = data.get('page_access_token')

        if not self._page_token or not self._page_id:
            raise AuthError(
                "Missing Facebook Page token or page ID. Re-run setup with page permissions."
            )

        return self._page_token

    def post(self, body, hashtags='', media_path=None, reply_to=None):
        """Post to Facebook Page."""
        self._load_token()
        text = self.format_post(body, hashtags)

        try:
            if media_path and os.path.exists(media_path):
                # Photo post
                with open(media_path, 'rb') as f:
                    resp = requests.post(
                        f'{GRAPH_API}/{self._page_id}/photos',
                        data={
                            'message': text,
                            'access_token': self._page_token,
                        },
                        files={'source': f}
                    )
            else:
                # Text post
                resp = requests.post(
                    f'{GRAPH_API}/{self._page_id}/feed',
                    params={
                        'message': text,
                        'access_token': self._page_token,
                    }
                )

            if resp.status_code == 429:
                raise RateLimitError("Facebook rate limited")
            elif resp.status_code == 401:
                raise AuthError("Facebook auth expired")

            data = resp.json()
            if 'error' in data:
                raise PlatformError(
                    f"Facebook post failed: {data['error'].get('message', '')}",
                    retryable=True
                )

            post_id = data.get('id', data.get('post_id', ''))
            log.info(f"Posted to Facebook: {post_id}")
            return {
                'platform_post_id': str(post_id),
                'url': f"https://www.facebook.com/{post_id}",
            }

        except (RateLimitError, AuthError, PlatformError):
            raise
        except Exception as e:
            raise PlatformError(f"Facebook post failed: {e}", retryable=True)

    def get_metrics(self, platform_post_id):
        """Fetch Facebook post metrics."""
        try:
            self._load_token()

            # Get basic metrics
            resp = requests.get(
                f'{GRAPH_API}/{platform_post_id}',
                params={
                    'fields': 'likes.summary(true),comments.summary(true),shares',
                    'access_token': self._page_token,
                }
            )

            metrics = {'likes': 0, 'shares': 0, 'comments': 0, 'impressions': 0, 'clicks': 0}

            if resp.status_code == 200:
                data = resp.json()
                metrics['likes'] = data.get('likes', {}).get('summary', {}).get('total_count', 0)
                metrics['comments'] = data.get('comments', {}).get('summary', {}).get('total_count', 0)
                metrics['shares'] = data.get('shares', {}).get('count', 0)

            # Get insights (impressions, clicks)
            insights_resp = requests.get(
                f'{GRAPH_API}/{platform_post_id}/insights',
                params={
                    'metric': 'post_impressions,post_clicks',
                    'access_token': self._page_token,
                }
            )

            if insights_resp.status_code == 200:
                for insight in insights_resp.json().get('data', []):
                    name = insight.get('name', '')
                    value = insight.get('values', [{}])[0].get('value', 0)
                    if name == 'post_impressions':
                        metrics['impressions'] = value
                    elif name == 'post_clicks':
                        metrics['clicks'] = value

            return metrics
        except Exception as e:
            log.error(f"Failed to fetch Facebook metrics: {e}")
            return {'likes': 0, 'shares': 0, 'comments': 0, 'impressions': 0, 'clicks': 0}

    def validate_auth(self):
        """Validate Facebook Page authentication."""
        try:
            self._load_token()
            resp = requests.get(
                f'{GRAPH_API}/{self._page_id}',
                params={
                    'fields': 'name,id',
                    'access_token': self._page_token,
                }
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    'valid': True,
                    'account_name': data.get('name', ''),
                    'account_id': data.get('id', ''),
                    'expires_at': None,
                }
        except AuthError:
            pass
        except Exception as e:
            return {
                'valid': False, 'account_name': None, 'account_id': None,
                'expires_at': None, 'error': str(e),
            }

        return {'valid': False, 'account_name': None, 'account_id': None, 'expires_at': None}
