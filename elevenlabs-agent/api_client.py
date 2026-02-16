"""
ElevenLabs API client with authentication, rate limiting, and error handling.
Config loaded from config/elevenlabs_config.json.
"""

import os
import json
import time
import logging

try:
    import requests
except ImportError:
    requests = None

log = logging.getLogger('elevenlabs-agent')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'elevenlabs_config.json')
BASE_URL = 'https://api.elevenlabs.io/v1'

# Rate limiting defaults
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_RATE_LIMIT_DELAY = 10.0


class ElevenLabsConfigError(Exception):
    """Raised when API configuration is missing or invalid."""
    pass


class ElevenLabsAPIError(Exception):
    """Raised when an API request fails."""
    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ElevenLabsClient:
    """Client for the ElevenLabs text-to-speech API."""

    def __init__(self, config_path=None):
        """
        Initialize the client. Loads API key from config JSON.

        Args:
            config_path: Path to config JSON. Defaults to config/elevenlabs_config.json.
        """
        if requests is None:
            raise ImportError(
                "The 'requests' library is required. "
                "Install it with: pip install requests"
            )

        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        self.api_key = self.config.get('api_key', '')
        self.max_retries = self.config.get('max_retries', DEFAULT_MAX_RETRIES)
        self.retry_delay = self.config.get('retry_delay', DEFAULT_RETRY_DELAY)
        self.rate_limit_delay = self.config.get('rate_limit_delay', DEFAULT_RATE_LIMIT_DELAY)
        self.default_model = self.config.get('default_model', 'eleven_multilingual_v2')
        self.default_voice_settings = self.config.get('voice_settings', {
            'stability': 0.5,
            'similarity_boost': 0.75,
            'style': 0.0,
            'use_speaker_boost': True,
        })

        if not self.api_key:
            raise ElevenLabsConfigError(
                "No API key found in config. "
                f"Please add your api_key to {self.config_path}\n"
                "Get your key at: https://elevenlabs.io/app/settings/api-keys"
            )

    def _load_config(self):
        """Load configuration from JSON file."""
        if not os.path.exists(self.config_path):
            raise ElevenLabsConfigError(
                f"Config file not found: {self.config_path}\n"
                "Create it with:\n"
                '{\n'
                '    "api_key": "your-elevenlabs-api-key-here"\n'
                '}\n'
                "Get your key at: https://elevenlabs.io/app/settings/api-keys"
            )

        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise ElevenLabsConfigError(f"Invalid JSON in config file: {e}")

        return config

    def _headers(self):
        """Build request headers with API key."""
        return {
            'xi-api-key': self.api_key,
            'Accept': 'application/json',
        }

    def _request(self, method, endpoint, return_json=True, **kwargs):
        """
        Make an authenticated API request with retry and rate limit handling.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path (e.g., /voices)
            return_json: If True, parse response as JSON. If False, return raw bytes.
            **kwargs: Additional arguments passed to requests.request()

        Returns:
            Parsed JSON dict/list or raw bytes depending on return_json.

        Raises:
            ElevenLabsAPIError: On non-recoverable API errors.
        """
        url = f"{BASE_URL}{endpoint}"
        headers = self._headers()

        # Merge any caller-provided headers
        if 'headers' in kwargs:
            headers.update(kwargs.pop('headers'))

        for attempt in range(1, self.max_retries + 1):
            try:
                log.debug(f"API {method} {endpoint} (attempt {attempt})")
                response = requests.request(
                    method, url, headers=headers, timeout=60, **kwargs
                )

                # Rate limited
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    wait = float(retry_after) if retry_after else self.rate_limit_delay
                    log.warning(f"Rate limited. Waiting {wait}s before retry...")
                    time.sleep(wait)
                    continue

                # Server error — retry
                if response.status_code >= 500:
                    log.warning(
                        f"Server error {response.status_code} on {endpoint}. "
                        f"Retry {attempt}/{self.max_retries}..."
                    )
                    time.sleep(self.retry_delay * attempt)
                    continue

                # Client error — do not retry
                if response.status_code >= 400:
                    body = None
                    try:
                        body = response.json()
                    except Exception:
                        body = response.text
                    error_msg = f"API error {response.status_code} on {endpoint}"
                    if isinstance(body, dict) and 'detail' in body:
                        error_msg += f": {body['detail']}"
                    elif isinstance(body, dict) and 'message' in body:
                        error_msg += f": {body['message']}"
                    raise ElevenLabsAPIError(error_msg, response.status_code, body)

                # Success
                if return_json:
                    return response.json()
                else:
                    return response.content

            except requests.exceptions.Timeout:
                log.warning(f"Request timeout on {endpoint}. Retry {attempt}/{self.max_retries}...")
                time.sleep(self.retry_delay * attempt)
            except requests.exceptions.ConnectionError as e:
                log.warning(f"Connection error on {endpoint}: {e}. Retry {attempt}/{self.max_retries}...")
                time.sleep(self.retry_delay * attempt)
            except ElevenLabsAPIError:
                raise
            except Exception as e:
                log.error(f"Unexpected error on {endpoint}: {e}")
                if attempt == self.max_retries:
                    raise ElevenLabsAPIError(f"Request failed after {self.max_retries} retries: {e}")
                time.sleep(self.retry_delay * attempt)

        raise ElevenLabsAPIError(f"Request to {endpoint} failed after {self.max_retries} retries")

    # --- Voice endpoints ---

    def get_voices(self):
        """
        GET /voices — List all available voices.

        Returns:
            List of voice dicts with keys: voice_id, name, category, labels, etc.
        """
        data = self._request('GET', '/voices')
        return data.get('voices', [])

    def get_voice(self, voice_id):
        """
        GET /voices/{voice_id} — Get details for a specific voice.

        Args:
            voice_id: The ElevenLabs voice ID.

        Returns:
            Voice detail dict.
        """
        return self._request('GET', f'/voices/{voice_id}')

    # --- Model endpoints ---

    def get_models(self):
        """
        GET /models — List available TTS models.

        Returns:
            List of model dicts.
        """
        return self._request('GET', '/models')

    # --- Text-to-Speech ---

    def generate_audio(self, text, voice_id, model_id=None, voice_settings=None,
                       output_format='mp3_44100_128'):
        """
        POST /text-to-speech/{voice_id} — Generate audio from text.

        Args:
            text: The text to convert to speech.
            voice_id: The voice to use.
            model_id: TTS model (default: eleven_multilingual_v2).
            voice_settings: Optional dict with stability, similarity_boost, style, use_speaker_boost.
            output_format: Audio format (default: mp3_44100_128).

        Returns:
            Raw audio bytes (MP3).
        """
        model = model_id or self.default_model
        settings = voice_settings or self.default_voice_settings

        payload = {
            'text': text,
            'model_id': model,
            'voice_settings': settings,
        }

        audio_bytes = self._request(
            'POST',
            f'/text-to-speech/{voice_id}',
            return_json=False,
            json=payload,
            headers={
                'Accept': 'audio/mpeg',
                'Content-Type': 'application/json',
            },
        )

        return audio_bytes

    # --- Usage ---

    def get_usage(self):
        """
        GET /usage/character-stats — Get character usage statistics.

        Returns:
            Usage stats dict from the API.
        """
        return self._request('GET', '/usage/character-stats')

    def get_subscription(self):
        """
        GET /user/subscription — Get current subscription info including character limits.

        Returns:
            Subscription info dict.
        """
        return self._request('GET', '/user/subscription')

    def get_user(self):
        """
        GET /user — Get current user info.

        Returns:
            User info dict.
        """
        return self._request('GET', '/user')
