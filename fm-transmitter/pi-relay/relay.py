#!/usr/bin/env python3
"""
Power FM Pi Relay — Main daemon
Pulls an Icecast stream, decodes via ffmpeg, and feeds audio to an FM transmitter.

Audio pipeline:
    Icecast stream → requests.get(stream=True) → ffmpeg (MP3→PCM s16le 48kHz mono) → transmitter backend

Usage:
    python relay.py                     # Run with config.json
    python relay.py --config alt.json   # Use alternate config
"""

import os
import sys
import json
import time
import signal
import logging
import subprocess
import threading

RELAY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RELAY_DIR)

from transmitter import create_transmitter
from heartbeat import HeartbeatReporter

# --- Logging ---
LOG_DIR = os.path.join(RELAY_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'relay.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('fm-relay')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def load_config(path=None):
    """Load relay configuration."""
    if path is None:
        path = os.path.join(RELAY_DIR, 'config.json')

    if not os.path.exists(path):
        log.error(f"Config file not found: {path}")
        log.error("Create config.json with: node_id, stream_url, fm_frequency, transmitter_type, hub_url")
        sys.exit(1)

    with open(path) as f:
        return json.load(f)


class Relay:
    """Main relay: pulls Icecast stream → ffmpeg → FM transmitter."""

    def __init__(self, config):
        self.config = config
        self.stream_url = config['stream_url']
        self.fm_frequency = config['fm_frequency']
        self.transmitter_type = config.get('transmitter_type', 'simulated')
        self.sample_rate = config.get('sample_rate', 48000)
        self.reconnect_delay = config.get('reconnect_delay', 5)
        self.max_reconnect_delay = config.get('max_reconnect_delay', 60)
        self.chunk_size = config.get('chunk_size', 4096)

        # State
        self.stream_connected = False
        self.fm_transmitting = False
        self.buffer_health = None
        self.audio_level = None
        self.errors = None
        self._ffmpeg_process = None
        self._stream_thread = None
        self._lock = threading.Lock()

        # Create transmitter
        tx_kwargs = config.get('transmitter_options', {})
        self.transmitter = create_transmitter(self.transmitter_type, self.fm_frequency, **tx_kwargs)

    def start(self):
        """Start the relay: transmitter + stream reader."""
        log.info(f"Starting relay: {self.stream_url} → {self.fm_frequency} MHz ({self.transmitter_type})")
        self.transmitter.start()
        self.fm_transmitting = True

        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True, name='stream')
        self._stream_thread.start()

    def stop(self):
        """Stop the relay."""
        log.info("Stopping relay...")
        self.stream_connected = False
        self.fm_transmitting = False

        if self._ffmpeg_process:
            try:
                self._ffmpeg_process.terminate()
                self._ffmpeg_process.wait(timeout=5)
            except Exception:
                try:
                    self._ffmpeg_process.kill()
                except Exception:
                    pass
            self._ffmpeg_process = None

        self.transmitter.stop()
        log.info("Relay stopped.")

    def get_status(self):
        """Return current relay status for heartbeat reporting."""
        tx_status = self.transmitter.get_status()
        return {
            'stream_connected': self.stream_connected,
            'fm_transmitting': tx_status.get('is_transmitting', False),
            'buffer_health': self.buffer_health,
            'audio_level': self.audio_level,
            'errors': self.errors,
            'stream_url': self.stream_url,
            'frequency': self.fm_frequency,
            'transmitter': tx_status,
        }

    def _stream_loop(self):
        """Continuously connect to stream and feed audio to transmitter.
        Reconnects on failure with exponential backoff."""
        delay = self.reconnect_delay

        while running:
            try:
                log.info(f"Connecting to stream: {self.stream_url}")
                self._run_ffmpeg_pipeline()
                delay = self.reconnect_delay  # Reset on clean exit
            except Exception as e:
                log.error(f"Stream error: {e}")
                self.errors = str(e)

            if not running:
                break

            self.stream_connected = False
            log.info(f"Reconnecting in {delay}s...")
            for _ in range(delay):
                if not running:
                    return
                time.sleep(1)

            # Exponential backoff (capped)
            delay = min(delay * 2, self.max_reconnect_delay)

    def _run_ffmpeg_pipeline(self):
        """Start ffmpeg to pull the Icecast stream and output raw PCM.

        Pipeline: Icecast URL → ffmpeg → PCM s16le 48kHz mono → stdout
        We read ffmpeg's stdout and feed chunks to the transmitter.
        """
        cmd = [
            'ffmpeg',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', str(self.max_reconnect_delay),
            '-i', self.stream_url,
            '-f', 's16le',
            '-acodec', 'pcm_s16le',
            '-ar', str(self.sample_rate),
            '-ac', '1',
            '-',  # Output to stdout
        ]

        log.info(f"Starting ffmpeg: {' '.join(cmd[:5])}...")
        self._ffmpeg_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self.chunk_size * 4,
        )

        self.stream_connected = True
        self.errors = None
        log.info("Stream connected. Feeding audio to transmitter...")

        try:
            bytes_read = 0
            while running and self._ffmpeg_process.poll() is None:
                data = self._ffmpeg_process.stdout.read(self.chunk_size)
                if not data:
                    break

                bytes_read += len(data)
                self.transmitter.feed_audio(data)

                # Update buffer health estimate (simple: 1.0 if getting data, 0 if not)
                self.buffer_health = 1.0

                # Calculate audio level from PCM data
                if len(data) >= 2:
                    import struct
                    num_samples = len(data) // 2
                    try:
                        samples = struct.unpack(f'<{num_samples}h', data[:num_samples * 2])
                        peak = max(abs(s) for s in samples) / 32768.0 if samples else 0.0
                        self.audio_level = peak
                    except struct.error:
                        pass

        except Exception as e:
            log.error(f"Pipeline error: {e}")
            self.errors = str(e)
        finally:
            self.stream_connected = False
            self.buffer_health = 0.0
            if self._ffmpeg_process and self._ffmpeg_process.poll() is None:
                self._ffmpeg_process.terminate()
                try:
                    stderr = self._ffmpeg_process.stderr.read().decode('utf-8', errors='replace')[-500:]
                    if stderr.strip():
                        log.debug(f"ffmpeg stderr: {stderr}")
                except Exception:
                    pass
            self._ffmpeg_process = None
            log.info(f"Stream disconnected. Total bytes read: {bytes_read}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Power FM Pi Relay')
    parser.add_argument('--config', type=str, default=None, help='Path to config.json')
    args = parser.parse_args()

    config = load_config(args.config)

    log.info("=" * 50)
    log.info(f"  Power FM Pi Relay — Node: {config.get('node_id', 'unknown')}")
    log.info(f"  Stream: {config.get('stream_url', 'N/A')}")
    log.info(f"  FM Frequency: {config.get('fm_frequency', 'N/A')} MHz")
    log.info(f"  Transmitter: {config.get('transmitter_type', 'simulated')}")
    log.info("=" * 50)

    relay = Relay(config)
    relay.start()

    # Start heartbeat reporter
    heartbeat = HeartbeatReporter(config, relay=relay)
    heartbeat.start()

    # Wait for shutdown
    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    log.info("Shutting down...")
    heartbeat.stop()
    relay.stop()
    log.info("Goodbye.")


if __name__ == '__main__':
    main()
