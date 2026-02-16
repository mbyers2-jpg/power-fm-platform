# Social Media Agent Setup Guide

## Quick Start

```bash
cd ~/Agents/social-media-agent

# 1. Create venv and install dependencies
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 2. Load content
venv/bin/python agent.py --load-content ~/Documents/Projects/Protect-The-Culture/Social-Media-Content-Package.md

# 3. Set campaign start date
venv/bin/python agent.py --set-start-date 2026-02-17

# 4. Preview schedule
venv/bin/python agent.py --schedule

# 5. Dry run
venv/bin/python agent.py --dry-run

# 6. Set up platform(s)
venv/bin/python agent.py --setup twitter
venv/bin/python agent.py --setup linkedin
venv/bin/python agent.py --setup facebook   # Also sets up Instagram

# 7. Start daemon
./start.sh
```

---

## Platform Setup

### Twitter / X

1. Go to [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)
2. Create a **Project** and **App** (Free tier works for posting)
3. Under **User authentication settings**:
   - Set App permissions to **Read and Write**
   - Type: **Web App, Automated App or Bot**
4. Under **Keys and Tokens**:
   - Generate **API Key** and **API Key Secret** (Consumer Keys)
   - Generate **Bearer Token**
   - Generate **Access Token** and **Access Token Secret** (with Read and Write)
5. Run: `venv/bin/python agent.py --setup twitter`
6. Enter all credentials when prompted

**Free Tier Limits:** 1,500 tweets/month, 50 tweets/day

---

### LinkedIn

1. Go to [LinkedIn Developers](https://www.linkedin.com/developers/apps)
2. Create an app (needs a LinkedIn Company Page)
3. Under **Products** tab, request:
   - **Share on LinkedIn**
   - **Sign In with LinkedIn using OpenID Connect**
4. Under **Auth** tab:
   - Copy **Client ID** and **Client Secret**
   - Add redirect URL: `http://localhost:8338/callback`
5. Run: `venv/bin/python agent.py --setup linkedin`
6. Enter Client ID and Client Secret
7. Authorize in the browser window that opens

**Limits:** No strict daily limit for organic posts. Token expires in 60 days.

---

### Facebook + Instagram (Meta)

Both platforms use the same Meta Graph API authentication.

1. Go to [Meta for Developers](https://developers.facebook.com/apps/)
2. Create a **Business** type app
3. Add the **Facebook Login for Business** product
4. Under **App Settings > Basic**:
   - Copy **App ID** and **App Secret**
5. Under **Facebook Login > Settings**:
   - Add redirect URL: `http://localhost:8339/callback`
6. Run: `venv/bin/python agent.py --setup facebook`
7. Enter App ID and App Secret
8. Authorize in the browser window that opens
9. Select your Facebook Page when prompted

**Facebook Requirements:**
- A Facebook Page (personal profiles cannot post via API)

**Instagram Requirements:**
- An **Instagram Business** or **Creator** account
- Connected to your Facebook Page (in Page Settings > Instagram)
- The app must have `instagram_content_publish` permission

**Important:** Instagram Graph API requires images for feed posts. Text-only posts are not supported on Instagram.

**Token Duration:** Long-lived tokens last ~60 days. The agent will warn when tokens are near expiry.

---

## Managing the Agent

```bash
# Check status
venv/bin/python agent.py --status

# View schedule
venv/bin/python agent.py --schedule

# Force-post a specific post
venv/bin/python agent.py --post-now <post_id>

# Fetch engagement metrics
venv/bin/python agent.py --metrics

# Generate engagement report
venv/bin/python agent.py --report

# Start/stop daemon
./start.sh
./stop.sh

# View logs
tail -f logs/agent.log
```

## Troubleshooting

- **"Auth not configured"** — Run `--setup <platform>` for the platform
- **"Rate limited"** — Posts will auto-retry with exponential backoff (30m, 60m, 120m)
- **Token expired** — Re-run `--setup <platform>` to refresh credentials
- **Instagram post failed** — Instagram requires an image URL for feed posts
- **Thread partially posted** — Remaining tweets marked as failed; fix and re-schedule

## File Locations

- Database: `data/social_media.db`
- Logs: `logs/agent.log`
- Reports: `reports/`
- Config/tokens: `config/` (never share these files)
