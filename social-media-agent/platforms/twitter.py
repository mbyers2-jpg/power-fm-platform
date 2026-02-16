#!/usr/bin/env python3
"""
Twitter/X platform implementation using tweepy (API v2).
"""

import os
import json
import logging

from platforms import PlatformBase, PlatformError, RateLimitError, AuthError, register_platform

log = logging.getLogger('social-media-agent')

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
TWITTER_CONFIG = os.path.join(CONFIG_DIR, 'twitter_config.json')
TWITTER_TOKEN = os.path.join(CONFIG_DIR, 'twitter_token.json')


@register_platform('twitter')
class TwitterPlatform(PlatformBase):
    name = 'twitter'
    display_name = 'X / Twitter'
    max_post_length = 280
    supports_threads = True
    supports_images = True

    def __init__(self):
        super().__init__()
        self._client = None

    def _get_client(self):
        """Get authenticated tweepy Client."""
        if self._client:
            return self._client

        try:
            import tweepy
        except ImportError:
            raise PlatformError("tweepy not installed. Run: pip install tweepy")

        if not os.path.exists(TWITTER_TOKEN):
            raise AuthError(
                "Twitter not authenticated. Run: python agent.py --setup twitter"
            )

        with open(TWITTER_TOKEN, 'r') as f:
            token_data = json.load(f)

        access_token = token_data.get('access_token')
        if not access_token:
            raise AuthError("Invalid Twitter token. Re-run setup.")

        # For OAuth 2.0 User Context (PKCE)
        if not os.path.exists(TWITTER_CONFIG):
            raise AuthError("Twitter config not found. Run: python agent.py --setup twitter")

        with open(TWITTER_CONFIG, 'r') as f:
            config = json.load(f)

        # Try OAuth 2.0 Bearer + User Context
        try:
            self._client = tweepy.Client(
                bearer_token=config.get('bearer_token'),
                consumer_key=config.get('api_key'),
                consumer_secret=config.get('api_secret'),
                access_token=token_data.get('access_token'),
                access_token_secret=token_data.get('access_token_secret'),
            )
            return self._client
        except Exception as e:
            raise AuthError(f"Failed to create Twitter client: {e}")

    def post(self, body, hashtags='', media_path=None, reply_to=None):
        """Post a tweet."""
        client = self._get_client()
        text = self.format_post(body, hashtags)

        kwargs = {}
        if reply_to:
            kwargs['in_reply_to_tweet_id'] = reply_to

        # Handle media upload
        media_ids = None
        if media_path and os.path.exists(media_path):
            try:
                import tweepy
                # Media upload requires v1.1 API
                config = json.load(open(TWITTER_CONFIG, 'r'))
                token = json.load(open(TWITTER_TOKEN, 'r'))
                auth = tweepy.OAuth1UserHandler(
                    config['api_key'], config['api_secret'],
                    token['access_token'], token['access_token_secret']
                )
                api = tweepy.API(auth)
                media = api.media_upload(media_path)
                media_ids = [media.media_id]
                kwargs['media_ids'] = media_ids
            except Exception as e:
                log.warning(f"Media upload failed, posting without image: {e}")

        try:
            response = client.create_tweet(text=text, **kwargs)
            tweet_id = str(response.data['id'])
            log.info(f"Posted tweet {tweet_id}")
            return {
                'platform_post_id': tweet_id,
                'url': f"https://x.com/i/status/{tweet_id}",
            }
        except Exception as e:
            error_str = str(e)
            if 'rate limit' in error_str.lower() or '429' in error_str:
                raise RateLimitError(f"Twitter rate limited: {e}")
            elif 'unauthorized' in error_str.lower() or '401' in error_str:
                raise AuthError(f"Twitter auth failed: {e}")
            else:
                raise PlatformError(f"Twitter post failed: {e}", retryable=True)

    def get_metrics(self, platform_post_id):
        """Fetch tweet engagement metrics."""
        client = self._get_client()

        try:
            response = client.get_tweet(
                platform_post_id,
                tweet_fields=['public_metrics']
            )
            metrics = response.data.get('public_metrics', {}) if response.data else {}
            return {
                'likes': metrics.get('like_count', 0),
                'shares': metrics.get('retweet_count', 0),
                'comments': metrics.get('reply_count', 0),
                'impressions': metrics.get('impression_count', 0),
                'clicks': 0,  # Not available in free tier
            }
        except Exception as e:
            log.error(f"Failed to fetch Twitter metrics for {platform_post_id}: {e}")
            return {'likes': 0, 'shares': 0, 'comments': 0, 'impressions': 0, 'clicks': 0}

    def validate_auth(self):
        """Validate Twitter authentication."""
        try:
            client = self._get_client()
            me = client.get_me()
            if me.data:
                return {
                    'valid': True,
                    'account_name': f"@{me.data.username}",
                    'account_id': str(me.data.id),
                    'expires_at': None,
                }
        except Exception as e:
            return {
                'valid': False,
                'account_name': None,
                'account_id': None,
                'expires_at': None,
                'error': str(e),
            }

        return {'valid': False, 'account_name': None, 'account_id': None, 'expires_at': None}
