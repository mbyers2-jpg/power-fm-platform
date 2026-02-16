#!/usr/bin/env python3
"""
Instagram platform implementation using Meta Graph API.
Requires a Business or Creator Instagram account connected to a Facebook Page.
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


@register_platform('instagram')
class InstagramPlatform(PlatformBase):
    name = 'instagram'
    display_name = 'Instagram'
    max_post_length = 2200
    supports_threads = False
    supports_images = True

    def __init__(self):
        super().__init__()
        self._token = None
        self._ig_user_id = None

    def _load_token(self):
        """Load Meta OAuth token."""
        if self._token:
            return self._token

        if not os.path.exists(META_TOKEN):
            raise AuthError("Instagram not authenticated. Run: python agent.py --setup instagram")

        with open(META_TOKEN, 'r') as f:
            data = json.load(f)

        self._token = data.get('access_token')
        self._ig_user_id = data.get('instagram_user_id')

        if not self._token or not self._ig_user_id:
            raise AuthError("Invalid Meta token or missing Instagram user ID. Re-run setup.")

        return self._token

    def post(self, body, hashtags='', media_path=None, reply_to=None):
        """
        Post to Instagram.
        Note: Instagram Graph API requires an image for feed posts.
        Text-only posts are not supported. If no media_path is provided,
        the post will fail gracefully.
        """
        self._load_token()
        text = self.format_post(body, hashtags)

        if not media_path or not os.path.exists(media_path):
            raise PlatformError(
                "Instagram requires an image for feed posts. Provide media_path.",
                retryable=False
            )

        try:
            # Step 1: Create media container
            # Image must be publicly accessible URL or uploaded
            # For local files, we need to host them first
            if media_path.startswith('http'):
                image_url = media_path
            else:
                raise PlatformError(
                    "Instagram requires a publicly accessible image URL. "
                    "Upload image to a CDN or use a hosted URL.",
                    retryable=False
                )

            container_resp = requests.post(
                f'{GRAPH_API}/{self._ig_user_id}/media',
                params={
                    'image_url': image_url,
                    'caption': text,
                    'access_token': self._token,
                }
            )

            if container_resp.status_code == 429:
                raise RateLimitError("Instagram rate limited")
            elif container_resp.status_code == 401:
                raise AuthError("Instagram auth expired")

            container_data = container_resp.json()
            if 'error' in container_data:
                raise PlatformError(
                    f"Instagram container creation failed: {container_data['error'].get('message', '')}",
                    retryable=True
                )

            container_id = container_data['id']

            # Step 2: Publish the container
            publish_resp = requests.post(
                f'{GRAPH_API}/{self._ig_user_id}/media_publish',
                params={
                    'creation_id': container_id,
                    'access_token': self._token,
                }
            )

            publish_data = publish_resp.json()
            if 'error' in publish_data:
                raise PlatformError(
                    f"Instagram publish failed: {publish_data['error'].get('message', '')}",
                    retryable=True
                )

            post_id = publish_data['id']
            log.info(f"Posted to Instagram: {post_id}")
            return {
                'platform_post_id': post_id,
                'url': f"https://www.instagram.com/p/{post_id}/",
            }

        except (RateLimitError, AuthError, PlatformError):
            raise
        except Exception as e:
            raise PlatformError(f"Instagram post failed: {e}", retryable=True)

    def get_metrics(self, platform_post_id):
        """Fetch Instagram post metrics."""
        try:
            self._load_token()
            resp = requests.get(
                f'{GRAPH_API}/{platform_post_id}/insights',
                params={
                    'metric': 'impressions,reach,engagement',
                    'access_token': self._token,
                }
            )

            # Also get basic metrics
            media_resp = requests.get(
                f'{GRAPH_API}/{platform_post_id}',
                params={
                    'fields': 'like_count,comments_count',
                    'access_token': self._token,
                }
            )

            metrics = {'likes': 0, 'shares': 0, 'comments': 0, 'impressions': 0, 'clicks': 0}

            if media_resp.status_code == 200:
                media_data = media_resp.json()
                metrics['likes'] = media_data.get('like_count', 0)
                metrics['comments'] = media_data.get('comments_count', 0)

            if resp.status_code == 200:
                insights = resp.json().get('data', [])
                for insight in insights:
                    name = insight.get('name', '')
                    value = insight.get('values', [{}])[0].get('value', 0)
                    if name == 'impressions':
                        metrics['impressions'] = value
                    elif name == 'engagement':
                        metrics['shares'] = value  # Engagement includes saves/shares

            return metrics
        except Exception as e:
            log.error(f"Failed to fetch Instagram metrics: {e}")
            return {'likes': 0, 'shares': 0, 'comments': 0, 'impressions': 0, 'clicks': 0}

    def validate_auth(self):
        """Validate Instagram authentication."""
        try:
            self._load_token()
            resp = requests.get(
                f'{GRAPH_API}/{self._ig_user_id}',
                params={
                    'fields': 'username,name',
                    'access_token': self._token,
                }
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    'valid': True,
                    'account_name': f"@{data.get('username', '')}",
                    'account_id': self._ig_user_id,
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
