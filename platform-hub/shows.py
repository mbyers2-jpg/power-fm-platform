#!/usr/bin/env python3
"""
Power FM DJ Show System

Maps virtual DJ personalities to the 6 schedule blocks defined in scheduler.py.
Each DJ has a unique voice (ElevenLabs), style, and show identity. Provides
show lookup by time, schedule display, and automated intro generation.

Usage:
    venv/bin/python shows.py                    # Show schedule
    venv/bin/python shows.py --generate-intros  # Generate all DJ show intros via ElevenLabs
    venv/bin/python shows.py --current          # Show what's on right now
"""

import argparse
import logging
import os
import sys
from datetime import datetime

log = logging.getLogger('platform-hub')

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELEVENLABS_OUTPUT = os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'output')


# ---------------------------------------------------------------------------
# DJ Profiles
# ---------------------------------------------------------------------------

DJS = {
    'dj_nova': {
        'name': 'DJ Nova',
        'bio': 'High-energy morning host bringing the heat since day one.',
        'voice': 'Charlie',
        'style': 'energetic',
    },
    'dj_silk': {
        'name': 'DJ Silk',
        'bio': 'Smooth late-night vibes and R&B classics.',
        'voice': 'Lily',
        'style': 'smooth',
    },
    'dj_blaze': {
        'name': 'DJ Blaze',
        'bio': 'Afternoon drive specialist. Peak energy, peak hits.',
        'voice': 'Adam',
        'style': 'hype',
    },
    'mc_culture': {
        'name': 'MC Culture',
        'bio': 'The voice of the culture. Midday mix master.',
        'voice': 'Brian',
        'style': 'authoritative',
    },
    'dj_phantom': {
        'name': 'DJ Phantom',
        'bio': 'Deep cuts and underground heat. The overnight selector.',
        'voice': 'Daniel',
        'style': 'chill',
    },
}


# ---------------------------------------------------------------------------
# Show Schedule — maps DJs to scheduler.py SCHEDULE blocks
# ---------------------------------------------------------------------------

SHOWS = {
    'morning_power_hour': {
        'label': 'The Morning Power Hour',
        'dj': 'dj_nova',
        'time': '6am-10am',
        'tagline': 'Wake up and get locked in!',
        'intro_text': "Good morning! You're locked in to The Morning Power Hour with DJ Nova on Power FM! Let's get this energy right!",
    },
    'midday_mix': {
        'label': 'The Midday Mix',
        'dj': 'mc_culture',
        'time': '10am-3pm',
        'tagline': 'Culture on rotation.',
        'intro_text': "It's The Midday Mix with MC Culture on Power FM. The culture. The music. The movement. Let's go.",
    },
    'afternoon_drive': {
        'label': 'Afternoon Drive',
        'dj': 'dj_blaze',
        'time': '3pm-7pm',
        'tagline': 'Peak hours. Peak hits.',
        'intro_text': "DJ Blaze here! It's Afternoon Drive on Power FM. Strap in, we're turning it up for the ride home!",
    },
    'evening_vibes': {
        'label': 'Evening Vibes',
        'dj': 'dj_silk',
        'time': '7pm-9pm',
        'tagline': 'Slow it down. Feel the music.',
        'intro_text': "Evening Vibes with DJ Silk on Power FM. Sit back, relax, and let the music take you somewhere.",
    },
    'late_night': {
        'label': 'Late Night Sessions',
        'dj': 'dj_silk',
        'time': '9pm-12am',
        'tagline': 'After dark. Deep cuts only.',
        'intro_text': "Late Night Sessions with DJ Silk. Slow jams, deep cuts, and vibes only. Power FM after dark.",
    },
    'overnight': {
        'label': 'The Overnight',
        'dj': 'dj_phantom',
        'time': '12am-6am',
        'tagline': 'Auto-pilot. Underground heat.',
        'intro_text': "DJ Phantom on the overnight shift. Power FM never sleeps. Underground heat till sunrise.",
    },
}

