"""
Heartbeat reporter for Pi relay nodes.
POSTs status to the platform-hub dashboard at a configurable interval.
"""

import json
import os
import time
import logging
import platform
import threading

log = logging.getLogger('fm-relay.heartbeat')

try:
    import requests
except ImportError:
    requests = None


class HeartbeatReporter:
    """Periodically POSTs node status to the platform-hub."""

    def __init__(self, config, relay=None):
        self.node_id = config.get('node_id', 'unknown')
        self.hub_url = config.get('hub_url', 'http://localhost:5560')
        self.interval = config.get('heartbeat_interval', 60)
        self.relay = relay  # Reference to the Relay instance for live status
        self._thread = None
        self._running = False
        self._start_time = time.time()

    def start(self):
        """Start the heartbeat reporter in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='heartbeat')
        self._thread.start()
        log.info(f"Heartbeat reporter started (every {self.interval}s → {self.hub_url})")

    def stop(self):
        """Stop the heartbeat reporter."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        log.info("Heartbeat reporter stopped.")

    def _loop(self):
        """Main heartbeat loop."""
        while self._running:
            try:
                self._send_heartbeat()
            except Exception as e:
                log.error(f"Heartbeat error: {e}")

            # Sleep in 1-second increments for responsive shutdown
            for _ in range(self.interval):
                if not self._running:
                    break
                time.sleep(1)

    def _send_heartbeat(self):
        """Collect status and POST to hub."""
        if requests is None:
            log.warning("requests library not available — heartbeat skipped")
            return

        status = self._collect_status()
        url = f"{self.hub_url.rstrip('/')}/api/transmitters/heartbeat"

        try:
            resp = requests.post(url, json=status, timeout=10)
            if resp.status_code == 200:
                log.debug(f"Heartbeat sent: {status.get('status', 'ok')}")
            else:
                log.warning(f"Heartbeat response {resp.status_code}: {resp.text[:200]}")
        except requests.ConnectionError:
            log.warning(f"Cannot reach hub at {url} — will retry next cycle")
        except Exception as e:
            log.error(f"Heartbeat POST failed: {e}")

    def _collect_status(self):
        """Build the heartbeat payload."""
        uptime = int(time.time() - self._start_time)

        payload = {
            'node_id': self.node_id,
            'status': 'ok',
            'stream_connected': False,
            'fm_transmitting': False,
            'cpu_temp': self._get_cpu_temp(),
            'cpu_usage': self._get_cpu_usage(),
            'memory_usage': self._get_memory_usage(),
            'uptime_seconds': uptime,
            'buffer_health': None,
            'audio_level': None,
            'errors': None,
        }

        # Get live status from relay if available
        if self.relay:
            relay_status = self.relay.get_status()
            payload['stream_connected'] = relay_status.get('stream_connected', False)
            payload['fm_transmitting'] = relay_status.get('fm_transmitting', False)
            payload['buffer_health'] = relay_status.get('buffer_health')
            payload['audio_level'] = relay_status.get('audio_level')

            errors = relay_status.get('errors')
            if errors:
                payload['errors'] = errors
                payload['status'] = 'degraded'

        return payload

    @staticmethod
    def _get_cpu_temp():
        """Read CPU temperature (Raspberry Pi specific)."""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp') as f:
                return float(f.read().strip()) / 1000.0
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def _get_cpu_usage():
        """Get CPU usage percentage."""
        try:
            import psutil
            return psutil.cpu_percent(interval=0.5)
        except ImportError:
            # Fallback: parse /proc/stat (Linux only)
            try:
                with open('/proc/stat') as f:
                    line = f.readline()
                fields = line.split()
                idle = int(fields[4])
                total = sum(int(f) for f in fields[1:])
                return round((1 - idle / total) * 100, 1) if total > 0 else None
            except Exception:
                return None

    @staticmethod
    def _get_memory_usage():
        """Get memory usage percentage."""
        try:
            import psutil
            return psutil.virtual_memory().percent
        except ImportError:
            # Fallback: parse /proc/meminfo (Linux only)
            try:
                meminfo = {}
                with open('/proc/meminfo') as f:
                    for line in f:
                        parts = line.split()
                        meminfo[parts[0].rstrip(':')] = int(parts[1])
                total = meminfo.get('MemTotal', 1)
                available = meminfo.get('MemAvailable', 0)
                return round((1 - available / total) * 100, 1)
            except Exception:
                return None
