#!/usr/bin/env python3
"""
ElevenLabs Voice Generation Agent for Power FM
Generates station IDs, ad reads, show intros in multiple voices/languages.
Manages voice library and tracks audio asset generation.

Usage:
    venv/bin/python agent.py --voices                    # List available voices
    venv/bin/python agent.py --models                    # List available models
    venv/bin/python agent.py --generate "text" --voice "name"  # Generate audio
    venv/bin/python agent.py --station-id "Power 106 LA" # Generate station ID
    venv/bin/python agent.py --report                    # Generate localization report
    venv/bin/python agent.py --daemon                    # Run continuously
"""

import sys
import os
import re
import json
import time
import signal
import logging
import argparse
from datetime import datetime, timedelta

# --- Configuration ---
POLL_INTERVAL = 300  # 5 minutes — daemon checks for queued generations
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')

# --- Logging Setup ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('elevenlabs-agent')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def safe_filename(text, max_len=50):
    """Generate a filesystem-safe filename from text."""
    # Take first N chars, replace non-alphanumeric with underscores
    clean = re.sub(r'[^a-zA-Z0-9\s-]', '', text[:max_len])
    clean = re.sub(r'\s+', '_', clean.strip())
    return clean or 'audio'


def get_client():
    """
    Initialize and return the ElevenLabs API client.
    Returns None with a helpful message if not configured.
    """
    try:
        from api_client import ElevenLabsClient, ElevenLabsConfigError
        client = ElevenLabsClient()
        return client
    except ElevenLabsConfigError as e:
        log.error(f"Configuration error: {e}")
        print(f"\nERROR: {e}")
        print("\nTo set up the ElevenLabs agent:")
        print("  1. Get an API key from https://elevenlabs.io/app/settings/api-keys")
        print("  2. Create config/elevenlabs_config.json with:")
        print('     {"api_key": "your-key-here"}')
        print("  3. See SETUP.md for full instructions.")
        return None
    except ImportError as e:
        log.error(f"Missing dependency: {e}")
        print(f"\nERROR: {e}")
        print("Run: pip install requests")
        return None


def sync_voices(client, conn):
    """Sync voices from the API into the local database."""
    from database import save_voice

    log.info("Syncing voices from ElevenLabs API...")
    try:
        voices = client.get_voices()
    except Exception as e:
        log.error(f"Failed to fetch voices: {e}")
        return 0

    count = 0
    for v in voices:
        labels = v.get('labels', {})
        language = labels.get('language', labels.get('accent', ''))
        description_parts = []
        if labels.get('description'):
            description_parts.append(labels['description'])
        if labels.get('age'):
            description_parts.append(labels['age'])
        if labels.get('gender'):
            description_parts.append(labels['gender'])
        if labels.get('use_case'):
            description_parts.append(labels['use_case'])

        voice_data = {
            'voice_id': v['voice_id'],
            'name': v.get('name', ''),
            'category': v.get('category', ''),
            'language': language,
            'description': ', '.join(description_parts) if description_parts else '',
            'preview_url': v.get('preview_url', ''),
        }
        save_voice(conn, voice_data)
        count += 1

    log.info(f"Synced {count} voices")
    return count


def cmd_voices(client, conn):
    """List all available voices (--voices)."""
    from database import get_all_voices

    # Always sync from API first
    sync_voices(client, conn)

    voices = get_all_voices(conn)
    if not voices:
        print("No voices found.")
        return

    print(f"\n{'Name':<30} {'Category':<15} {'Language':<15} {'Voice ID'}")
    print("-" * 90)
    for v in voices:
        print(f"{v['name']:<30} {v['category'] or '':<15} {v['language'] or '':<15} {v['voice_id']}")
    print(f"\nTotal: {len(voices)} voices")


def cmd_models(client):
    """List available models (--models)."""
    log.info("Fetching available models...")
    try:
        models = client.get_models()
    except Exception as e:
        log.error(f"Failed to fetch models: {e}")
        print(f"ERROR: {e}")
        return

    print(f"\n{'Model ID':<40} {'Name':<35} {'Languages'}")
    print("-" * 100)
    for m in models:
        langs = [lang.get('language_id', '') for lang in m.get('languages', [])]
        lang_str = ', '.join(langs[:5])
        if len(langs) > 5:
            lang_str += f' (+{len(langs) - 5} more)'
        print(f"{m['model_id']:<40} {m.get('name', ''):<35} {lang_str}")
    print(f"\nTotal: {len(models)} models")


