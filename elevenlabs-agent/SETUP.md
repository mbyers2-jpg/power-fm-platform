# ElevenLabs Agent Setup

Voice generation agent for Power FM — generates station IDs, ad reads, and show intros using ElevenLabs text-to-speech API.

## 1. Get an ElevenLabs API Key

1. Go to [https://elevenlabs.io](https://elevenlabs.io) and sign up (or log in)
2. Navigate to **Profile Settings** > **API Keys**: [https://elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys)
3. Click **Create API Key** and copy it

## 2. Create the Config File

```bash
mkdir -p ~/Agents/elevenlabs-agent/config
```

Create `~/Agents/elevenlabs-agent/config/elevenlabs_config.json`:

```json
{
    "api_key": "your-elevenlabs-api-key-here"
}
```

### Optional Config Fields

```json
{
    "api_key": "your-elevenlabs-api-key-here",
    "default_model": "eleven_multilingual_v2",
    "max_retries": 3,
    "retry_delay": 2.0,
    "rate_limit_delay": 10.0,
    "voice_settings": {
        "stability": 0.5,
        "similarity_boost": 0.75,
        "style": 0.0,
        "use_speaker_boost": true
    }
}
```

## 3. Start the Agent

```bash
cd ~/Agents/elevenlabs-agent
chmod +x start.sh stop.sh
./start.sh
```

The start script will:
- Check for the config file
- Create a Python virtual environment if needed
- Install `requests` dependency
- Install and load the macOS launch agent (runs as daemon)

## 4. Usage

### List available voices
```bash
cd ~/Agents/elevenlabs-agent
venv/bin/python agent.py --voices
```

### List TTS models
```bash
venv/bin/python agent.py --models
```

### Generate audio from text
```bash
venv/bin/python agent.py --generate "Welcome to Power FM" --voice "Rachel"
```

### Generate a station ID
```bash
venv/bin/python agent.py --station-id "Power 106 LA"
venv/bin/python agent.py --station-id "Power FM NYC" --voice "Adam" --language en
```

### Generate a report
```bash
venv/bin/python agent.py --report
cat ~/Agents/elevenlabs-agent/reports/localization_$(date +%Y-%m-%d).md
```

### Run as daemon (continuous)
```bash
venv/bin/python agent.py --daemon
```

### Stop the daemon
```bash
./stop.sh
```

## 5. Directory Structure

```
elevenlabs-agent/
├── agent.py                  # Main agent script
├── api_client.py             # ElevenLabs API wrapper
├── database.py               # SQLite database layer
├── start.sh                  # Start daemon
├── stop.sh                   # Stop daemon
├── com.marcbyers.elevenlabs-agent.plist  # macOS launch agent
├── SETUP.md                  # This file
├── config/
│   └── elevenlabs_config.json  # API key (YOU CREATE THIS)
├── data/
│   └── elevenlabs.db          # SQLite database (auto-created)
├── logs/
│   ├── agent.log              # Application log
│   ├── stdout.log             # Daemon stdout
│   └── stderr.log             # Daemon stderr
├── output/                    # Generated audio files (MP3)
└── reports/                   # Localization reports (Markdown)
```

## 6. ElevenLabs Plan Notes

- **Free tier**: 10,000 characters/month, 3 custom voices
- **Starter**: 30,000 characters/month
- **Creator**: 100,000 characters/month
- **Pro**: 500,000 characters/month
- **Scale**: 2,000,000 characters/month

Character usage is tracked in the `usage_log` table and shown in reports.

## Troubleshooting

**"Config file not found"** — Create `config/elevenlabs_config.json` with your API key.

**"401 Unauthorized"** — Your API key is invalid or expired. Generate a new one at https://elevenlabs.io/app/settings/api-keys.

**"429 Rate Limited"** — You have hit the API rate limit. The agent will automatically retry after a delay.

**"Quota exceeded"** — You have used all your characters for the billing period. Upgrade your plan or wait for the next cycle.

**Voice not found** — Use `--voices` to list available voices. Voice names are matched with case-insensitive partial matching.
