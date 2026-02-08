"""
Gmail OAuth2 Authentication — shared with email-agent credentials.
Uses the same token from ~/Agents/email-agent/config/ for Gmail send access.
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
]

# Share credentials with email-agent
EMAIL_AGENT_CONFIG = os.path.expanduser('~/Agents/email-agent/config')
LOCAL_CONFIG = os.path.join(os.path.dirname(__file__), 'config')

TOKEN_PATH = os.path.join(LOCAL_CONFIG, 'token.json')
CREDENTIALS_PATH = os.path.join(EMAIL_AGENT_CONFIG, 'credentials.json')
FALLBACK_CREDENTIALS = os.path.join(LOCAL_CONFIG, 'credentials.json')


def authenticate():
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds_file = CREDENTIALS_PATH if os.path.exists(CREDENTIALS_PATH) else FALLBACK_CREDENTIALS
            if not os.path.exists(creds_file):
                raise FileNotFoundError(
                    "credentials.json not found. Set up email-agent first "
                    "(~/Agents/email-agent/SETUP.md) or place credentials.json "
                    "in ~/Agents/comms-agent/config/"
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
            creds = flow.run_local_server(port=0)

        os.makedirs(LOCAL_CONFIG, exist_ok=True)
        with open(TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())

    return creds


def get_gmail_service():
    creds = authenticate()
    return build('gmail', 'v1', credentials=creds)
