"""
Gmail OAuth2 Authentication Module
Handles initial auth flow and token refresh for persistent agent operation.
"""

import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
]

CONFIG_DIR = os.path.join(os.path.dirname(__file__), 'config')
TOKEN_PATH = os.path.join(CONFIG_DIR, 'token.json')
CREDENTIALS_PATH = os.path.join(CONFIG_DIR, 'credentials.json')


def authenticate():
    """
    Authenticate with Gmail API.
    First run opens browser for OAuth consent.
    Subsequent runs use saved refresh token.
    Returns authenticated Gmail API service.
    """
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                print("ERROR: credentials.json not found in config/")
                print("Follow the setup guide: ~/Agents/email-agent/SETUP.md")
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_PATH}. "
                    "Download from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, 'w') as token_file:
            token_file.write(creds.to_json())
        print("Authentication successful. Token saved.")

    return creds


def get_gmail_service():
    """Return an authenticated Gmail API service instance."""
    creds = authenticate()
    service = build('gmail', 'v1', credentials=creds)
    return service


if __name__ == '__main__':
    print("Running Gmail authentication flow...")
    service = get_gmail_service()
    profile = service.users().getProfile(userId='me').execute()
    print(f"Authenticated as: {profile['emailAddress']}")
    print(f"Total messages: {profile['messagesTotal']}")
