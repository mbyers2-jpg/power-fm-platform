# YouTube Agent Setup

## 1. Get YouTube Data API v3 Key
1. Go to https://console.cloud.google.com
2. Create or select a project
3. Enable **YouTube Data API v3** in APIs & Services
4. Create credentials → API Key
5. Copy the API key

## 2. Configure
Create `config/youtube_config.json`:
```json
{
    "api_key": "YOUR_YOUTUBE_API_KEY"
}
```

## 3. Create Virtual Environment
```bash
cd ~/Agents/youtube-agent
python3 -m venv venv
venv/bin/pip install requests
```

## 4. Optional: Audio Extraction
For the YouTube-to-FM Bridge audio extraction feature, install yt-dlp:
```bash
venv/bin/pip install yt-dlp
```

## 5. Test
```bash
venv/bin/python agent.py --report
```

## 6. Start Daemon
```bash
./start.sh
```

## Notes
- YouTube Data API has a daily quota of 10,000 units
- Search requests cost 100 units each; most other requests cost 1 unit
- The agent tracks quota usage internally
- Audio extraction requires yt-dlp (optional dependency)
