# Spotify Agent Setup -- API Credentials

Follow these steps to create a Spotify Developer app and configure the agent.

## Step 1: Create a Spotify Developer Account

1. Go to https://developer.spotify.com/dashboard
2. Log in with your Spotify account (or create one)
3. Accept the Developer Terms of Service if prompted

## Step 2: Create an App

1. Click **Create App**
2. Fill in:
   - App name: `Power FM Spotify Agent`
   - App description: `Artist streaming data and playlist tracking for Power FM`
   - Redirect URI: `http://localhost:8888/callback` (required but not used for client_credentials)
   - Check the **Web API** checkbox
3. Click **Save**

## Step 3: Get Your Credentials

1. On your app's dashboard, you'll see the **Client ID** displayed
2. Click **Settings** (top right)
3. Click **View client secret** to reveal the **Client Secret**
4. Copy both values

## Step 4: Create the Config File

Create the config directory and file:

```bash
mkdir -p ~/Agents/spotify-agent/config
```

Create `~/Agents/spotify-agent/config/spotify_config.json` with your credentials:

```json
{
    "client_id": "YOUR_CLIENT_ID_HERE",
    "client_secret": "YOUR_CLIENT_SECRET_HERE"
}
```

Replace `YOUR_CLIENT_ID_HERE` and `YOUR_CLIENT_SECRET_HERE` with the actual values from Step 3.

## Step 5: Create the Virtual Environment

```bash
cd ~/Agents/spotify-agent
python3 -m venv venv
venv/bin/pip install requests
```

## Step 6: Test the Agent

Search for an artist to verify credentials are working:

```bash
cd ~/Agents/spotify-agent
venv/bin/python agent.py --search "Firefly"
```

Add an artist to start tracking:

```bash
venv/bin/python agent.py --artist 4dpARuHxo51G3z768sgnrY
```

Generate a report:

```bash
venv/bin/python agent.py --report
```

## Step 7: Start as Background Service

```bash
cd ~/Agents/spotify-agent
chmod +x start.sh stop.sh
./start.sh
```

Or run manually:

```bash
venv/bin/python agent.py --daemon
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `--scan` | Pull latest data for all tracked artists |
| `--artist <spotify_id>` | Add/update a specific artist and their tracks |
| `--playlists` | Check tracked playlists for our artists' tracks |
| `--demographics` | Pull listener geography (limited without Spotify for Artists) |
| `--search "query"` | Search for an artist by name |
| `--report` | Generate a markdown report |
| `--daemon` | Run continuously, polling every hour |

## Notes

- This agent uses the **client_credentials** OAuth flow, which provides access to public Spotify data (artist profiles, tracks, playlists, audio features)
- For **listener demographics** and **stream counts**, you need Spotify for Artists access, which requires the Authorization Code flow with user consent
- The access token is cached in `config/.spotify_token_cache.json` and auto-refreshes when expired
- Rate limits are handled automatically (respects 429 Retry-After headers)
- All data is stored in `data/spotify.db` (SQLite with WAL mode)

## Troubleshooting

**"Config file not found"**: Create `config/spotify_config.json` per Step 4.

**"client_id and client_secret must be set"**: Check that your config JSON has both fields filled in (not empty strings).

**"Spotify auth failed (HTTP 401)"**: Your client_id or client_secret is incorrect. Re-check the values on your Spotify Developer Dashboard.

**"Rate limited (429)"**: The agent handles this automatically. If it persists, reduce polling frequency or number of tracked artists.

**"Artist not found"**: Double-check the Spotify artist ID. You can find it in the Spotify artist URL: `https://open.spotify.com/artist/XXXXXXXXXXXXXXXXXXXXXX` -- the ID is the string after `/artist/`.