def resolve_voice(client, conn, voice_name):
    """
    Resolve a voice name to a voice_id.
    First checks the local DB, then syncs from API if not found.
    Returns (voice_id, voice_name) tuple or (None, None) if not found.
    """
    from database import get_voice_by_name

    # Try local DB first
    voice = get_voice_by_name(conn, voice_name)
    if voice:
        return voice['voice_id'], voice['name']

    # Sync from API and try again
    sync_voices(client, conn)
    voice = get_voice_by_name(conn, voice_name)
    if voice:
        return voice['voice_id'], voice['name']

    return None, None


def cmd_generate(client, conn, text, voice_name, model_id=None):
    """Generate audio from text with specified voice (--generate)."""
    from database import save_generation, log_usage, update_generation_status

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Resolve voice
    voice_id, resolved_name = resolve_voice(client, conn, voice_name)
    if not voice_id:
        print(f"\nERROR: Voice '{voice_name}' not found.")
        print("Use --voices to see available voices.")
        return None

    model = model_id or 'eleven_multilingual_v2'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{safe_filename(resolved_name)}_{safe_filename(text)}_{timestamp}.mp3"
    output_path = os.path.join(OUTPUT_DIR, filename)

    # Record the pending generation
    gen_data = {
        'voice_id': voice_id,
        'text': text,
        'model_id': model,
        'character_count': len(text),
        'status': 'pending',
    }
    gen_id = save_generation(conn, gen_data)

    log.info(f"Generating audio: voice={resolved_name}, model={model}, chars={len(text)}")
    print(f"\nGenerating audio...")
    print(f"  Voice: {resolved_name} ({voice_id})")
    print(f"  Model: {model}")
    print(f"  Text: {text[:80]}{'...' if len(text) > 80 else ''}")
    print(f"  Characters: {len(text)}")

    try:
        audio_bytes = client.generate_audio(text, voice_id, model_id=model)
    except Exception as e:
        log.error(f"Generation failed: {e}")
        update_generation_status(conn, gen_id, 'failed')
        print(f"\nERROR: Generation failed: {e}")
        return None

    # Save audio file
    with open(output_path, 'wb') as f:
        f.write(audio_bytes)

    # Estimate duration (MP3 at 128kbps: bytes / (128000/8) = seconds)
    estimated_duration = round(len(audio_bytes) / 16000, 1)

    update_generation_status(conn, gen_id, 'completed', output_path, estimated_duration)
    log_usage(conn, len(text), 1)

    log.info(f"Audio saved: {output_path} ({estimated_duration}s, {len(audio_bytes)} bytes)")
    print(f"\n  Output: {output_path}")
    print(f"  Size: {len(audio_bytes):,} bytes")
    print(f"  Est. duration: {estimated_duration}s")
    print("  Status: completed")

    return output_path


def cmd_station_id(client, conn, station_name, voice_name=None, language='en', market=None):
    """Generate a station ID audio clip (--station-id)."""
    from database import save_station_id

    # Build the station ID text
    text = f"You're listening to {station_name}"
    if not voice_name:
        voice_name = 'Rachel'  # Default — a popular ElevenLabs voice

    # Parse market from station name if not provided
    if not market:
        # Try to extract market from patterns like "Power 106 LA" or "Hot 97 NYC"
        parts = station_name.strip().split()
        if len(parts) >= 2:
            market = parts[-1] if len(parts[-1]) <= 4 else ''
        else:
            market = ''

    log.info(f"Generating station ID: {station_name} (voice={voice_name}, lang={language})")

    output_path = cmd_generate(client, conn, text, voice_name)
    if not output_path:
        return None

    # Get the generation ID from the most recent generation
    from database import get_recent_generations
    recent = get_recent_generations(conn, limit=1)
    gen_id = recent[0]['id'] if recent else None

    # Resolve voice_id for the station_ids record
    voice_id, _ = resolve_voice(client, conn, voice_name)

    station_data = {
        'generation_id': gen_id,
        'station_name': station_name,
        'market': market,
        'language': language,
        'voice_id': voice_id or '',
        'output_path': output_path,
    }
    save_station_id(conn, station_data)

    print(f"\n  Station ID recorded for: {station_name}")
    if market:
        print(f"  Market: {market}")
    print(f"  Language: {language}")

    return output_path


