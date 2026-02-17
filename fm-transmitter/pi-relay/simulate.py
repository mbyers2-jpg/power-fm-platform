#!/usr/bin/env python3
"""
Power FM Pi Relay — Simulator
Runs the relay with a SimulatedTransmitter backend for testing on Mac/Linux.
Generates synthetic audio if no stream is available.

Usage:
    python simulate.py                          # Use config.json (with simulated backend)
    python simulate.py --standalone             # No stream needed — synthetic audio
    python simulate.py --hub-url http://...     # Override hub URL
"""

import os
import sys
import json
import time
import struct
import signal
import logging
import math
import threading
import argparse

RELAY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RELAY_DIR)

from transmitter import SimulatedTransmitter
from heartbeat import HeartbeatReporter

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger('fm-relay.simulate')

running = True


def shutdown_handler(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


class SimulatedRelay:
    """A lightweight relay simulator that generates synthetic audio and heartbeats."""

    def __init__(self, config):
        self.config = config
        self.node_id = config.get('node_id', 'sim-01')
        self.frequency = config.get('fm_frequency', 88.1)
        self.stream_url = config.get('stream_url', 'http://localhost:8000/stream')
        self.sample_rate = 48000
        self.stream_connected = True
        self.fm_transmitting = True
        self.buffer_health = 1.0
        self.audio_level = 0.0
        self.errors = None

        self.transmitter = SimulatedTransmitter(self.frequency, log_interval=10.0)

    def start(self):
        self.transmitter.start()
        log.info(f"Simulator started: node={self.node_id}, freq={self.frequency} MHz")

    def stop(self):
        self.transmitter.stop()
        self.fm_transmitting = False
        log.info("Simulator stopped.")

    def get_status(self):
        return {
            'stream_connected': self.stream_connected,
            'fm_transmitting': self.transmitter.is_transmitting,
            'buffer_health': self.buffer_health,
            'audio_level': self.audio_level,
            'errors': self.errors,
        }

    def generate_audio(self, duration_ms=100):
        """Generate a synthetic tone (440Hz sine wave) as PCM s16le."""
        num_samples = int(self.sample_rate * duration_ms / 1000)
        samples = []
        for i in range(num_samples):
            t = i / self.sample_rate
            # Mix of tones to simulate music
            val = (
                0.3 * math.sin(2 * math.pi * 440 * t) +   # A4
                0.2 * math.sin(2 * math.pi * 554 * t) +   # C#5
                0.15 * math.sin(2 * math.pi * 659 * t) +  # E5
                0.1 * math.sin(2 * math.pi * 220 * t)     # A3
            )
            # Apply amplitude envelope
            envelope = 0.7 + 0.3 * math.sin(2 * math.pi * 0.5 * t)
            val *= envelope
            sample = int(max(-32768, min(32767, val * 16384)))
            samples.append(sample)

        pcm_data = struct.pack(f'<{num_samples}h', *samples)
        peak = max(abs(s) for s in samples) / 32768.0 if samples else 0.0
        self.audio_level = peak
        return pcm_data


def main():
    parser = argparse.ArgumentParser(description='Power FM Pi Relay Simulator')
    parser.add_argument('--config', type=str, default=None, help='Path to config.json')
    parser.add_argument('--standalone', action='store_true',
                        help='Generate synthetic audio instead of pulling a stream')
    parser.add_argument('--node-id', type=str, default=None, help='Override node ID')
    parser.add_argument('--frequency', type=float, default=None, help='Override FM frequency')
    parser.add_argument('--hub-url', type=str, default=None, help='Override hub URL')
    args = parser.parse_args()

    # Build config
    config_path = args.config or os.path.join(RELAY_DIR, 'config.json')
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {
            'node_id': 'sim-01',
            'stream_url': 'http://localhost:8000/stream',
            'fm_frequency': 88.1,
            'transmitter_type': 'simulated',
            'hub_url': 'http://localhost:5560',
            'heartbeat_interval': 60,
        }

    # Override with CLI args
    config['transmitter_type'] = 'simulated'
    if args.node_id:
        config['node_id'] = args.node_id
    if args.frequency:
        config['fm_frequency'] = args.frequency
    if args.hub_url:
        config['hub_url'] = args.hub_url

    log.info("=" * 50)
    log.info("  Power FM Pi Relay — SIMULATOR MODE")
    log.info(f"  Node: {config['node_id']}")
    log.info(f"  FM Frequency: {config['fm_frequency']} MHz")
    log.info(f"  Hub: {config.get('hub_url', 'http://localhost:5560')}")
    if args.standalone:
        log.info("  Mode: Standalone (synthetic audio)")
    else:
        log.info(f"  Stream: {config.get('stream_url', 'N/A')}")
    log.info("=" * 50)

    if args.standalone:
        # Standalone mode: synthetic audio + heartbeats
        relay = SimulatedRelay(config)
        relay.start()

        heartbeat = HeartbeatReporter(config, relay=relay)
        heartbeat.start()

        try:
            while running:
                pcm_data = relay.generate_audio(duration_ms=100)
                relay.transmitter.feed_audio(pcm_data)
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass

        heartbeat.stop()
        relay.stop()

    else:
        # Stream mode: use full relay with simulated transmitter
        from relay import Relay
        relay = Relay(config)
        relay.start()

        heartbeat = HeartbeatReporter(config, relay=relay)
        heartbeat.start()

        try:
            while running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

        heartbeat.stop()
        relay.stop()

    log.info("Simulator finished.")


if __name__ == '__main__':
    main()
