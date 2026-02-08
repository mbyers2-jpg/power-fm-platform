#!/usr/bin/env python3
"""
Document Manager Agent for Marc Byers
Watches Desktop and Downloads for new files and auto-sorts them
into the organized folder structure.

Usage:
    venv/bin/python agent.py              # Scan once + sort
    venv/bin/python agent.py --daemon     # Watch continuously
    venv/bin/python agent.py --dry-run    # Show what would be moved without moving
"""

import sys
import os
import shutil
import signal
import time
import logging
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from classifier import classify_file

HOME = os.path.expanduser('~')
WATCH_DIRS = [
    os.path.join(HOME, 'Desktop'),
    os.path.join(HOME, 'Downloads'),
]
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
QUARANTINE_DIR = os.path.join(os.path.dirname(__file__), 'quarantine')

# Files to always ignore
IGNORE_FILES = {'.DS_Store', '.localized', 'CLAUDE.md', '.gitkeep'}
IGNORE_PREFIXES = ('.',)

# Settle time — wait for file to finish downloading before moving
SETTLE_SECONDS = 5

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(QUARANTINE_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'doc-manager.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('doc-manager')

running = True

def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received.")
    running = False

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def is_file_settled(filepath):
    """Check if a file has finished writing (size stable for SETTLE_SECONDS)."""
    try:
        size1 = os.path.getsize(filepath)
        time.sleep(SETTLE_SECONDS)
        size2 = os.path.getsize(filepath)
        return size1 == size2
    except OSError:
        return False


def safe_move(src, dest_dir, dry_run=False):
    """Move a file to destination, handling name conflicts."""
    os.makedirs(dest_dir, exist_ok=True)
    filename = os.path.basename(src)
    dest = os.path.join(dest_dir, filename)

    # Handle name conflicts
    if os.path.exists(dest):
        name, ext = os.path.splitext(filename)
        counter = 2
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{name}_v{counter}{ext}")
            counter += 1

    if dry_run:
        log.info(f"[DRY RUN] Would move: {src} → {dest}")
        return dest

    try:
        shutil.move(src, dest)
        log.info(f"Moved: {os.path.basename(src)} → {dest_dir.replace(HOME, '~')}")
        return dest
    except Exception as e:
        log.error(f"Failed to move {src}: {e}")
        return None


def should_ignore(filename):
    """Check if a file should be ignored."""
    if filename in IGNORE_FILES:
        return True
    if any(filename.startswith(p) for p in IGNORE_PREFIXES):
        return True
    # Ignore .crdownload (Chrome partial downloads)
    if filename.endswith('.crdownload') or filename.endswith('.part') or filename.endswith('.download'):
        return True
    return False


def process_file(filepath, dry_run=False):
    """Classify and move a single file."""
    filename = os.path.basename(filepath)

    if should_ignore(filename):
        return None

    if not os.path.isfile(filepath):
        return None

    dest_dir, reason = classify_file(filename)

    if dest_dir is None:
        log.warning(f"Cannot classify: {filename} (reason: {reason})")
        return None

    # Don't move if already in the right place
    current_dir = os.path.dirname(filepath)
    if os.path.normpath(current_dir) == os.path.normpath(dest_dir):
        return None

    # For installers, just leave them
    if reason == 'installer_kept_in_downloads':
        return None

    result = safe_move(filepath, dest_dir, dry_run=dry_run)
    if result:
        log.info(f"  Classified as: {reason}")
    return result


def scan_and_sort(dry_run=False):
    """One-time scan of watch directories."""
    total_moved = 0
    for watch_dir in WATCH_DIRS:
        if not os.path.isdir(watch_dir):
            continue

        log.info(f"Scanning: {watch_dir}")

        for item in os.listdir(watch_dir):
            filepath = os.path.join(watch_dir, item)

            # Handle directories in Desktop/Downloads
            if os.path.isdir(filepath) and not item.startswith('.'):
                # Leave known system dirs alone
                continue

            if os.path.isfile(filepath):
                result = process_file(filepath, dry_run=dry_run)
                if result:
                    total_moved += 1

    return total_moved


class NewFileHandler(FileSystemEventHandler):
    """Watchdog handler for new files appearing in watched directories."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self._pending = {}

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = event.src_path
        filename = os.path.basename(filepath)

        if should_ignore(filename):
            return

        log.info(f"New file detected: {filename}")
        # Wait for file to settle, then process
        self._pending[filepath] = time.time()

    def on_modified(self, event):
        if event.is_directory:
            return
        filepath = event.src_path
        if filepath in self._pending:
            self._pending[filepath] = time.time()

    def process_pending(self):
        """Process files that have settled."""
        now = time.time()
        to_remove = []

        for filepath, last_modified in list(self._pending.items()):
            if now - last_modified >= SETTLE_SECONDS:
                if os.path.exists(filepath):
                    process_file(filepath, dry_run=self.dry_run)
                to_remove.append(filepath)

        for fp in to_remove:
            del self._pending[fp]


def run_daemon(dry_run=False):
    """Watch directories for new files continuously."""
    log.info("Document manager starting in daemon mode")
    log.info(f"Watching: {', '.join(WATCH_DIRS)}")

    # Initial scan
    moved = scan_and_sort(dry_run=dry_run)
    log.info(f"Initial scan complete. Moved {moved} files.")

    handler = NewFileHandler(dry_run=dry_run)
    observer = Observer()

    for watch_dir in WATCH_DIRS:
        if os.path.isdir(watch_dir):
            observer.schedule(handler, watch_dir, recursive=False)

    observer.start()
    log.info("File watcher active. Waiting for new files...")

    try:
        while running:
            handler.process_pending()
            time.sleep(2)
    finally:
        observer.stop()
        observer.join()

    log.info("Document manager stopped.")


def main():
    daemon_mode = '--daemon' in sys.argv
    dry_run = '--dry-run' in sys.argv

    if dry_run:
        log.info("DRY RUN MODE — no files will be moved")

    if daemon_mode:
        run_daemon(dry_run=dry_run)
    else:
        moved = scan_and_sort(dry_run=dry_run)
        print(f"\nFiles sorted: {moved}")
        if dry_run:
            print("(Dry run — nothing was actually moved)")


if __name__ == '__main__':
    main()
