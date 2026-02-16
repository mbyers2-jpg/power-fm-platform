"""
Icecast/Shoutcast API Client
Handles communication with streaming servers, parsing XML/JSON responses,
and extracting server stats, mount points, and listener data.
"""

import os
import json
import time
import logging
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:
    requests = None

log = logging.getLogger('icecast-agent')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'icecast_servers.json')

DEFAULT_CONFIG = {
    "servers": [],
    "poll_interval": 60,
    "alert_thresholds": {
        "min_listeners": 0,
        "max_latency_ms": 5000,
        "min_bitrate": 64
    }
}


class IcecastClient:
    """Client for communicating with Icecast and Shoutcast streaming servers."""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        self.servers = self.config.get('servers', [])
        self.thresholds = self.config.get('alert_thresholds', DEFAULT_CONFIG['alert_thresholds'])
        self.poll_interval = self.config.get('poll_interval', 60)

        if requests is None:
            log.error("'requests' library not installed. Run: pip install requests")

    def _load_config(self):
        """Load server configuration from JSON file."""
        if not os.path.exists(self.config_path):
            log.warning(f"Config file not found: {self.config_path}")
            log.warning("No servers configured. Create config/icecast_servers.json to add servers.")
            log.warning("See SETUP.md for configuration instructions.")
            return DEFAULT_CONFIG

        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            log.info(f"Loaded config with {len(config.get('servers', []))} server(s)")
            return config
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON in config file: {e}")
            return DEFAULT_CONFIG
        except Exception as e:
            log.error(f"Failed to load config: {e}")
            return DEFAULT_CONFIG

    def reload_config(self):
        """Reload configuration from disk."""
        self.config = self._load_config()
        self.servers = self.config.get('servers', [])
        self.thresholds = self.config.get('alert_thresholds', DEFAULT_CONFIG['alert_thresholds'])
        self.poll_interval = self.config.get('poll_interval', 60)

    def has_servers(self):
        """Check if any servers are configured."""
        return len(self.servers) > 0

    def _request(self, server, endpoint, timeout=10):
        """
        Make an authenticated HTTP request to a streaming server.
        Returns (response_text, response_time_ms, status_code) or (None, 0, 0) on failure.
        """
        if requests is None:
            log.error("requests library not available")
            return None, 0, 0

        host = server.get('host', 'localhost')
        port = server.get('port', 8000)
        user = server.get('admin_user', 'admin')
        password = server.get('admin_password', '')
        protocol = server.get('protocol', 'http')

        url = f"{protocol}://{host}:{port}{endpoint}"

        try:
            start = time.time()
            resp = requests.get(
                url,
                auth=(user, password) if user and password else None,
                timeout=timeout,
                headers={'User-Agent': 'PowerFM-IcecastAgent/1.0'}
            )
            elapsed_ms = int((time.time() - start) * 1000)

            if resp.status_code == 401:
                log.error(f"Authentication failed for {host}:{port} — check admin credentials")
                return None, elapsed_ms, 401
            elif resp.status_code == 403:
                log.error(f"Access forbidden for {host}:{port}{endpoint}")
                return None, elapsed_ms, 403

            resp.raise_for_status()
            return resp.text, elapsed_ms, resp.status_code

        except requests.exceptions.ConnectTimeout:
            log.error(f"Connection timeout to {host}:{port}")
            return None, 0, 0
        except requests.exceptions.ConnectionError:
            log.error(f"Cannot connect to {host}:{port} — server may be down")
            return None, 0, 0
        except requests.exceptions.ReadTimeout:
            log.error(f"Read timeout from {host}:{port}")
            return None, 0, 0
        except requests.exceptions.RequestException as e:
            log.error(f"Request failed for {host}:{port}{endpoint}: {e}")
            return None, 0, 0

    def _parse_icecast_xml(self, xml_text):
        """
        Parse Icecast /admin/stats XML response.
        Returns a dict with server info and list of mount point dicts.
        """
        result = {
            'server_info': {},
            'mounts': []
        }

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            log.error(f"Failed to parse Icecast XML: {e}")
            return result

        # Server-level info
        for tag in ['admin', 'host', 'location', 'server_id', 'server_start',
                     'stream_kbytes_read', 'stream_kbytes_sent',
                     'client_connections', 'source_connections', 'stats_connections',
                     'listener_connections']:
            elem = root.find(tag)
            if elem is not None and elem.text:
                result['server_info'][tag] = elem.text

        # Icecast version from server_id
        server_id = root.find('server_id')
        if server_id is not None and server_id.text:
            result['server_info']['version'] = server_id.text

        # Mount points — Icecast uses <source mount="/mountname"> elements
        for source in root.findall('source'):
            mount_name = source.get('mount', '')
            if not mount_name:
                continue

            mount = {
                'mount_name': mount_name,
                'stream_title': '',
                'genre': '',
                'bitrate': None,
                'sample_rate': None,
                'channels': None,
                'content_type': '',
                'listeners_current': 0,
                'listeners_peak': 0,
                'connected_since': '',
                'source_ip': '',
                'user_agent': '',
            }

            # Extract all available fields
            field_map = {
                'server_name': 'stream_title',
                'title': 'stream_title',
                'genre': 'genre',
                'bitrate': 'bitrate',
                'ice-bitrate': 'bitrate',
                'audio_bitrate': 'bitrate',
                'samplerate': 'sample_rate',
                'audio_samplerate': 'sample_rate',
                'channels': 'channels',
                'audio_channels': 'channels',
                'server_type': 'content_type',
                'content-type': 'content_type',
                'listeners': 'listeners_current',
                'listener_peak': 'listeners_peak',
                'stream_start': 'connected_since',
                'source_ip': 'source_ip',
                'user_agent': 'user_agent',
            }

            for xml_tag, dict_key in field_map.items():
                elem = source.find(xml_tag)
                if elem is not None and elem.text:
                    val = elem.text.strip()
                    # Convert numeric fields
                    if dict_key in ('bitrate', 'sample_rate', 'channels',
                                    'listeners_current', 'listeners_peak'):
                        try:
                            val = int(float(val))
                        except (ValueError, TypeError):
                            val = 0
                    mount[dict_key] = val

            # Use server_name as stream_title if title not set
            if not mount['stream_title']:
                sn = source.find('server_name')
                if sn is not None and sn.text:
                    mount['stream_title'] = sn.text.strip()

            result['mounts'].append(mount)

        return result

    def _parse_shoutcast_xml(self, xml_text):
        """
        Parse Shoutcast admin XML response (v1 or v2).
        Returns same format as _parse_icecast_xml.
        """
        result = {
            'server_info': {},
            'mounts': []
        }

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            log.error(f"Failed to parse Shoutcast XML: {e}")
            return result

        # Shoutcast v2 uses <SHOUTCASTSERVER> root with <STREAMCONFIGS>/<STREAM> children
        # Shoutcast v1 uses <SHOUTCASTSERVER> with direct stats

        # Try v2 format first
        version_elem = root.find('.//VERSION')
        if version_elem is not None and version_elem.text:
            result['server_info']['version'] = f"Shoutcast {version_elem.text}"

        # v2: streams are under STREAM elements
        streams = root.findall('.//STREAM') or root.findall('.//stream')
        if streams:
            for stream in streams:
                mount = {
                    'mount_name': '/stream',
                    'stream_title': '',
                    'genre': '',
                    'bitrate': None,
                    'sample_rate': None,
                    'channels': None,
                    'content_type': 'audio/mpeg',
                    'listeners_current': 0,
                    'listeners_peak': 0,
                    'connected_since': '',
                    'source_ip': '',
                    'user_agent': '',
                }

                v2_map = {
                    'SERVERTITLE': 'stream_title',
                    'SERVERGENRE': 'genre',
                    'BITRATE': 'bitrate',
                    'SAMPLERATE': 'sample_rate',
                    'CURRENTLISTENERS': 'listeners_current',
                    'PEAKLISTENERS': 'listeners_peak',
                    'CONTENT': 'content_type',
                    'STREAMPATH': 'mount_name',
                    'SOURCEIP': 'source_ip',
                }

                for xml_tag, dict_key in v2_map.items():
                    elem = stream.find(xml_tag)
                    if elem is not None and elem.text:
                        val = elem.text.strip()
                        if dict_key in ('bitrate', 'sample_rate', 'listeners_current', 'listeners_peak'):
                            try:
                                val = int(float(val))
                            except (ValueError, TypeError):
                                val = 0
                        mount[dict_key] = val

                # Stream ID for mount name fallback
                sid = stream.find('ID') or stream.find('id')
                if sid is not None and sid.text and mount['mount_name'] == '/stream':
                    mount['mount_name'] = f"/stream/{sid.text}"

                result['mounts'].append(mount)
        else:
            # v1 format: single stream, stats at root level
            mount = {
                'mount_name': '/stream',
                'stream_title': '',
                'genre': '',
                'bitrate': None,
                'sample_rate': None,
                'channels': None,
                'content_type': 'audio/mpeg',
                'listeners_current': 0,
                'listeners_peak': 0,
                'connected_since': '',
                'source_ip': '',
                'user_agent': '',
            }

            v1_map = {
                'SERVERTITLE': 'stream_title',
                'SERVERGENRE': 'genre',
                'BITRATE': 'bitrate',
                'SAMPLERATE': 'sample_rate',
                'CURRENTLISTENERS': 'listeners_current',
                'PEAKLISTENERS': 'listeners_peak',
                'CONTENT': 'content_type',
            }

            for xml_tag, dict_key in v1_map.items():
                elem = root.find(xml_tag)
                if elem is not None and elem.text:
                    val = elem.text.strip()
                    if dict_key in ('bitrate', 'sample_rate', 'listeners_current', 'listeners_peak'):
                        try:
                            val = int(float(val))
                        except (ValueError, TypeError):
                            val = 0
                    mount[dict_key] = val

            if mount['stream_title'] or mount['listeners_current'] > 0:
                result['mounts'].append(mount)

        return result

    def _parse_shoutcast_json(self, json_text):
        """
        Parse Shoutcast v2 JSON response (/statistics?json=1).
        Returns same format as other parsers.
        """
        result = {
            'server_info': {},
            'mounts': []
        }

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse Shoutcast JSON: {e}")
            return result

        if 'version' in data:
            result['server_info']['version'] = f"Shoutcast {data['version']}"

        streams = data.get('streams', [])
        if isinstance(streams, list):
            for i, stream in enumerate(streams):
                mount = {
                    'mount_name': stream.get('streampath', f'/stream/{i+1}'),
                    'stream_title': stream.get('servertitle', ''),
                    'genre': stream.get('servergenre', ''),
                    'bitrate': stream.get('bitrate'),
                    'sample_rate': stream.get('samplerate'),
                    'channels': stream.get('channels'),
                    'content_type': stream.get('content', 'audio/mpeg'),
                    'listeners_current': stream.get('currentlisteners', 0),
                    'listeners_peak': stream.get('peaklisteners', 0),
                    'connected_since': '',
                    'source_ip': '',
                    'user_agent': '',
                }
                result['mounts'].append(mount)

        return result

    def get_server_stats(self, server):
        """
        Get overall server stats for a given server config dict.
        Returns parsed stats dict or None on failure.
        """
        server_type = server.get('type', 'icecast').lower()

        if server_type == 'icecast':
            text, latency, status = self._request(server, '/admin/stats')
            if text:
                parsed = self._parse_icecast_xml(text)
                parsed['latency_ms'] = latency
                parsed['status_code'] = status
                return parsed

        elif server_type == 'shoutcast':
            # Try JSON endpoint first (v2), fall back to XML
            text, latency, status = self._request(server, '/statistics?json=1')
            if text:
                try:
                    parsed = self._parse_shoutcast_json(text)
                    parsed['latency_ms'] = latency
                    parsed['status_code'] = status
                    return parsed
                except Exception:
                    pass

            # Fall back to XML admin endpoint
            sid = server.get('stream_id', 1)
            text, latency, status = self._request(server, f'/admin.cgi?sid={sid}&mode=viewxml')
            if text:
                parsed = self._parse_shoutcast_xml(text)
                parsed['latency_ms'] = latency
                parsed['status_code'] = status
                return parsed

        return None

    def get_mount_stats(self, server, mount_name):
        """Get stats for a specific mount point on a server."""
        stats = self.get_server_stats(server)
        if not stats:
            return None

        for mount in stats.get('mounts', []):
            if mount['mount_name'] == mount_name:
                return mount

        return None

    def get_listeners(self, server, mount_name=None):
        """
        Get listener details. For Icecast, uses /admin/listclients endpoint.
        Returns list of listener dicts or empty list.
        """
        server_type = server.get('type', 'icecast').lower()

        if server_type == 'icecast' and mount_name:
            text, _, status = self._request(server, f'/admin/listclients?mount={mount_name}')
            if text:
                listeners = []
                try:
                    root = ET.fromstring(text)
                    source = root.find('source')
                    if source is not None:
                        for listener in source.findall('listener'):
                            info = {}
                            for child in listener:
                                info[child.tag] = child.text
                            listeners.append(info)
                except ET.ParseError:
                    pass
                return listeners

        # Fallback: return count from stats
        stats = self.get_server_stats(server)
        if stats:
            for mount in stats.get('mounts', []):
                if mount_name is None or mount['mount_name'] == mount_name:
                    return [{'count': mount.get('listeners_current', 0), 'mount': mount['mount_name']}]

        return []

    def check_health(self, server):
        """
        Perform a health check on a server.
        Returns a dict with connectivity, response time, and basic diagnostics.
        """
        server_type = server.get('type', 'icecast').lower()
        host = server.get('host', 'unknown')
        port = server.get('port', 8000)

        health = {
            'server': f"{host}:{port}",
            'name': server.get('name', host),
            'type': server_type,
            'reachable': False,
            'latency_ms': 0,
            'status_code': 0,
            'version': None,
            'mount_count': 0,
            'total_listeners': 0,
            'errors': []
        }

        stats = self.get_server_stats(server)
        if stats is None:
            health['errors'].append(f"Cannot reach server at {host}:{port}")
            return health

        health['reachable'] = True
        health['latency_ms'] = stats.get('latency_ms', 0)
        health['status_code'] = stats.get('status_code', 0)

        server_info = stats.get('server_info', {})
        health['version'] = server_info.get('version')

        mounts = stats.get('mounts', [])
        health['mount_count'] = len(mounts)

        total_listeners = 0
        for mount in mounts:
            total_listeners += mount.get('listeners_current', 0)

            # Check thresholds
            bitrate = mount.get('bitrate')
            if bitrate is not None and bitrate < self.thresholds.get('min_bitrate', 64):
                health['errors'].append(
                    f"Mount {mount['mount_name']}: bitrate {bitrate}kbps below minimum {self.thresholds['min_bitrate']}kbps"
                )

        health['total_listeners'] = total_listeners

        # Latency check
        max_latency = self.thresholds.get('max_latency_ms', 5000)
        if health['latency_ms'] > max_latency:
            health['errors'].append(
                f"High latency: {health['latency_ms']}ms (threshold: {max_latency}ms)"
            )

        return health

    def get_all_status(self):
        """
        Iterate all configured servers and return combined status.
        Returns list of (server_config, stats_dict, health_dict) tuples.
        """
        if not self.servers:
            log.warning("No servers configured. Add servers to config/icecast_servers.json")
            return []

        results = []
        for server in self.servers:
            name = server.get('name', server.get('host', 'unknown'))
            log.info(f"Checking server: {name} ({server.get('host')}:{server.get('port')})")

            stats = self.get_server_stats(server)
            health = self.check_health(server)

            results.append((server, stats, health))

        return results
