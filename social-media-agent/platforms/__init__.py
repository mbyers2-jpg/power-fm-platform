#!/usr/bin/env python3
"""
Platform base class and registry for Social Media Agent.
Each platform implements post(), delete(), get_metrics(), and validate_auth().
"""

import logging
from abc import ABC, abstractmethod

log = logging.getLogger('social-media-agent')

# Platform registry
_PLATFORMS = {}


def register_platform(name):
    """Decorator to register a platform implementation."""
    def wrapper(cls):
        _PLATFORMS[name] = cls
        return cls
    return wrapper


def get_platform(name):
    """Get a platform implementation by name."""
    cls = _PLATFORMS.get(name)
    if not cls:
        raise ValueError(f"Unknown platform: {name}. Available: {list(_PLATFORMS.keys())}")
    return cls()


def get_available_platforms():
    """Get list of registered platform names."""
    return list(_PLATFORMS.keys())


class PlatformBase(ABC):
    """Base class for social media platform implementations."""

    name = 'base'
    display_name = 'Base Platform'
    max_post_length = 10000
    supports_threads = False
    supports_images = False

    def __init__(self):
        self.config_dir = None
        self._authenticated = False

    @abstractmethod
    def post(self, body, hashtags='', media_path=None, reply_to=None):
        """
        Post content to the platform.

        Args:
            body: Post text content
            hashtags: Space-separated hashtags to append
            media_path: Optional path to media file
            reply_to: Optional platform post ID to reply to (for threads)

        Returns:
            dict with 'platform_post_id' and 'url' on success

        Raises:
            PlatformError on failure
        """
        pass

    @abstractmethod
    def get_metrics(self, platform_post_id):
        """
        Fetch engagement metrics for a post.

        Args:
            platform_post_id: The platform's ID for the post

        Returns:
            dict with keys: likes, shares, comments, impressions, clicks
        """
        pass

    @abstractmethod
    def validate_auth(self):
        """
        Validate current authentication.

        Returns:
            dict with 'valid' (bool), 'account_name', 'account_id', 'expires_at'
        """
        pass

    def format_post(self, body, hashtags=''):
        """Format body + hashtags for posting, respecting character limits."""
        if hashtags:
            full_text = f"{body}\n\n{hashtags}"
        else:
            full_text = body

        if len(full_text) > self.max_post_length:
            # Truncate body, keep hashtags
            available = self.max_post_length - len(hashtags) - 5  # 5 for "\n\n" + "..."
            full_text = f"{body[:available]}...\n\n{hashtags}"

        return full_text

    def post_thread(self, tweets):
        """
        Post a thread (series of connected posts).
        Default implementation posts sequentially with reply_to chaining.

        Args:
            tweets: List of dicts with 'body' and optional 'hashtags', 'media_path'

        Returns:
            List of result dicts from post()
        """
        if not self.supports_threads:
            raise PlatformError(f"{self.display_name} does not support threads")

        results = []
        reply_to = None

        for tweet in tweets:
            result = self.post(
                body=tweet['body'],
                hashtags=tweet.get('hashtags', ''),
                media_path=tweet.get('media_path'),
                reply_to=reply_to,
            )
            results.append(result)
            reply_to = result.get('platform_post_id')

        return results


class PlatformError(Exception):
    """Raised when a platform operation fails."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class RateLimitError(PlatformError):
    """Raised when rate limited by platform API."""

    def __init__(self, message, retry_after=None):
        super().__init__(message, retryable=True)
        self.retry_after = retry_after


class AuthError(PlatformError):
    """Raised when authentication fails or token is expired."""

    def __init__(self, message):
        super().__init__(message, retryable=False)