# Hour-range mapping for each show block (matching scheduler.py SCHEDULE)
_HOUR_RANGES = {
    'morning_power_hour': (6, 10),
    'midday_mix': (10, 15),
    'afternoon_drive': (15, 19),
    'evening_vibes': (19, 21),
    'late_night': (21, 24),
    'overnight': (0, 6),
}


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def get_current_show():
    """
    Return the current show dict based on datetime.now().hour.

    Maps the current hour to one of the 6 schedule blocks and returns
    a dict with show info, DJ profile, and live status.

    Returns:
        dict with keys: show_key, label, time, tagline, intro_text,
                        dj (full DJ profile dict), is_live (True)
    """
    now = datetime.now()
    current_hour = now.hour

    for show_key, (start, end) in _HOUR_RANGES.items():
        if end == 24:
            # Block 21:00-00:00 => hour >= 21
            if current_hour >= start:
                return _build_show_info(show_key, is_live=True)
        elif start < end:
            if start <= current_hour < end:
                return _build_show_info(show_key, is_live=True)
        else:
            # Wrap-around block (0-6)
            if current_hour >= start or current_hour < end:
                return _build_show_info(show_key, is_live=True)

    # Fallback to overnight
    return _build_show_info('overnight', is_live=True)


def get_show_schedule():
    """
    Return all shows with their DJs, times, and whether they're currently live.

    Returns:
        list of dicts, each containing: show_key, label, time, tagline,
        intro_text, dj (full DJ profile), is_live (bool)
    """
    current = get_current_show()
    current_key = current['show_key']

    schedule = []
    for show_key in SHOWS:
        is_live = (show_key == current_key)
        schedule.append(_build_show_info(show_key, is_live=is_live))

    return schedule


def generate_show_intros(conn):
    """
    Generate DJ show intro audio files via ElevenLabs API for all shows
    that don't already have intros.

    Saves to ~/Agents/elevenlabs-agent/output/ with filenames like:
        DJ_Nova_Morning_Power_Hour_intro_20260216.mp3

    Uses each DJ's assigned ElevenLabs voice.

    Args:
        conn: Database connection (passed to ElevenLabs cmd_generate for
              logging generations).

    Returns:
        dict mapping show_key to output file path (or None if skipped/failed).
    """
    # Import the ElevenLabs agent functions
    sys.path.insert(0, os.path.join(AGENTS_DIR, 'elevenlabs-agent'))
    from agent import get_client, cmd_generate

    client = get_client()
    if not client:
        print("\nERROR: Could not initialize ElevenLabs client.")
        print("See ~/Agents/elevenlabs-agent/SETUP.md for configuration.")
        return {}

    os.makedirs(ELEVENLABS_OUTPUT, exist_ok=True)
    today = datetime.now().strftime('%Y%m%d')
    results = {}

    for show_key, show in SHOWS.items():
        dj_key = show['dj']
        dj = DJS[dj_key]
        dj_name_clean = dj['name'].replace(' ', '_')
        show_label_clean = show['label'].replace(' ', '_').replace("'", '')

        # Build the expected filename pattern for today
        filename_prefix = f"{dj_name_clean}_{show_label_clean}_intro_{today}"

        # Check if an intro already exists for today
        existing = _find_existing_intro(filename_prefix)
        if existing:
            print(f"  [SKIP] {show['label']} — intro already exists: {os.path.basename(existing)}")
            results[show_key] = existing
            continue

        # Generate the intro using the DJ's assigned voice
        voice_name = dj['voice']
        intro_text = show['intro_text']

        print(f"\n  [GEN] {show['label']} — voice: {voice_name}")
        output_path = cmd_generate(client, conn, intro_text, voice_name)

        if output_path and os.path.isfile(output_path):
            # Rename to our standardized filename
            new_filename = f"{filename_prefix}.mp3"
            new_path = os.path.join(ELEVENLABS_OUTPUT, new_filename)

            # If the generated file is already in the output dir, rename it
            if os.path.dirname(os.path.abspath(output_path)) == os.path.abspath(ELEVENLABS_OUTPUT):
                os.rename(output_path, new_path)
            else:
                # Copy from wherever it was generated
                import shutil
                shutil.copy2(output_path, new_path)

            results[show_key] = new_path
            print(f"  [OK]  Saved: {new_filename}")
        else:
            results[show_key] = None
            print(f"  [FAIL] Could not generate intro for {show['label']}")

    return results