def cmd_report(conn):
    """Generate localization report (--report)."""
    from database import (
        get_stats, get_recent_generations, get_station_ids,
        get_ad_reads, get_all_voices, get_usage_today
    )

    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'localization_{today}.md')

    stats = get_stats(conn)
    recent_gens = get_recent_generations(conn, limit=20)
    station_ids = get_station_ids(conn)
    ad_reads = get_ad_reads(conn)
    voices = get_all_voices(conn)
    usage_today = get_usage_today(conn)

    # Count generations per voice for the voice library table
    voice_gen_counts = {}
    for v in voices:
        row = conn.execute(
            "SELECT COUNT(*) FROM generations WHERE voice_id = ?",
            (v['voice_id'],)
        ).fetchone()
        voice_gen_counts[v['voice_id']] = row[0] if row else 0

    lines = [
        f"# Localization Report -- {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Overview",
        f"- Total voices available: {stats['total_voices']}",
        f"- Total generations: {stats['total_generations']}",
        f"- Station IDs generated: {stats['total_station_ids']}",
        f"- Ad reads generated: {stats['total_ad_reads']}",
        f"- Characters used today: {usage_today['characters_used']}",
        f"- Characters used (all time): {stats['chars_total']}",
        "",
    ]

    # Recent Generations
    lines.append("## Recent Generations")
    if recent_gens:
        lines.append("")
        lines.append("| Date | Voice | Text (preview) | Duration | Status |")
        lines.append("|------|-------|-----------------|----------|--------|")
        for g in recent_gens:
            date_str = g['created_at'][:16] if g['created_at'] else ''
            voice_name = g['voice_name'] or g['voice_id'][:12]
            text_preview = g['text'][:40] + '...' if len(g['text']) > 40 else g['text']
            duration = f"{g['duration_seconds']}s" if g['duration_seconds'] else '-'
            lines.append(f"| {date_str} | {voice_name} | {text_preview} | {duration} | {g['status']} |")
    else:
        lines.append("")
        lines.append("No generations yet.")
    lines.append("")

    # Station IDs
    lines.append("## Station IDs")
    if station_ids:
        lines.append("")
        lines.append("| Station | Market | Voice | Language | File |")
        lines.append("|---------|--------|-------|----------|------|")
        for s in station_ids:
            voice_name = s['voice_name'] or s['voice_id'][:12] if s['voice_id'] else '-'
            filename = os.path.basename(s['output_path']) if s['output_path'] else '-'
            lines.append(
                f"| {s['station_name']} | {s['market'] or '-'} | "
                f"{voice_name} | {s['language'] or 'en'} | {filename} |"
            )
    else:
        lines.append("")
        lines.append("No station IDs generated yet.")
    lines.append("")

    # Ad Reads
    lines.append("## Ad Reads")
    if ad_reads:
        lines.append("")
        lines.append("| Advertiser | Campaign | Voice | Duration | File |")
        lines.append("|------------|----------|-------|----------|------|")
        for a in ad_reads:
            voice_name = a['voice_name'] or a['voice_id'][:12] if a['voice_id'] else '-'
            duration = f"{a['duration_seconds']}s" if a['duration_seconds'] else '-'
            filename = os.path.basename(a['output_path']) if a['output_path'] else '-'
            lines.append(
                f"| {a['advertiser'] or '-'} | {a['campaign'] or '-'} | "
                f"{voice_name} | {duration} | {filename} |"
            )
    else:
        lines.append("")
        lines.append("No ad reads generated yet.")
    lines.append("")

    # Voice Library
    lines.append("## Voice Library")
    if voices:
        lines.append("")
        lines.append("| Name | Category | Language | Generations |")
        lines.append("|------|----------|----------|-------------|")
        for v in voices:
            gen_count = voice_gen_counts.get(v['voice_id'], 0)
            lines.append(
                f"| {v['name']} | {v['category'] or '-'} | "
                f"{v['language'] or '-'} | {gen_count} |"
            )
    else:
        lines.append("")
        lines.append("No voices synced yet. Run with --voices to sync from API.")
    lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Report generated: {report_path}")
    print(f"\nLocalization report saved to: {report_path}")
    return report_path


def process_queued_generations(client, conn):
    """Process any queued (pending) generations in the database."""
    from database import update_generation_status, log_usage

    pending = conn.execute(
        "SELECT * FROM generations WHERE status = 'pending' ORDER BY created_at ASC"
    ).fetchall()

    if not pending:
        return 0

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    processed = 0

    for gen in pending:
        if not running:
            break

        log.info(f"Processing queued generation {gen['id']}: {gen['text'][:60]}")

        try:
            audio_bytes = client.generate_audio(
                gen['text'],
                gen['voice_id'],
                model_id=gen['model_id']
            )

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"gen_{gen['id']}_{timestamp}.mp3"
            output_path = os.path.join(OUTPUT_DIR, filename)

            with open(output_path, 'wb') as f:
                f.write(audio_bytes)

            estimated_duration = round(len(audio_bytes) / 16000, 1)
            update_generation_status(conn, gen['id'], 'completed', output_path, estimated_duration)
            log_usage(conn, gen['character_count'] or len(gen['text']), 1)
            processed += 1

            log.info(f"Generation {gen['id']} completed: {output_path}")

        except Exception as e:
            log.error(f"Generation {gen['id']} failed: {e}")
            update_generation_status(conn, gen['id'], 'failed')

    return processed


