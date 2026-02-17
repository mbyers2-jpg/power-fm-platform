"""
FM Transmitter abstraction layer with pluggable backends.

Backends:
    SimulatedTransmitter — No hardware, logs audio levels (for testing on Mac/Linux)
    RpitxTransmitter     — Uses rpitx via GPIO4 (Raspberry Pi, no extra hardware)
    Si4713Transmitter    — I2C FM transmitter module (future upgrade)
"""

import os
import sys
import time
import struct
import logging
import subprocess
import threading

log = logging.getLogger('fm-relay.transmitter')


class BaseTransmitter:
    """Base class for FM transmitter backends."""

    def __init__(self, frequency, **kwargs):
        self.frequency = frequency
        self.is_transmitting = False
        self._lock = threading.Lock()

    def start(self):
        """Start FM transmission."""
        raise NotImplementedError

    def stop(self):
        """Stop FM transmission."""
        raise NotImplementedError

    def feed_audio(self, pcm_data):
        """Feed raw PCM audio data (s16le, 48kHz, mono) to the transmitter."""
        raise NotImplementedError

    def get_status(self):
        """Return transmitter status dict."""
        return {
            'backend': self.__class__.__name__,
            'frequency': self.frequency,
            'is_transmitting': self.is_transmitting,
        }


class SimulatedTransmitter(BaseTransmitter):
    """Simulated FM transmitter for testing — logs audio levels, no hardware needed."""

    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.samples_received = 0
        self.peak_level = 0.0
        self._log_interval = kwargs.get('log_interval', 5.0)
        self._last_log_time = 0

    def start(self):
        with self._lock:
            self.is_transmitting = True
            self.samples_received = 0
            self.peak_level = 0.0
            log.info(f"[SIM] FM transmitter started on {self.frequency} MHz (simulated)")

    def stop(self):
        with self._lock:
            self.is_transmitting = False
            log.info(f"[SIM] FM transmitter stopped. Total samples: {self.samples_received}")

    def feed_audio(self, pcm_data):
        """Process PCM data and calculate audio levels."""
        if not self.is_transmitting:
            return

        # Parse s16le samples and calculate RMS level
        num_samples = len(pcm_data) // 2
        if num_samples == 0:
            return

        self.samples_received += num_samples

        # Calculate peak level from raw PCM s16le
        try:
            samples = struct.unpack(f'<{num_samples}h', pcm_data[:num_samples * 2])
            peak = max(abs(s) for s in samples) / 32768.0 if samples else 0.0
            rms = (sum(s * s for s in samples) / num_samples) ** 0.5 / 32768.0 if samples else 0.0
            self.peak_level = max(self.peak_level, peak)
        except struct.error:
            peak = 0.0
            rms = 0.0

        # Periodic logging
        now = time.time()
        if now - self._last_log_time >= self._log_interval:
            self._last_log_time = now
            log.info(f"[SIM] {self.frequency} MHz — peak: {peak:.3f}, rms: {rms:.4f}, "
                     f"samples: {self.samples_received}")

    def get_status(self):
        status = super().get_status()
        status['samples_received'] = self.samples_received
        status['peak_level'] = self.peak_level
        return status


