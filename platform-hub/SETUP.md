# Platform Hub Setup

The Platform Hub is the orchestrator agent — it reads from all 6 API connector agent databases (read-only) and generates unified Power FM platform reports.

## Prerequisites
Set up at least one of the connector agents first:
- `chartmetric-agent` — see `~/Agents/chartmetric-agent/SETUP.md`
- `elevenlabs-agent` — see `~/Agents/elevenlabs-agent/SETUP.md`
- `youtube-agent` — see `~/Agents/youtube-agent/SETUP.md`
- `icecast-agent` — see `~/Agents/icecast-agent/SETUP.md`
- `spotify-agent` — see `~/Agents/spotify-agent/SETUP.md`
- `stripe-agent` — see `~/Agents/stripe-agent/SETUP.md`

## No API Keys Required
The Platform Hub doesn't connect to any external API. It only reads from other agent databases.

## 1. Create Virtual Environment
```bash
cd ~/Agents/platform-hub
python3 -m venv venv
venv/bin/pip install requests
```

## 2. Test
```bash
venv/bin/python agent.py --dashboard
```

## 3. Start Daemon
```bash
./start.sh
```

## Commands
```bash
venv/bin/python agent.py --status      # Agent status
venv/bin/python agent.py --dashboard   # Full dashboard
venv/bin/python agent.py --layers      # Layer health
venv/bin/python agent.py --metrics     # Cross-platform metrics
venv/bin/python agent.py --report      # Generate unified report
venv/bin/python agent.py --daemon      # Run continuously
```
