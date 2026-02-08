"""
Song Tracker Agent
Main agent that orchestrates:
- Catalog scanning and auto-discovery
- Revenue calculation and tracking
- Report generation
- Data import monitoring
- Real-time dashboard data
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Setup paths
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import get_connection, init_db, list_songs, get_song, get_song_earnings, get_catalog_value
from calculator import (
    calculate_split_distribution, calculate_song_analytics,
    global_revenue_summary, project_revenue, TERRITORY_MULTIPLIERS,
    PRO_RATES, DISTRIBUTOR_FEES
)
from scanner import scan_music_files, scan_artist_folders, auto_catalog
from importer import import_csv

# Logging
LOG_DIR = os.path.join(AGENT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "song-tracker.log")),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(AGENT_DIR, "reports")
IMPORTS_DIR = os.path.join(AGENT_DIR, "imports")
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(IMPORTS_DIR, exist_ok=True)

POLL_INTERVAL = 3600  # 1 hour


# ─── Report Generation ──────────────────────────────────────────────

def generate_catalog_report(conn):
    """Generate full catalog report with revenue data."""
    songs = list_songs(conn)
    catalog, grand_total = get_catalog_value(conn)
    summary = global_revenue_summary(conn)

    today = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(REPORTS_DIR, f"catalog_{today}.md")

    lines = []
    lines.append(f"# Song Catalog Report — {today}")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Overview
    lines.append("## Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Songs | {len(songs)} |")
    lines.append(f"| Total Revenue (All Time) | ${grand_total:,.2f} |")

    # Revenue by source
    if summary["by_source"]:
        lines.append("")
        lines.append("## Revenue by Source")
        lines.append("")
        lines.append("| Source | Revenue |")
        lines.append("|--------|---------|")
        for row in summary["by_source"]:
            total = row["total"] or 0
            lines.append(f"| {row['source'].replace('_', ' ').title()} | ${total:,.2f} |")

    # Revenue by territory
    if summary["by_territory"]:
        lines.append("")
        lines.append("## Revenue by Territory")
        lines.append("")
        lines.append("| Territory | Streams | Revenue |")
        lines.append("|-----------|---------|---------|")
        for row in summary["by_territory"][:20]:
            lines.append(f"| {row['territory']} | {row['streams']:,} | ${row['revenue']:,.2f} |")

    # Monthly trend
    if summary["monthly_trend"]:
        lines.append("")
        lines.append("## Monthly Trend")
        lines.append("")
        lines.append("| Month | Streams | Revenue |")
        lines.append("|-------|---------|---------|")
        for row in summary["monthly_trend"]:
            lines.append(f"| {row['month']} | {row['streams']:,} | ${row['revenue']:,.2f} |")

    # Song-by-song breakdown
    lines.append("")
    lines.append("## Catalog — Song Earnings")
    lines.append("")

    if catalog:
        # Sort by total revenue
        catalog.sort(key=lambda x: x["total"], reverse=True)
        lines.append("| Song | Artist | Streaming | Radio | PRO | Sync | Total |")
        lines.append("|------|--------|-----------|-------|-----|------|-------|")
        for s in catalog:
            lines.append(
                f"| {s['title']} | {s['artist']} | "
                f"${s['streaming']:,.2f} | ${s['radio']:,.2f} | "
                f"${s['pro_royalties']:,.2f} | ${s['sync']:,.2f} | "
                f"**${s['total']:,.2f}** |"
            )
    else:
        lines.append("*No songs in catalog yet. Run a scan or import data.*")

    # Rate card reference
    lines.append("")
    lines.append("## Platform Rate Reference (per stream)")
    lines.append("")
    rate_rows = conn.execute(
        "SELECT platform, tier, rate FROM rate_cards ORDER BY rate DESC"
    ).fetchall()
    if rate_rows:
        lines.append("| Platform | Tier | Rate |")
        lines.append("|----------|------|------|")
        for r in rate_rows:
            lines.append(f"| {r['platform'].replace('_', ' ').title()} | {r['tier']} | ${r['rate']:.4f} |")

    # Territory multipliers
    lines.append("")
    lines.append("## Territory Rate Multipliers")
    lines.append("")
    lines.append("Rates are adjusted by territory. US = 1.0x (baseline).")
    lines.append("")
    tier1 = {k: v for k, v in TERRITORY_MULTIPLIERS.items() if v >= 0.75}
    tier2 = {k: v for k, v in TERRITORY_MULTIPLIERS.items() if 0.25 <= v < 0.75}
    tier3 = {k: v for k, v in TERRITORY_MULTIPLIERS.items() if v < 0.25}
    lines.append(f"**Tier 1** (75-100%): {', '.join(f'{k} ({v:.0%})' for k, v in sorted(tier1.items(), key=lambda x: -x[1]))}")
    lines.append(f"**Tier 2** (25-74%): {', '.join(f'{k} ({v:.0%})' for k, v in sorted(tier2.items(), key=lambda x: -x[1]))}")
    lines.append(f"**Tier 3** (<25%): {', '.join(f'{k} ({v:.0%})' for k, v in sorted(tier3.items(), key=lambda x: -x[1]))}")

    report = "\n".join(lines)
    with open(report_path, "w") as f:
        f.write(report)

    return report_path


def generate_song_report(conn, song_id):
    """Generate detailed report for a single song."""
    analytics = calculate_song_analytics(song_id, conn)
    if not analytics:
        return None

    song = analytics["song"]
    today = datetime.now().strftime("%Y-%m-%d")
    safe_title = song["title"].replace(" ", "-").replace("/", "-")[:30]
    report_path = os.path.join(REPORTS_DIR, f"song_{safe_title}_{today}.md")

    lines = []
    lines.append(f"# {song['title']} — Performance Report")
    lines.append(f"**Artist:** {song['artist']}")
    if song.get("featured_artists"):
        lines.append(f"**Featured:** {song['featured_artists']}")
    if song.get("album"):
        lines.append(f"**Album:** {song['album']}")
    if song.get("isrc"):
        lines.append(f"**ISRC:** {song['isrc']}")
    if song.get("release_date"):
        lines.append(f"**Released:** {song['release_date']}")
    if song.get("label"):
        lines.append(f"**Label:** {song['label']}")
    if song.get("distributor"):
        lines.append(f"**Distributor:** {song['distributor']}")
    lines.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Earnings summary
    e = analytics["earnings"]
    lines.append("## Earnings Summary")
    lines.append("")
    lines.append("| Source | Revenue |")
    lines.append("|--------|---------|")
    lines.append(f"| Streaming | ${e['streaming']:,.2f} |")
    lines.append(f"| Radio | ${e['radio']:,.2f} |")
    lines.append(f"| PRO Royalties | ${e['pro_royalties']:,.2f} |")
    lines.append(f"| Sync Placements | ${e['sync']:,.2f} |")
    lines.append(f"| **TOTAL** | **${e['total']:,.2f}** |")

    # Streaming breakdown
    if analytics["streams"]:
        lines.append("")
        lines.append("## Streaming Breakdown")
        lines.append("")
        lines.append("| Platform | Streams | Revenue | Per Stream |")
        lines.append("|----------|---------|---------|------------|")
        for s in analytics["streams"]:
            per = s["total_revenue"] / s["total_streams"] if s["total_streams"] else 0
            lines.append(
                f"| {s['platform'].replace('_', ' ').title()} | "
                f"{s['total_streams']:,} | ${s['total_revenue']:,.2f} | ${per:.4f} |"
            )

    # Radio
    if analytics["radio"]:
        lines.append("")
        lines.append("## Radio Plays")
        lines.append("")
        lines.append("| Type | Plays | Audience Reach | Revenue Est. |")
        lines.append("|------|-------|---------------|--------------|")
        for r in analytics["radio"]:
            lines.append(
                f"| {r['station_type'].title()} | {r['plays']} | "
                f"{r['reach'] or 0:,} | ${r['revenue'] or 0:,.2f} |"
            )

    # PRO Royalties
    if analytics["pro_royalties"]:
        lines.append("")
        lines.append("## PRO Royalties")
        lines.append("")
        lines.append("| PRO | Type | Total |")
        lines.append("|-----|------|-------|")
        for p in analytics["pro_royalties"]:
            lines.append(f"| {p['pro']} | {p['royalty_type']} | ${p['total']:,.2f} |")

    # Sync
    if analytics["sync"]:
        lines.append("")
        lines.append("## Sync Placements")
        lines.append("")
        lines.append("| Type | Count | Total Fees |")
        lines.append("|------|-------|------------|")
        for s in analytics["sync"]:
            lines.append(f"| {s['placement_type'].title()} | {s['count']} | ${s['total_fee']:,.2f} |")

    # Splits
    if analytics["splits"]:
        lines.append("")
        lines.append("## Revenue Splits")
        lines.append("")
        lines.append("| Rights Holder | Role | Split | Gross | Net to Writer | To Publisher |")
        lines.append("|---------------|------|-------|-------|---------------|--------------|")
        for sp in analytics["splits"]:
            lines.append(
                f"| {sp['name']} | {sp['role']} | {sp['split_pct']:.1f}% | "
                f"${sp['gross_amount']:,.2f} | ${sp['net_to_writer']:,.2f} | "
                f"${sp['to_publisher']:,.2f} |"
            )

    # Playlists
    if analytics["playlists"]:
        lines.append("")
        lines.append("## Playlist Placements")
        lines.append("")
        lines.append("| Playlist | Platform | Followers | Position | Editorial |")
        lines.append("|----------|----------|-----------|----------|-----------|")
        for pl in analytics["playlists"]:
            editorial = "Yes" if pl.get("is_editorial") else "No"
            lines.append(
                f"| {pl['playlist_name']} | {pl['platform']} | "
                f"{pl.get('playlist_followers', 0):,} | {pl.get('position', '-')} | {editorial} |"
            )

    # Audience
    if analytics["audience"]:
        a = analytics["audience"]
        lines.append("")
        lines.append("## Audience Data")
        lines.append("")
        if a.get("monthly_listeners"):
            lines.append(f"- **Monthly Listeners:** {a['monthly_listeners']:,}")
        if a.get("followers"):
            lines.append(f"- **Followers:** {a['followers']:,}")
        if a.get("saves"):
            lines.append(f"- **Saves:** {a['saves']:,}")
        if a.get("shazams"):
            lines.append(f"- **Shazams:** {a['shazams']:,}")

        # Demographics
        if a.get("age_18_24_pct"):
            lines.append("")
            lines.append("**Age Demographics:**")
            if a.get("age_13_17_pct"): lines.append(f"- 13-17: {a['age_13_17_pct']:.1f}%")
            if a.get("age_18_24_pct"): lines.append(f"- 18-24: {a['age_18_24_pct']:.1f}%")
            if a.get("age_25_34_pct"): lines.append(f"- 25-34: {a['age_25_34_pct']:.1f}%")
            if a.get("age_35_44_pct"): lines.append(f"- 35-44: {a['age_35_44_pct']:.1f}%")
            if a.get("age_45_plus_pct"): lines.append(f"- 45+: {a['age_45_plus_pct']:.1f}%")

        if a.get("male_pct"):
            lines.append(f"\n**Gender:** {a['male_pct']:.0f}% male / {a['female_pct']:.0f}% female")

        if a.get("top_cities"):
            try:
                cities = json.loads(a["top_cities"])
                lines.append("\n**Top Cities:**")
                for c in cities[:5]:
                    lines.append(f"- {c['city']}: {c.get('listeners', 'N/A'):,}")
            except (json.JSONDecodeError, TypeError):
                pass

    # Projections
    proj = analytics["projections"]
    if proj["monthly_total"] > 0:
        lines.append("")
        lines.append("## Revenue Projections")
        lines.append("")
        lines.append(f"Based on last 90 days of streaming data:")
        lines.append(f"- **Monthly projected:** ${proj['monthly_total']:,.2f}")
        lines.append(f"- **Annual projected:** ${proj['annual_projection']:,.2f}")
        if proj["by_platform"]:
            lines.append("")
            lines.append("| Platform | Daily Streams | Monthly Revenue | 12-Month |")
            lines.append("|----------|--------------|-----------------|----------|")
            for p in proj["by_platform"]:
                lines.append(
                    f"| {p['platform'].replace('_', ' ').title()} | "
                    f"{p['daily_streams']:,} | ${p['monthly_revenue']:,.2f} | "
                    f"${p['projected_total']:,.2f} |"
                )

    report = "\n".join(lines)
    with open(report_path, "w") as f:
        f.write(report)

    return report_path


# ─── Import Monitor ─────────────────────────────────────────────────

def check_imports(conn):
    """Check imports/ directory for new CSV/JSON files to process."""
    imports_dir = Path(IMPORTS_DIR)
    if not imports_dir.exists():
        return 0

    processed = 0
    for f in imports_dir.iterdir():
        if f.suffix not in (".csv", ".json"):
            continue

        # Check if already imported
        existing = conn.execute(
            "SELECT id FROM import_log WHERE filename = ?", (f.name,)
        ).fetchone()
        if existing:
            continue

        log.info(f"Found new import file: {f.name}")

        # Detect import type from filename
        lower = f.name.lower()
        import_type = None
        if "spotify" in lower:
            import_type = "spotify"
        elif "apple" in lower:
            import_type = "apple"
        elif "distrokid" in lower:
            import_type = "distrokid"
        elif "tunecore" in lower:
            import_type = "tunecore"
        elif "ascap" in lower:
            import_type = "ascap"
        elif "bmi" in lower:
            import_type = "bmi"
        elif "soundexchange" in lower:
            import_type = "soundexchange"
        elif "catalog" in lower:
            import_type = "catalog"
        else:
            import_type = "generic_streams"

        try:
            if f.suffix == ".json":
                from importer import import_json
                count = import_json(str(f), conn)
                log.info(f"Imported {count} records from {f.name}")
            else:
                imported, skipped, errors = import_csv(str(f), import_type, conn)
                log.info(f"Imported {imported} records from {f.name} (skipped: {skipped})")
                if errors:
                    log.warning(f"Import errors: {errors}")
            processed += 1
        except Exception as e:
            log.error(f"Failed to import {f.name}: {e}")

    return processed


# ─── Daemon Mode ─────────────────────────────────────────────────────

def run_once(conn):
    """Run a single scan + report cycle."""
    log.info("Starting scan cycle...")

    # Auto-discover songs from filesystem
    added, total = auto_catalog(conn)
    if added > 0:
        log.info(f"Discovered {added} new songs ({total} total in filesystem)")

    # Check for new imports
    imported = check_imports(conn)
    if imported > 0:
        log.info(f"Processed {imported} new import files")

    # Generate reports
    report_path = generate_catalog_report(conn)
    log.info(f"Catalog report: {report_path}")

    # Generate individual song reports for songs with revenue
    songs = list_songs(conn)
    for song in songs:
        earnings = get_song_earnings(conn, song["id"])
        if earnings["total"] > 0:
            path = generate_song_report(conn, song["id"])
            if path:
                log.info(f"Song report: {path}")

    return True


def run_daemon(conn):
    """Run continuously, scanning on interval."""
    log.info(f"Song tracker daemon starting (polling every {POLL_INTERVAL}s)")

    while True:
        try:
            run_once(conn)
        except Exception as e:
            log.error(f"Scan cycle error: {e}")

        log.info(f"Next scan in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)


# ─── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Song Tracker Agent")
    parser.add_argument("--daemon", action="store_true", help="Run as background daemon")
    parser.add_argument("--scan", action="store_true", help="Scan filesystem for songs")
    parser.add_argument("--report", action="store_true", help="Generate catalog report")
    parser.add_argument("--song", type=int, help="Generate report for specific song ID")
    parser.add_argument("--import-file", type=str, help="Import a specific file")
    parser.add_argument("--import-type", type=str, help="Import type (spotify, apple, distrokid, etc.)")
    parser.add_argument("--list", action="store_true", help="List all songs in catalog")
    parser.add_argument("--search", type=str, help="Search songs by title or artist")
    parser.add_argument("--earnings", type=int, help="Show earnings for song ID")
    parser.add_argument("--project", type=int, help="Project revenue for song ID")
    parser.add_argument("--catalog-value", action="store_true", help="Show total catalog value")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)
    log.info("Song tracker initialized")

    if args.daemon:
        run_daemon(conn)

    elif args.scan:
        added, total = auto_catalog(conn)
        print(f"Discovered {added} new songs ({total} total)")

    elif args.report:
        path = generate_catalog_report(conn)
        print(f"Report: {path}")

    elif args.song:
        path = generate_song_report(conn, args.song)
        if path:
            print(f"Song report: {path}")
        else:
            print(f"Song ID {args.song} not found")

    elif args.import_file:
        if not args.import_type:
            print("--import-type required (spotify, apple, distrokid, tunecore, ascap, bmi, soundexchange, catalog, generic_streams)")
            sys.exit(1)
        imported, skipped, errors = import_csv(args.import_file, args.import_type, conn)
        print(f"Imported: {imported}, Skipped: {skipped}")
        if errors:
            print(f"Errors: {errors}")

    elif args.list:
        songs = list_songs(conn)
        if not songs:
            print("No songs in catalog. Run --scan to discover from filesystem.")
        else:
            print(f"\n{'ID':<5} {'Title':<35} {'Artist':<20} {'Status':<10} {'Released'}")
            print("-" * 90)
            for s in songs:
                print(f"{s['id']:<5} {s['title'][:33]:<35} {s['artist'][:18]:<20} "
                      f"{s['status']:<10} {s['release_date'] or '-'}")

    elif args.search:
        from database import search_songs
        results = search_songs(conn, args.search)
        for s in results:
            print(f"[{s['id']}] {s['title']} — {s['artist']} ({s['status']})")

    elif args.earnings:
        earnings = get_song_earnings(conn, args.earnings)
        song = get_song(conn, args.earnings)
        if song:
            print(f"\n{song['title']} — {song['artist']}")
            print(f"  Streaming:     ${earnings['streaming']:,.2f}")
            print(f"  Radio:         ${earnings['radio']:,.2f}")
            print(f"  PRO Royalties: ${earnings['pro_royalties']:,.2f}")
            print(f"  Sync:          ${earnings['sync']:,.2f}")
            print(f"  TOTAL:         ${earnings['total']:,.2f}")
        else:
            print(f"Song ID {args.earnings} not found")

    elif args.project:
        proj = project_revenue(args.project, 12, conn)
        song = get_song(conn, args.project)
        if song:
            print(f"\n{song['title']} — Revenue Projections")
            print(f"  Monthly:  ${proj['monthly_total']:,.2f}")
            print(f"  Annual:   ${proj['annual_projection']:,.2f}")
            for p in proj["by_platform"]:
                print(f"    {p['platform']}: {p['daily_streams']:,}/day → ${p['monthly_revenue']:,.2f}/mo")

    elif args.catalog_value:
        catalog, total = get_catalog_value(conn)
        print(f"\nTotal Catalog Value: ${total:,.2f}")
        print(f"Songs: {len(catalog)}")
        for s in sorted(catalog, key=lambda x: x["total"], reverse=True)[:10]:
            if s["total"] > 0:
                print(f"  {s['title']} ({s['artist']}): ${s['total']:,.2f}")

    else:
        # Default: run once
        run_once(conn)

    conn.close()


if __name__ == "__main__":
    main()
