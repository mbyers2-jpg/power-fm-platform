"""
Catalog Scanner
Auto-discovers songs from the filesystem by scanning:
- ~/Music/ (audio files with metadata)
- ~/Documents/Artists/ (artist folders with contracts, releases)
- ~/Documents/Business/Contracts-Agreements/ (deals referencing songs)

Extracts metadata from audio files and cross-references with known catalog.
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime
from database import get_connection, init_db, add_song, add_rights_holder

HOME = Path.home()

SCAN_DIRS = {
    "masters": HOME / "Music" / "Masters",
    "stems": HOME / "Music" / "Stems",
    "demos": HOME / "Music" / "Demos",
    "rough_mixes": HOME / "Music" / "Rough-Mixes",
    "projects": HOME / "Music" / "Projects",
    "midi": HOME / "Music" / "MIDI",
    "artists": HOME / "Documents" / "Artists",
}

AUDIO_EXTENSIONS = {".wav", ".mp3", ".aiff", ".aif", ".flac", ".m4a", ".ogg", ".wma", ".amr"}
PROJECT_EXTENSIONS = {".band", ".logic", ".als", ".flp", ".ptx", ".rpp"}

# Known artists from the ecosystem
KNOWN_ARTISTS = {
    "firefly": "Firefly",
    "glenn": "Glenn",
    "lord afrixana": "Lord Afrixana",
    "lord-afrixana": "Lord Afrixana",
    "afrixana": "Lord Afrixana",
    "stephen fulton": "Stephen Fulton",
    "stephen-fulton": "Stephen Fulton",
    "fulton": "Stephen Fulton",
    "waddell": "Waddell",
    "cool boy": "Cool Boy",
    "cool-boy": "Cool Boy",
}


def detect_artist(filename, filepath):
    """Try to detect the artist from filename or path."""
    lower_name = filename.lower()
    lower_path = str(filepath).lower()

    for pattern, artist in KNOWN_ARTISTS.items():
        if pattern in lower_name or pattern in lower_path:
            return artist

    # Try to parse "Artist - Title" or "Artist_Title" format
    for sep in [" - ", " _ ", "_-_"]:
        if sep in filename:
            parts = filename.split(sep)
            if len(parts) >= 2:
                return parts[0].strip()

    # Check parent directory name in Artists folder
    if "Artists" in str(filepath):
        parts = str(filepath).split("/")
        for i, part in enumerate(parts):
            if part == "Artists" and i + 1 < len(parts):
                return parts[i + 1].replace("-", " ")

    return None


def detect_song_title(filename, artist=None):
    """Extract song title from filename."""
    # Remove extension
    name = Path(filename).stem

    # Remove common suffixes
    for suffix in ["_Master", "_master", "_Final", "_final", "_Mix", "_mix",
                   "_Vocal", "_vocal", "_Instrumental", "_instrumental",
                   "_Stem", "_stem", "_Demo", "_demo", "_Rough", "_rough",
                   " (1)", " (2)", " copy", "_v2", "_v3", "_V2", "_V3"]:
        name = name.replace(suffix, "")

    # Remove artist prefix if known
    if artist:
        for sep in [" - ", " _ ", "_-_", f"{artist}_", f"{artist} "]:
            if sep.lower() in name.lower():
                idx = name.lower().index(sep.lower()) + len(sep)
                name = name[idx:]
                break

    # Clean up
    name = name.replace("_", " ").replace("-", " ").strip()
    name = re.sub(r'\s+', ' ', name)

    return name


def detect_audio_type(filename, filepath):
    """Classify audio file type from path and name."""
    lower = filename.lower()
    path_str = str(filepath).lower()

    if "master" in path_str or "master" in lower:
        return "master"
    if "stem" in path_str or "stem" in lower:
        return "stem"
    if "demo" in path_str or "demo" in lower:
        return "demo"
    if "rough" in path_str or "mix" in lower:
        return "rough_mix"
    if "midi" in path_str or lower.endswith(".mid"):
        return "midi"
    if any(lower.endswith(ext) for ext in PROJECT_EXTENSIONS):
        return "project"

    return "unknown"


def scan_music_files(conn):
    """Scan all music directories for audio files."""
    discovered = []

    for dir_name, dir_path in SCAN_DIRS.items():
        if not dir_path.exists():
            continue

        for root, dirs, files in os.walk(dir_path):
            for filename in files:
                filepath = Path(root) / filename
                ext = filepath.suffix.lower()

                if ext not in AUDIO_EXTENSIONS and ext not in PROJECT_EXTENSIONS:
                    continue

                artist = detect_artist(filename, filepath)
                title = detect_song_title(filename, artist)
                audio_type = detect_audio_type(filename, filepath)

                if not title or len(title) < 2:
                    continue

                discovered.append({
                    "title": title,
                    "artist": artist or "Unknown",
                    "file_path": str(filepath),
                    "file_type": audio_type,
                    "file_size": filepath.stat().st_size,
                    "modified": datetime.fromtimestamp(filepath.stat().st_mtime).strftime("%Y-%m-%d"),
                })

    return discovered


def scan_artist_folders(conn):
    """Scan Documents/Artists for artist-related files and metadata."""
    artists_dir = HOME / "Documents" / "Artists"
    if not artists_dir.exists():
        return []

    artist_data = []
    for artist_folder in artists_dir.iterdir():
        if not artist_folder.is_dir():
            continue

        artist_name = artist_folder.name.replace("-", " ")
        files = []
        for root, dirs, filenames in os.walk(artist_folder):
            for fname in filenames:
                fpath = Path(root) / fname
                files.append({
                    "name": fname,
                    "path": str(fpath),
                    "size": fpath.stat().st_size,
                    "modified": datetime.fromtimestamp(fpath.stat().st_mtime).strftime("%Y-%m-%d"),
                })

        artist_data.append({
            "name": artist_name,
            "folder": str(artist_folder),
            "file_count": len(files),
            "files": files,
        })

    return artist_data


def auto_catalog(conn):
    """Auto-discover and catalog songs from filesystem scan."""
    music_files = scan_music_files(conn)

    # Group by song (title + artist)
    songs = {}
    for f in music_files:
        key = f"{f['artist']}::{f['title']}".lower()
        if key not in songs:
            songs[key] = {
                "title": f["title"],
                "artist": f["artist"],
                "files": [],
                "has_master": False,
                "has_stems": False,
                "has_demo": False,
            }
        songs[key]["files"].append(f)
        if f["file_type"] == "master":
            songs[key]["has_master"] = True
        if f["file_type"] == "stem":
            songs[key]["has_stems"] = True
        if f["file_type"] == "demo":
            songs[key]["has_demo"] = True

    # Insert into database
    added = 0
    for key, song_data in songs.items():
        # Check if already exists
        existing = conn.execute(
            "SELECT id FROM songs WHERE LOWER(title) = LOWER(?) AND LOWER(artist) = LOWER(?)",
            (song_data["title"], song_data["artist"])
        ).fetchone()

        if not existing:
            status = "active" if song_data["has_master"] else "unreleased"
            song_id = add_song(
                conn, song_data["title"], song_data["artist"],
                status=status,
                notes=f"Auto-discovered from filesystem scan. {len(song_data['files'])} files found."
            )
            added += 1

    return added, len(songs)


if __name__ == "__main__":
    conn = get_connection()
    init_db(conn)

    print("Scanning music files...")
    files = scan_music_files(conn)
    print(f"Found {len(files)} audio/project files")

    print("\nScanning artist folders...")
    artists = scan_artist_folders(conn)
    print(f"Found {len(artists)} artist folders")

    print("\nAuto-cataloging...")
    added, total = auto_catalog(conn)
    print(f"Added {added} new songs ({total} total discovered)")

    conn.close()
