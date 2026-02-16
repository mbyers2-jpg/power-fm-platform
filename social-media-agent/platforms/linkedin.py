#!/usr/bin/env python3
"""
LinkedIn platform implementation using LinkedIn Share API v2.
"""

import os
import json
import logging
import requests

from platforms import PlatformBase, PlatformError, RateLimitError, AuthError, register_platform

log = logging.getLogger('social-media-agent')

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
LINKEDIN_TOKEN = os.path.join(CONFIG_DIR, 'linkedin_token.json')

API_BASE = 'https://api.linkedin.com/v2'


@register_platform('linkedin')
class LinkedInPlatform(PlatformBase):
    name = 'linkedin'
    display_name = 'LinkedIn'
    max_post_length = 3000
    supports_threads = False
    supports_images = True

    def __init__(self):
        super().__init__()
        self._token = None
        self._person_id = None

    def _load_token(self):
        """Load OAuth token from file."""
        if self._token:
            return self._token

        if not os.path.exists(LINKEDIN_TOKEN):
            raise AuthError("LinkedIn not authenticated. Run: python agent.py --setup linkedin")

        with open(LINKEDIN_TOKEN, 'r') as f:
            data = json.load(f)

        self._token = data.get('access_token')
        self._person_id = data.get('person_id')

        if not self._token:
            raise AuthError("Invalid LinkedIn token. Re-run setup.")

        return self._token

    def _headers(self):
        """Get API request headers."""
        token = self._load_token()
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'X-Restli-Protocol-Version': '2.0.0',
        }

    def post(self, body, hashtags='', media_path=None, reply_to=None):
        """Post to LinkedIn."""
        self._load_token()
        text = self.format_post(body, hashtags)

        payload = {
            'author': f'urn:li:person:{self._person_id}',
            'lifecycleState': 'PUBLISHED',
            'specificContent': {
                'com.linkedin.ugc.ShareContent': {
                    'shareCommentary': {
                        'text': text,
                    },
                    'shareMediaCategory': 'NONE',
                }
            },
            'visibility': {
                'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC',
            }
        }

        # Handle image upload if provided
        if media_path and os.path.exists(media_path):
            try:
                media_urn = self._upload_image(media_path)
                if media_urn:
                    payload['specificContent']['com.linkedin.ugc.ShareContent']['shareMediaCategory'] = 'IMAGE'
                    payload['specificContent']['com.linkedin.ugc.ShareContent']['media'] = [{
                        'status': 'READY',
                        'media': media_urn,
                    }]
            except Exception as e:
                log.warning(f"LinkedIn image upload failed, posting without: {e}")

        try:
            resp = requests.post(f'{API_BASE}/ugcPosts', headers=self._headers(), json=payload)

            if resp.status_code == 429:
                raise RateLimitError("LinkedIn rate limited")
            elif resp.status_code == 401:
                raise AuthError("LinkedIn auth expired. Re-run setup.")
            elif resp.status_code not in (200, 201):
                raise PlatformError(f"LinkedIn post failed ({resp.status_code}): {resp.text}", retryable=True)

            post_id = resp.headers.get('X-RestLi-Id', resp.json().get('id', ''))
            log.info(f"Posted to LinkedIn: {post_id}")
            return {
                'platform_post_id': str(post_id),
                'url': f"https://www.linkedin.com/feed/update/{post_id}/",
            }
        except (RateLimitError, AuthError, PlatformError):
            raise
        except Exception as e:
            raise PlatformError(f"LinkedIn post failed: {e}", retryable=True)

    def _upload_image(self, media_path):
        """Upload an image to LinkedIn and return the media URN."""
        # Step 1: Register upload
        register_payload = {
            'registerUploadRequest': {
                'recipes': ['urn:li:digitalmediaRecipe:feedshare-image'],
                'owner': f'urn:li:person:{self._person_id}',
                'serviceRelationships': [{
                    'relationshipType': 'OWNER',
                    'identifier': 'urn:li:userGeneratedContent',
                }]
            }
        }
        resp = requests.post(
            f'{API_BASE}/assets?action=registerUpload',
            headers=self._headers(), json=register_payload
        )
        if resp.status_code != 200:
            return None

        upload_data = resp.json()['value']
        upload_url = upload_data['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
        asset = upload_data['asset']

        # Step 2: Upload binary
        with open(media_path, 'rb') as f:
            headers = self._headers()
            headers['Content-Type'] = 'application/octet-stream'
            resp = requests.put(upload_url, headers=headers, data=f)

        if resp.status_code in (200, 201):
            return asset
        return None

    def get_metrics(self, platform_post_id):
        """Fetch LinkedIn post metrics."""
        try:
            resp = requests.get(
                f'{API_BASE}/socialActions/{platform_post_id}',
                headers=self._headers()
            )
            if resp.status_code != 200:
                return {'likes': 0, 'shares': 0, 'comments': 0, 'impressions': 0, 'clicks': 0}

            data = resp.json()
            return {
                'likes': data.get('likesSummary', {}).get('totalLikes', 0),
                'shares': data.get('sharesSummary', {}).get('totalShares', 0) if 'sharesSummary' in data else 0,
                'comments': data.get('commentsSummary', {}).get('totalFirstLevelComments', 0),
                'impressions': 0,  # Requires organization analytics API
                'clicks': 0,
            }
        except Exception as e:
            log.error(f"Failed to fetch LinkedIn metrics: {e}")
            return {'likes': 0, 'shares': 0, 'comments': 0, 'impressions': 0, 'clicks': 0}

    def validate_auth(self):
        """Validate LinkedIn authentication."""
        try:
            token = self._load_token()
            resp = requests.get(f'{API_BASE}/me', headers=self._headers())

            if resp.status_code == 200:
                data = resp.json()
                name = f"{data.get('localizedFirstName', '')} {data.get('localizedLastName', '')}".strip()
                return {
                    'valid': True,
                    'account_name': name,
                    'account_id': data.get('id'),
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