def run_daemon(client, conn):
    """Continuous loop: sync voices, process queued generations, generate periodic reports."""
    from database import get_agent_state, set_agent_state

    log.info("ElevenLabs agent starting in daemon mode (Ctrl+C to stop)")
    log.info(f"Polling every {POLL_INTERVAL} seconds")

    # Initial voice sync
    sync_voices(client, conn)

    # Initial report
    cmd_report(conn)
    set_agent_state(conn, 'last_report_timestamp', datetime.utcnow().isoformat())

    while running:
        # Process any queued generations
        processed = process_queued_generations(client, conn)
        if processed > 0:
            log.info(f"Processed {processed} queued generation(s)")

        # Re-sync voices periodically (every 6 hours)
        last_sync = get_agent_state(conn, 'last_voice_sync')
        if not last_sync or (
            datetime.utcnow() - datetime.fromisoformat(last_sync)
        ) > timedelta(hours=6):
            sync_voices(client, conn)
            set_agent_state(conn, 'last_voice_sync', datetime.utcnow().isoformat())

        # Regenerate report every hour
        last_report = get_agent_state(conn, 'last_report_timestamp')
        if not last_report or (
            datetime.utcnow() - datetime.fromisoformat(last_report)
        ) > timedelta(hours=1):
            cmd_report(conn)
            set_agent_state(conn, 'last_report_timestamp', datetime.utcnow().isoformat())

        # Sleep in 1-second increments so we can catch shutdown signals
        log.info(f"Sleeping {POLL_INTERVAL}s until next cycle...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

    log.info("ElevenLabs agent stopped.")


def main():
    parser = argparse.ArgumentParser(
        description='ElevenLabs Voice Generation Agent for Power FM',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --voices                         List all available voices
  %(prog)s --models                         List available TTS models
  %(prog)s --generate "Hello world" --voice "Rachel"   Generate audio
  %(prog)s --station-id "Power 106 LA"      Generate station ID
  %(prog)s --station-id "Power FM" --voice "Adam"      Station ID with specific voice
  %(prog)s --report                         Generate localization report
  %(prog)s --daemon                         Run as background daemon
        """
    )

    parser.add_argument('--generate', metavar='TEXT',
                        help='Generate audio from text')
    parser.add_argument('--voice', metavar='NAME', default=None,
                        help='Voice name to use (with --generate or --station-id)')
    parser.add_argument('--model', metavar='MODEL_ID', default=None,
                        help='TTS model ID (default: eleven_multilingual_v2)')
    parser.add_argument('--station-id', metavar='STATION', dest='station_id',
                        help='Generate station ID for a station name')
    parser.add_argument('--language', default='en',
                        help='Language for station ID (default: en)')
    parser.add_argument('--market', default=None,
                        help='Market for station ID (e.g., LA, NYC)')
    parser.add_argument('--voices', action='store_true',
                        help='List all available voices')
    parser.add_argument('--models', action='store_true',
                        help='List available TTS models')
    parser.add_argument('--report', action='store_true',
                        help='Generate localization report')
    parser.add_argument('--daemon', action='store_true',
                        help='Run as continuous background daemon')

    args = parser.parse_args()

    # If no arguments, show help
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    log.info("Initializing ElevenLabs agent...")

    # Database is always needed
    from database import get_connection
    conn = get_connection()

    # Report can run without API access (uses local DB only)
    if args.report:
        cmd_report(conn)
        conn.close()
        return

    # Everything else needs the API client
    client = get_client()
    if client is None:
        conn.close()
        sys.exit(1)

    try:
        if args.voices:
            cmd_voices(client, conn)

        elif args.models:
            cmd_models(client)

        elif args.generate:
            if not args.voice:
                print("ERROR: --voice is required with --generate")
                print("Use --voices to see available voices.")
                sys.exit(1)
            cmd_generate(client, conn, args.generate, args.voice, model_id=args.model)

        elif args.station_id:
            cmd_station_id(
                client, conn, args.station_id,
                voice_name=args.voice,
                language=args.language,
                market=args.market,
            )

        elif args.daemon:
            run_daemon(client, conn)

        else:
            parser.print_help()

    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    except Exception as e:
        log.error(f"Agent error: {e}", exc_info=True)
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()
        log.info("Database connection closed.")


if __name__ == '__main__':
    main()
