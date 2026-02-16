#!/usr/bin/env python3
"""
Twitter OAuth 2.0 authentication.
Supports both OAuth 1.0a (for media uploads) and OAuth 2.0 (for API v2).
"""

import os
import json
import logging

log = logging.getLogger('social-media-agent')

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
TWITTER_CONFIG = os.path.join(CONFIG_DIR, 'twitter_config.json')
TWITTER_TOKEN = os.path.join(CONFIG_DIR, 'twitter_token.json')


def setup_twitter():
    """
    Interactive setup for Twitter API credentials.
    Guides user through creating a Twitter Developer App and entering credentials.
    """
    print("\n=== Twitter / X Setup ===\n")
    print("Prerequisites:")
    print("  1. Go to https://developer.twitter.com/en/portal/dashboard")
    print("  2. Create a Project and App (Free tier is fine)")
    print("  3. Set App permissions to 'Read and Write'")
    print("  4. Generate API Key, API Secret, Bearer Token")
    print("  5. Generate Access Token and Access Token Secret")
    print("     (with Read and Write permissions)")
    print()

    api_key = input("API Key (Consumer Key): ").strip()
    api_secret = input("API Secret (Consumer Secret): ").strip()
    bearer_token = input("Bearer Token: ").strip()
    access_token = input("Access Token: ").strip()
    access_token_secret = input("Access Token Secret: ").strip()

    if not all([api_key, api_secret, access_token, access_token_secret]):
        print("ERROR: All fields are required.")
        return False

    # Save config
    os.makedirs(CONFIG_DIR, exist_ok=True)
    config = {
        'api_key': api_key,
        'api_secret': api_secret,
        'bearer_token': bearer_token,
    }
    with open(TWITTER_CONFIG, 'w') as f:
        json.dump(config, f, indent=2)
    os.chmod(TWITTER_CONFIG, 0o600)

    # Save token
    token = {
        'access_token': access_token,
        'access_token_secret': access_token_secret,
    }
    with open(TWITTER_TOKEN, 'w') as f:
        json.dump(token, f, indent=2)
    os.chmod(TWITTER_TOKEN, 0o600)

    # Validate
    print("\nValidating credentials...")
    try:
        import tweepy
        client = tweepy.Client(
            bearer_token=bearer_token,
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )
        me = client.get_me()
        if me.data:
            print(f"Authenticated as: @{me.data.username} (ID: {me.data.id})")
            print("Twitter setup complete!")
            return True
        else:
            print("WARNING: Could not verify account. Credentials saved but may be invalid.")
            return True
    except ImportError:
        print("tweepy not installed — credentials saved, will validate on first use.")
        return True
    except Exception as e:
        print(f"WARNING: Validation failed: {e}")
        print("Credentials saved. Please verify they are correct.")
        return True
