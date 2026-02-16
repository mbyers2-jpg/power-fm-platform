# Icecast Agent Setup — Power FM Transmitter Network

This agent monitors Icecast and Shoutcast streaming servers that feed FM transmitters.

## Prerequisites

- Python 3.9+
- Access to your Icecast/Shoutcast server admin interface
- Admin credentials for each server

## Step 1: Create Virtual Environment

```bash
cd ~/Agents/icecast-agent
python3 -m venv venv
venv/bin/pip install requests
```

Or just run `./start.sh` which handles this automatically.

## Step 2: Configure Servers

Create the config file at `config/icecast_servers.json`:

```json
{
    "servers": [
        {
            "name": "Power FM Primary",
            "host": "stream.powerfm.com",
            "port": 8000,
            "admin_user": "admin",
            "admin_password": "YOUR_PASSWORD",
            "type": "icecast"
        }
    ],
    "poll_interval": 60,
    "alert_thresholds": {
        "min_listeners": 0,
        "max_latency_ms": 5000,
        "min_bitrate": 64
    }
}
```

### Server configuration fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Friendly name for the server |
| `host` | Yes | Hostname or IP address |
| `port` | Yes | Server port (default: 8000 for Icecast, 8000 for Shoutcast) |
| `admin_user` | Yes | Admin username (default: "admin" for Icecast) |
| `admin_password` | Yes | Admin password (set in icecast.xml or sc_serv.conf) |
| `type` | Yes | "icecast" or "shoutcast" |
| `protocol` | No | "http" (default) or "https" |
| `stream_id` | No | Shoutcast stream ID (default: 1) |

### Adding servers interactively

You can also add servers via the command line:

```bash
# Interactive mode
venv/bin/python agent.py --add-server

# Or with flags
venv/bin/python agent.py --add-server --name "Power FM Primary" --host stream.powerfm.com --port 8000 --user admin --password YOUR_PASSWORD --type icecast
```

### Alert thresholds

| Threshold | Default | Description |
|-----------|---------|-------------|
| `min_listeners` | 0 | Alert if listeners drop below this |
| `max_latency_ms` | 5000 | Alert if server response exceeds this (ms) |
| `min_bitrate` | 64 | Alert if stream bitrate drops below this (kbps) |

## Step 3: Verify Connectivity

Run a health check to confirm the agent can reach your servers:

```bash
cd ~/Agents/icecast-agent
venv/bin/python agent.py --health
```

You should see each server's status, latency, version, and mount point count.

## Step 4: Test Status Check

```bash
venv/bin/python agent.py --status
```

This will show all servers, mount points, listener counts, and any active alerts.

## Step 5: Start the Agent

### Run once (scan + report)
```bash
venv/bin/python agent.py --report
```

### Run as background service (recommended)
```bash
./start.sh
```

This installs a macOS LaunchAgent that keeps the agent running 24/7, polling every 60 seconds.

### Stop the service
```bash
./stop.sh
```

## Icecast Server Requirements

For the agent to collect stats, your Icecast server must have admin access enabled in `icecast.xml`:

```xml
<authentication>
    <admin-user>admin</admin-user>
    <admin-password>YOUR_PASSWORD</admin-password>
</authentication>
```

The agent uses these Icecast admin endpoints:
- `/admin/stats` — Server and mount point statistics (XML)
- `/admin/listclients` — Per-mount listener details (XML)

## Shoutcast Server Requirements

For Shoutcast v2, the agent uses:
- `/statistics?json=1` — JSON stats endpoint (preferred)
- `/admin.cgi?sid=1&mode=viewxml` — XML fallback

For Shoutcast v1:
- `/admin.cgi?sid=1&mode=viewxml` — XML stats

Ensure admin access is enabled in `sc_serv.conf`:
```
adminpassword=YOUR_PASSWORD
```

## Multiple Servers

Add as many servers as needed to the `servers` array in the config:

```json
{
    "servers": [
        {
            "name": "Power FM Primary",
            "host": "primary.powerfm.com",
            "port": 8000,
            "admin_user": "admin",
            "admin_password": "pass1",
            "type": "icecast"
        },
        {
            "name": "Power FM Backup",
            "host": "backup.powerfm.com",
            "port": 8000,
            "admin_user": "admin",
            "admin_password": "pass2",
            "type": "icecast"
        },
        {
            "name": "Power FM Shoutcast",
            "host": "shoutcast.powerfm.com",
            "port": 8000,
            "admin_user": "admin",
            "admin_password": "pass3",
            "type": "shoutcast"
        }
    ],
    "poll_interval": 60,
    "alert_thresholds": {
        "min_listeners": 0,
        "max_latency_ms": 5000,
        "min_bitrate": 64
    }
}
```

## CLI Reference

```
venv/bin/python agent.py --status       # Show all servers and mounts
venv/bin/python agent.py --listeners    # Show listener counts
venv/bin/python agent.py --health       # Run health check
venv/bin/python agent.py --add-server   # Add a server
venv/bin/python agent.py --report       # Generate markdown report
venv/bin/python agent.py --daemon       # Run continuously
```

## Troubleshooting

### "Cannot connect to server"
- Verify the host and port are correct
- Check that the server is running: `curl http://HOST:PORT/`
- Check firewall rules allow access from this machine

### "Authentication failed"
- Verify admin_user and admin_password match your server config
- For Icecast: check `<authentication>` block in icecast.xml
- For Shoutcast: check `adminpassword` in sc_serv.conf

### "No mount points found"
- Verify a source client (e.g., BUTT, Mixxx, liquidsoap) is connected and streaming
- Check mount point name matches what the source is using

### View agent logs
```bash
tail -f ~/Agents/icecast-agent/logs/agent.log
```
