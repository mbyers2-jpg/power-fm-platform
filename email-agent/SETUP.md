# Email Agent Setup — Google Cloud API Credentials

Follow these steps in your browser to enable Gmail API access.

## Step 1: Create a Google Cloud Project

1. Go to https://console.cloud.google.com/
2. Sign in with **m.byers2@gmail.com**
3. Click the project dropdown (top left) → **New Project**
4. Name it: `Marc Byers Email Agent`
5. Click **Create**

## Step 2: Enable Gmail API

1. In the new project, go to **APIs & Services** → **Library**
2. Search for **Gmail API**
3. Click it → Click **Enable**

## Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** → Click **Create**
3. Fill in:
   - App name: `Email Agent`
   - User support email: `m.byers2@gmail.com`
   - Developer contact: `m.byers2@gmail.com`
4. Click **Save and Continue**
5. On Scopes page → **Add or Remove Scopes**
   - Add: `https://www.googleapis.com/auth/gmail.readonly`
   - Add: `https://www.googleapis.com/auth/gmail.modify`
   - Add: `https://www.googleapis.com/auth/gmail.labels`
6. Click **Save and Continue**
7. On Test Users page → **Add Users** → add `m.byers2@gmail.com`
8. Click **Save and Continue** → **Back to Dashboard**

## Step 4: Create OAuth Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **+ Create Credentials** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `Email Agent Desktop`
5. Click **Create**
6. Click **Download JSON**
7. Rename the downloaded file to `credentials.json`
8. Move it to: `~/Agents/email-agent/config/credentials.json`

## Step 5: First Authentication

Run this in terminal:

```bash
cd ~/Agents/email-agent
venv/bin/python auth.py
```

A browser window will open. Sign in with m.byers2@gmail.com and grant permissions.
Once complete, a `token.json` will be saved and the agent can run without a browser.

## Step 6: Start the Agent

```bash
cd ~/Agents/email-agent
venv/bin/python agent.py
```

Or to run it as a background service (24/7):

```bash
launchctl load ~/Library/LaunchAgents/com.marcbyers.email-agent.plist
```
