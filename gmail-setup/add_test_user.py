#!/usr/bin/env python3
"""Add m.byers2@gmail.com as test user on the OAuth consent screen, then run OAuth flow."""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
CONFIG_DIR = HOME / "Agents" / "email-agent" / "config"
PROJECT_ID = "marc-byers-email-agent"
EMAIL = "m.byers2@gmail.com"
SS = HOME / "Agents" / "gmail-setup"


def main():
    print("Step 1: Adding test user to OAuth consent screen...")

    with sync_playwright() as p:
        temp_profile = HOME / "Agents" / "gmail-setup" / "pw-profile"
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(temp_profile),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            accept_downloads=True,
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # Navigate to Audience page (where test users are managed)
        url = f"https://console.cloud.google.com/auth/audience?project={PROJECT_ID}"
        print(f"Loading audience/test users page...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        page.screenshot(path=str(SS / "audience_page.png"))
        print(f"Page: {page.title()}")

        # Look for "Add users" button
        added = False
        for selector in [
            'button:has-text("Add users")',
            'button:has-text("ADD USERS")',
            'button:has-text("Add user")',
            'a:has-text("Add users")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(3)
                    print(f"Clicked: {selector}")

                    page.screenshot(path=str(SS / "add_user_dialog.png"))

                    # Find the email input and type the email
                    for input_sel in [
                        'input[type="email"]',
                        'input[placeholder*="email"]',
                        'input[aria-label*="email"]',
                        'input[type="text"]',
                        'textarea',
                    ]:
                        try:
                            inp = page.locator(input_sel).first
                            if inp.is_visible(timeout=2000):
                                inp.fill(EMAIL)
                                time.sleep(1)
                                print(f"Entered email: {EMAIL}")
                                break
                        except Exception:
                            continue

                    # Click Save/Add/Confirm
                    time.sleep(1)
                    for btn_sel in [
                        'button:has-text("Save")',
                        'button:has-text("SAVE")',
                        'button:has-text("Add")',
                        'button:has-text("ADD")',
                        'button:has-text("Confirm")',
                        'button:has-text("OK")',
                    ]:
                        try:
                            btn = page.locator(btn_sel).first
                            if btn.is_visible(timeout=2000):
                                btn.click()
                                time.sleep(3)
                                print(f"Clicked save: {btn_sel}")
                                added = True
                                break
                        except Exception:
                            continue
                    break
            except Exception:
                continue

        if not added:
            # Check if user is already listed
            page_text = page.inner_text("body")
            if EMAIL in page_text:
                print(f"{EMAIL} already in test users list!")
                added = True
            else:
                page.screenshot(path=str(SS / "audience_no_add.png"))
                print("Could not find Add users button. Check screenshot.")

        page.screenshot(path=str(SS / "audience_after.png"))
        browser.close()
        return added


if __name__ == "__main__":
    if main():
        print("\nTest user added! Now running OAuth flow...")
        print()

        # Run OAuth flow
        import subprocess
        import sys

        venv_python = HOME / "Agents" / "email-agent" / "venv" / "bin" / "python3"
        result = subprocess.run([
            str(venv_python), "-c", """
from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path
import json

CONFIG = Path.home() / "Agents" / "email-agent" / "config"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]

print("Starting Gmail OAuth flow...")
print("Browser opening - sign in and click Allow")
print()

flow = InstalledAppFlow.from_client_secrets_file(str(CONFIG / "credentials.json"), SCOPES)
creds = flow.run_local_server(port=8089, open_browser=True)

with open(CONFIG / "token.json", "w") as f:
    f.write(creds.to_json())
print(f"Token saved to: {CONFIG / 'token.json'}")

from googleapiclient.discovery import build
service = build("gmail", "v1", credentials=creds)
profile = service.users().getProfile(userId="me").execute()
print(f"Connected as: {profile['emailAddress']}")
print(f"Messages: {profile['messagesTotal']:,}")
print("Gmail setup complete!")
"""
        ], timeout=180)

        sys.exit(result.returncode)
    else:
        print("\nFailed to add test user")
