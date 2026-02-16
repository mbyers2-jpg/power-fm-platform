#!/usr/bin/env python3
"""
Setup wizard for Social Media Agent platform authentication.
"""

import sys
import logging

log = logging.getLogger('social-media-agent')

PLATFORMS = {
    'twitter': {
        'name': 'Twitter / X',
        'setup': 'auth.twitter_auth.setup_twitter',
    },
    'linkedin': {
        'name': 'LinkedIn',
        'setup': 'auth.linkedin_auth.setup_linkedin',
    },
    'facebook': {
        'name': 'Facebook + Instagram (Meta)',
        'setup': 'auth.meta_auth.setup_meta',
    },
    'instagram': {
        'name': 'Instagram (via Meta)',
        'setup': 'auth.meta_auth.setup_meta',
    },
}


def run_setup(platform):
    """Run the setup wizard for a specific platform."""
    platform = platform.lower().strip()

    # Normalize platform name
    if platform in ('x', 'x/twitter'):
        platform = 'twitter'
    elif platform in ('fb',):
        platform = 'facebook'
    elif platform in ('ig',):
        platform = 'instagram'

    if platform not in PLATFORMS:
        print(f"Unknown platform: {platform}")
        print(f"Available: {', '.join(PLATFORMS.keys())}")
        return False

    info = PLATFORMS[platform]
    print(f"\nSetting up {info['name']}...")

    # Instagram and Facebook share Meta auth
    if platform in ('facebook', 'instagram'):
        from auth.meta_auth import setup_meta
        return setup_meta()
    elif platform == 'twitter':
        from auth.twitter_auth import setup_twitter
        return setup_twitter()
    elif platform == 'linkedin':
        from auth.linkedin_auth import setup_linkedin
        return setup_linkedin()

    return False


def run_setup_all():
    """Run setup for all platforms interactively."""
    print("\n=== Social Media Agent - Platform Setup ===\n")
    print("This wizard will help you set up authentication for each platform.")
    print("You can skip any platform and set it up later.\n")

    for platform, info in PLATFORMS.items():
        if platform == 'instagram':
            continue  # Covered by Meta setup

        answer = input(f"Set up {info['name']}? (y/n): ").strip().lower()
        if answer in ('y', 'yes'):
            run_setup(platform)
            print()

    print("\nSetup complete! Use 'python agent.py --status' to check platform connections.")