class RpitxTransmitter(BaseTransmitter):
    """FM transmitter using rpitx on Raspberry Pi GPIO4.

    Requires rpitx installed: https://github.com/F5OEO/rpitx
    Uses pifmrds for FM stereo with RDS, or fm_transmitter for basic FM.

    The audio pipeline feeds PCM data to rpitx via stdin pipe.
    """

    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.power = kwargs.get('power', 7)  # 0-7 GPIO drive strength
        self.rds_station = kwargs.get('rds_station', 'POWER FM')
        self.rds_text = kwargs.get('rds_text', 'powerfmlive.com')
        self._process = None

    def start(self):
        with self._lock:
            if self._process and self._process.poll() is None:
                log.warning("[RPITX] Already running, stopping first...")
                self.stop()

            # Use pifmrds for FM stereo with RDS
            cmd = [
                'sudo', '/usr/local/bin/pifmrds',
                '-freq', str(self.frequency),
                '-audio', '-',  # Read from stdin
                '-ps', self.rds_station[:8],
                '-rt', self.rds_text[:64],
                '-power', str(self.power),
            ]

            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self.is_transmitting = True
                log.info(f"[RPITX] FM transmitter started on {self.frequency} MHz "
                         f"(RDS: {self.rds_station})")
            except FileNotFoundError:
                log.error("[RPITX] pifmrds not found. Install rpitx first.")
                raise
            except Exception as e:
                log.error(f"[RPITX] Failed to start: {e}")
                raise

    def stop(self):
        with self._lock:
            if self._process:
                try:
                    self._process.stdin.close()
                except Exception:
                    pass
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    self._process.kill()
                self._process = None
            self.is_transmitting = False
            log.info("[RPITX] FM transmitter stopped.")

    def feed_audio(self, pcm_data):
        """Feed PCM audio data to rpitx via stdin pipe."""
        if not self.is_transmitting or not self._process:
            return

        try:
            self._process.stdin.write(pcm_data)
            self._process.stdin.flush()
        except BrokenPipeError:
            log.error("[RPITX] Broken pipe — transmitter process died.")
            self.is_transmitting = False
        except Exception as e:
            log.error(f"[RPITX] Write error: {e}")

    def get_status(self):
        status = super().get_status()
        status['rds_station'] = self.rds_station
        status['power'] = self.power
        if self._process:
            status['pid'] = self._process.pid
            status['running'] = self._process.poll() is None
        return status


class Si4713Transmitter(BaseTransmitter):
    """FM transmitter using Si4713 I2C module (Adafruit breakout board).

    Requires: adafruit-circuitpython-si4713 package.
    This is a placeholder for future hardware upgrade.
    """

    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.tx_power = kwargs.get('tx_power', 115)  # 88-115 dBuV
        self._board = None
        self._si4713 = None

    def start(self):
        with self._lock:
            try:
                import board
                import busio
                import adafruit_si4713

                i2c = busio.I2C(board.SCL, board.SDA)
                self._si4713 = adafruit_si4713.SI4713(i2c)
                self._si4713.tx_frequency_khz = int(self.frequency * 1000)
                self._si4713.tx_power = self.tx_power
                self.is_transmitting = True
                log.info(f"[SI4713] FM transmitter started on {self.frequency} MHz "
                         f"(power: {self.tx_power} dBuV)")
            except ImportError:
                log.error("[SI4713] adafruit-circuitpython-si4713 not installed.")
                raise
            except Exception as e:
                log.error(f"[SI4713] Failed to start: {e}")
                raise

    def stop(self):
        with self._lock:
            if self._si4713:
                try:
                    self._si4713.tx_power = 0
                except Exception:
                    pass
                self._si4713 = None
            self.is_transmitting = False
            log.info("[SI4713] FM transmitter stopped.")

    def feed_audio(self, pcm_data):
        """Si4713 takes audio via 3.5mm line-in, not digital PCM.
        This method is a no-op — audio routing is handled externally."""
        pass

    def get_status(self):
        status = super().get_status()
        status['tx_power'] = self.tx_power
        return status


# --- Factory ---

BACKENDS = {
    'simulated': SimulatedTransmitter,
    'rpitx': RpitxTransmitter,
    'si4713': Si4713Transmitter,
}


def create_transmitter(transmitter_type, frequency, **kwargs):
    """Factory: create a transmitter backend by type name."""
    cls = BACKENDS.get(transmitter_type)
    if not cls:
        raise ValueError(f"Unknown transmitter type: {transmitter_type}. "
                         f"Available: {list(BACKENDS.keys())}")
    return cls(frequency, **kwargs)
