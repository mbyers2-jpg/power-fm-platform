# Power FM Platform Hub

Central orchestrator and web dashboard for **Power FM** — a multi-market internet radio platform with 9 stations across the US, UK, and West Africa.

## What It Does

- **Aggregates data** from 6 upstream agents (YouTube, ElevenLabs, Spotify, Stripe, Chartmetric, Icecast)
- **Ranks music** via Power Charts — a weighted algorithm (views, likes, comments, recency, subscriber-normalized performance)
- **Generates playlists** automatically — hourly programming blocks, market-specific stations, and artist-focused rotations
- **Manages virtual DJs** with ElevenLabs AI voice integration across 6 programming blocks
- **Serves a web dashboard** with real-time stats, charts, network status, and artist profiles
- **Provides a CMS admin panel** for managing stations, playlists, audio library, schedules, and DJ shows
- **Monitors platform health** with alerts, automated backups, and listener analytics
- **Accepts song requests** from listeners with queue management

## Stations

| Station | Market | Port |
|---------|--------|------|
| Power FM | National | 8000 |
| Power 106 LA | Los Angeles | 8001 |
| Power 105.1 NYC | New York | 8002 |
| Power 92 Chicago | Chicago | 8003 |
| Power 96 Miami | Miami | 8004 |
| Power 107.5 Atlanta | Atlanta | 8005 |
| Power 104 Houston | Houston | 8006 |
| Power FM London | London | 8007 |
| Power FM Lagos | Lagos | 8008 |

Custom stations can be added via the admin panel.

## Programming Schedule

| Block | Time | Vibe |
|-------|------|------|
| Morning Power Hour | 6am–10am | High energy, upbeat |
| Midday Mix | 10am–3pm | Mainstream rotation |
| Afternoon Drive | 3pm–7pm | Peak energy |
| Evening Vibes | 7pm–9pm | Chill / R&B focused |
| Late Night | 9pm–12am | Slow jams, deep cuts |
| Overnight | 12am–6am | Auto-pilot |

## Quick Start

```bash
# Start the dashboard
cd ~/Agents/platform-hub
venv/bin/python dashboard.py

# Open in browser
open http://localhost:5560
```

Admin panel: `http://localhost:5560/admin/`

## Project Structure

```
platform-hub/
├── dashboard.py        # Flask web dashboard (port 5560)
├── cms.py              # Admin panel Blueprint (/admin/)
├── agent.py            # Orchestrator with CLI + daemon mode
├── database.py         # SQLite schema (platform_hub.db)
├── scheduler.py        # 6-block broadcast schedule engine
├── playlist.py         # Auto-playlist generator (M3U)
├── market_playlist.py  # Market-specific playlist generation
├── shows.py            # DJ personality system + ElevenLabs voices
├── charts.py           # Power Charts ranking engine
├── analytics.py        # Listener analytics + Icecast snapshots
├── artists.py          # Artist profile aggregation
├── requests_mod.py     # Song request system
├── notifications.py    # Platform health monitoring + alerts
├── backup_agent.py     # Automated database backups
├── start.sh / stop.sh  # launchd service management
├── playlists/          # Generated M3U playlist files
├── reports/            # Daily platform reports
├── data/               # SQLite databases
└── config/             # API configurations
```

## CLI Usage

```bash
venv/bin/python agent.py --status      # Platform status overview
venv/bin/python agent.py --dashboard   # Start web dashboard
venv/bin/python agent.py --charts      # Generate Power Charts
venv/bin/python agent.py --playlist    # Generate playlists
venv/bin/python agent.py --schedule    # Show current schedule
venv/bin/python agent.py --report      # Generate platform report
venv/bin/python agent.py --daemon      # Run 24/7 (used by launchd)
```

## Upstream Agents

Platform Hub reads from these agent databases:

| Agent | Status | Purpose |
|-------|--------|---------|
| youtube-agent | Working | Tracks artist channels, extracts audio |
| elevenlabs-agent | Working | AI voice generation (station IDs, promos, DJ intros) |
| spotify-agent | Blocked | Artist streaming data (403 — dev mode limitations) |
| stripe-agent | Working | Subscription management and revenue tracking |
| chartmetric-agent | Waiting | Industry chart data (API application pending) |
| icecast-agent | Waiting | Stream server management |

## Tech Stack

- **Python 3.9** with Flask
- **SQLite** with WAL journals for concurrent access
- **macOS launchd** for 24/7 daemon operation
- **ElevenLabs API** for AI-generated DJ voices
- **YouTube Data API v3** for content sourcing