def show_schedule_display():
    """Print formatted show schedule table to terminal."""
    now = datetime.now()
    now_str = now.strftime('%H:%M')
    current = get_current_show()

    print(f"\n{'=' * 78}")
    print(f"  POWER FM DJ SHOW SCHEDULE ({now_str})")
    print(f"{'=' * 78}\n")

    print(f"  {'Show':<28} {'DJ':<16} {'Time':<14} {'Style':<14} {'Status'}")
    print(f"  {'-' * 74}")

    for show_key, show in SHOWS.items():
        dj = DJS[show['dj']]
        is_live = (show_key == current['show_key'])
        marker = " << LIVE" if is_live else ""
        print(f"  {show['label']:<28} {dj['name']:<16} {show['time']:<14} {dj['style']:<14}{marker}")

    print()
    print(f"  NOW PLAYING:  {current['label']}")
    print(f"  DJ:           {current['dj']['name']}")
    print(f"  Tagline:      {current['tagline']}")
    print()

    # Show DJ bios
    print(f"  {'DJ PROFILES':}")
    print(f"  {'-' * 74}")
    shown_djs = set()
    for show_key, show in SHOWS.items():
        dj_key = show['dj']
        if dj_key not in shown_djs:
            dj = DJS[dj_key]
            print(f"  {dj['name']:<16} {dj['bio']}")
            print(f"  {'':16} Voice: {dj['voice']}  |  Style: {dj['style']}")
            print()
            shown_djs.add(dj_key)

    print(f"{'=' * 78}\n")


def get_show_for_api():
    """
    Return current show info as a dict for JSON API responses.

    Returns:
        dict suitable for JSON serialization with current show details
        and full schedule.
    """
    current = get_current_show()
    schedule = get_show_schedule()

    return {
        'current_show': current,
        'schedule': schedule,
        'timestamp': datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_show_info(show_key, is_live=False):
    """Build a complete show info dict from a show key."""
    show = SHOWS[show_key]
    dj_key = show['dj']
    dj = DJS[dj_key].copy()
    dj['key'] = dj_key

    return {
        'show_key': show_key,
        'label': show['label'],
        'time': show['time'],
        'tagline': show['tagline'],
        'intro_text': show['intro_text'],
        'dj': dj,
        'is_live': is_live,
    }


def _find_existing_intro(filename_prefix):
    """
    Check if an intro file with the given prefix already exists
    in the ElevenLabs output directory.

    Args:
        filename_prefix: e.g. 'DJ_Nova_The_Morning_Power_Hour_intro_20260216'

    Returns:
        Full path to existing file, or None.
    """
    if not os.path.isdir(ELEVENLABS_OUTPUT):
        return None

    for fname in os.listdir(ELEVENLABS_OUTPUT):
        if fname.startswith(filename_prefix) and fname.endswith('.mp3'):
            return os.path.join(ELEVENLABS_OUTPUT, fname)

    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Power FM DJ Show System')
    parser.add_argument('--generate-intros', action='store_true',
                        help='Generate all DJ show intros via ElevenLabs')
    parser.add_argument('--current', action='store_true',
                        help="Show what's on right now")
    args = parser.parse_args()

    if args.current:
        current = get_current_show()
        now_str = datetime.now().strftime('%H:%M')
        print(f"\n  POWER FM — NOW PLAYING ({now_str})")
        print(f"  {'=' * 50}")
        print(f"  Show:     {current['label']}")
        print(f"  DJ:       {current['dj']['name']}")
        print(f"  Time:     {current['time']}")
        print(f"  Tagline:  {current['tagline']}")
        print(f"  Style:    {current['dj']['style']}")
        print(f"  Voice:    {current['dj']['voice']}")
        print(f"  Bio:      {current['dj']['bio']}")
        print()
        return

    if args.generate_intros:
        # Need an ElevenLabs database connection for generation logging
        sys.path.insert(0, os.path.join(AGENTS_DIR, 'elevenlabs-agent'))
        from database import get_connection
        conn = get_connection()

        print(f"\n  POWER FM — Generating DJ Show Intros")
        print(f"  {'=' * 50}")
        print(f"  Output: {ELEVENLABS_OUTPUT}")
        print()

        try:
            results = generate_show_intros(conn)
        finally:
            conn.close()

        generated = sum(1 for v in results.values() if v is not None)
        print(f"\n  {generated}/{len(SHOWS)} show intros ready.")
        print()
        return

    # Default: show full schedule
    show_schedule_display()


if __name__ == '__main__':
    main()
