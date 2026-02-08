#!/usr/bin/env python3
"""
Browser automation to create OAuth Desktop client and download credentials.
Uses Playwright with the installed Chrome and existing Google login session.
"""

import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / "Agents" / "email-agent" / "config"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
DOWNLOADS = HOME / "Downloads"

# Chrome profile path
CHROME_PROFILE = HOME / "Library" / "Application Support" / "Google" / "Chrome"
# Use a temp copy to avoid conflicts
TEMP_PROFILE = HOME / "Agents" / "gmail-setup" / "chrome-profile"

PROJECT_ID = "marc-byers-email-agent"


def copy_chrome_profile():
    """Copy essential Chrome profile data for auth persistence."""
    default_profile = CHROME_PROFILE / "Default"
    temp_default = TEMP_PROFILE / "Default"

    if TEMP_PROFILE.exists():
        shutil.rmtree(TEMP_PROFILE, ignore_errors=True)

    temp_default.mkdir(parents=True, exist_ok=True)

    # Copy the key files that contain login session
    for item in ["Cookies", "Login Data", "Web Data", "Preferences",
                 "Local Storage", "Session Storage", "IndexedDB",
                 "Secure Preferences", "Network"]:
        src = default_profile / item
        dst = temp_default / item
        if src.exists():
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    # Copy Local State
    local_state = CHROME_PROFILE / "Local State"
    if local_state.exists():
        shutil.copy2(local_state, TEMP_PROFILE / "Local State")

    print(f"Profile copied to {TEMP_PROFILE}")


def run_browser_automation():
    """Automate Chrome to create OAuth credentials."""
    from playwright.sync_api import sync_playwright

    # First kill existing Chrome
    subprocess.run(["pkill", "-f", "Google Chrome"], capture_output=True)
    time.sleep(3)

    # Clean lock files
    for lock in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
        (CHROME_PROFILE / lock).unlink(missing_ok=True)

    # Copy profile
    copy_chrome_profile()

    # Snapshot downloads
    existing_downloads = set()
    if DOWNLOADS.exists():
        existing_downloads = {f.name for f in DOWNLOADS.iterdir()}

    print("Launching Chrome with Playwright...")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(TEMP_PROFILE),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            accept_downloads=True,
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # Navigate to credentials page
        print("Navigating to Google Cloud Console credentials page...")
        page.goto(
            f"https://console.cloud.google.com/apis/credentials?project={PROJECT_ID}",
            wait_until="networkidle",
            timeout=60000
        )
        time.sleep(5)

        print(f"Page title: {page.title()}")
        print(f"URL: {page.url}")

        # Check if we're on a login page
        if "accounts.google.com" in page.url:
            print("\nNot logged in. Need to authenticate first.")
            print("Please log in manually in the browser window that opened.")
            print("Waiting up to 120 seconds for login...")

            for i in range(60):
                time.sleep(2)
                if "console.cloud.google.com" in page.url:
                    print("Login successful!")
                    break
            else:
                print("Login timeout. Please try again.")
                browser.close()
                return False

            time.sleep(5)

        # Now we should be on the credentials page
        # Click "+ Create Credentials"
        print("\nLooking for 'Create credentials' button...")

        # Try multiple selectors for the Create Credentials button
        created = False
        for selector in [
            'text="Create credentials"',
            'text="+ Create credentials"',
            'button:has-text("Create credentials")',
            '[aria-label="Create credentials"]',
            'a:has-text("Create credentials")',
            'text="CREATE CREDENTIALS"',
        ]:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=3000):
                    print(f"Found button with selector: {selector}")
                    elem.click()
                    time.sleep(3)
                    created = True
                    break
            except Exception:
                continue

        if not created:
            # Try navigating directly to the create OAuth client page
            print("Navigating directly to OAuth client creation page...")
            page.goto(
                f"https://console.cloud.google.com/apis/credentials/oauthclient?project={PROJECT_ID}",
                wait_until="networkidle",
                timeout=30000
            )
            time.sleep(5)
        else:
            # Click "OAuth client ID" from the dropdown
            print("Looking for 'OAuth client ID' option...")
            for selector in [
                'text="OAuth client ID"',
                'a:has-text("OAuth client ID")',
                '[data-value="oauth"]',
            ]:
                try:
                    elem = page.locator(selector).first
                    if elem.is_visible(timeout=3000):
                        elem.click()
                        time.sleep(5)
                        break
                except Exception:
                    continue

        print(f"Current URL: {page.url}")
        print(f"Page title: {page.title()}")

        # Now on the OAuth client creation form
        # Select "Desktop app" application type
        print("\nSelecting 'Desktop app' type...")

        # Try to find and click the application type dropdown
        for selector in [
            'text="Desktop app"',
            'text="Desktop application"',
            '[aria-label="Application type"]',
            'mat-select',
            'select',
            'div[role="listbox"]',
        ]:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=3000):
                    elem.click()
                    time.sleep(2)
                    break
            except Exception:
                continue

        # If we clicked a dropdown, now select Desktop app
        for selector in [
            'text="Desktop app"',
            'mat-option:has-text("Desktop")',
            'option:has-text("Desktop")',
            'li:has-text("Desktop")',
            '[data-value="desktop"]',
        ]:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=3000):
                    elem.click()
                    time.sleep(2)
                    print("Selected Desktop app")
                    break
            except Exception:
                continue

        # Fill in name
        print("Setting name to 'Marc Agents'...")
        for selector in [
            'input[aria-label="Name"]',
            'input[name="name"]',
            'input[placeholder*="name"]',
            'input[type="text"]',
        ]:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=3000):
                    elem.clear()
                    elem.fill("Marc Agents")
                    print("Name set")
                    break
            except Exception:
                continue

        # Click Create button
        print("Clicking Create...")
        time.sleep(2)
        for selector in [
            'button:has-text("Create")',
            'text="CREATE"',
            'text="Create"',
            'button:has-text("SAVE")',
            'button:has-text("Save")',
        ]:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=3000):
                    elem.click()
                    print("Clicked Create")
                    time.sleep(8)
                    break
            except Exception:
                continue

        # Look for the download button on the success dialog
        print("Looking for download button...")
        time.sleep(5)

        for selector in [
            'text="DOWNLOAD JSON"',
            'text="Download JSON"',
            'button:has-text("Download")',
            'a:has-text("Download")',
            'text="DOWNLOAD"',
            '[aria-label="Download JSON"]',
        ]:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=5000):
                    with page.expect_download(timeout=30000) as download_info:
                        elem.click()
                    download = download_info.value
                    # Save the downloaded file
                    save_path = DOWNLOADS / download.suggested_filename
                    download.save_as(str(save_path))
                    print(f"Downloaded: {save_path}")

                    # Copy to email agent config
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(save_path, CREDENTIALS_PATH)
                    print(f"Credentials saved to: {CREDENTIALS_PATH}")
                    browser.close()
                    return True
            except Exception as e:
                print(f"  Selector {selector}: {e}")
                continue

        # If direct download didn't work, check for newly appeared links
        # Also try: the page might show client_id and client_secret inline
        print("\nTrying to extract credentials from page...")
        try:
            page_text = page.inner_text("body")

            # Look for client ID pattern
            import re
            client_id_match = re.search(r'(\d+-[\w]+\.apps\.googleusercontent\.com)', page_text)
            client_secret_match = re.search(r'(GOCSPX-[\w-]+)', page_text)

            if client_id_match and client_secret_match:
                client_id = client_id_match.group(1)
                client_secret = client_secret_match.group(1)
                print(f"Found client_id: {client_id[:30]}...")
                print(f"Found client_secret: {client_secret[:15]}...")

                credentials = {
                    "installed": {
                        "client_id": client_id,
                        "project_id": PROJECT_ID,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                        "client_secret": client_secret,
                        "redirect_uris": ["http://localhost"]
                    }
                }

                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(CREDENTIALS_PATH, "w") as f:
                    json.dump(credentials, f, indent=2)
                print(f"Credentials built and saved to: {CREDENTIALS_PATH}")
                browser.close()
                return True

        except Exception as e:
            print(f"Page text extraction failed: {e}")

        # Take a screenshot for debugging
        screenshot_path = HOME / "Agents" / "gmail-setup" / "page_state.png"
        page.screenshot(path=str(screenshot_path))
        print(f"\nScreenshot saved to: {screenshot_path}")
        print("Browser will stay open for 60 seconds for manual intervention...")

        # Watch for downloaded file
        print("Watching for credentials download...")
        for i in range(30):
            time.sleep(2)
            if DOWNLOADS.exists():
                for f in DOWNLOADS.iterdir():
                    if f.name not in existing_downloads and f.suffix == ".json":
                        try:
                            data = json.load(open(f))
                            if "installed" in data or "web" in data:
                                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(f, CREDENTIALS_PATH)
                                print(f"\nFound credentials: {f.name}")
                                print(f"Saved to: {CREDENTIALS_PATH}")
                                browser.close()
                                return True
                        except Exception:
                            continue

        browser.close()
        return False


def run_oauth_flow():
    """Run the Gmail OAuth2 authorization flow."""
    if not CREDENTIALS_PATH.exists():
        print("No credentials.json found!")
        return False

    print("\nStarting Gmail OAuth flow...")
    print("A browser window will open - sign in and click Allow")

    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.labels",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=8089)

    token_path = CONFIG_DIR / "token.json"
    with open(token_path, "w") as f:
        f.write(creds.to_json())

    print(f"Token saved to: {token_path}")
    return True


def test_gmail():
    """Test Gmail API connection."""
    token_path = CONFIG_DIR / "token.json"
    if not token_path.exists():
        return False

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.labels",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()

    print(f"\nConnected to Gmail as: {profile['emailAddress']}")
    print(f"Total messages: {profile['messagesTotal']:,}")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("  Gmail Credential Setup via Browser Automation")
    print("=" * 50)

    # Step 1: Create credentials via browser
    if CREDENTIALS_PATH.exists():
        print(f"\ncredentials.json already exists at {CREDENTIALS_PATH}")
        success = True
    else:
        success = run_browser_automation()

    if not success:
        print("\nBrowser automation could not complete. Check the screenshot at:")
        print(f"  ~/Agents/gmail-setup/page_state.png")
        sys.exit(1)

    # Step 2: OAuth flow
    token_path = CONFIG_DIR / "token.json"
    if not token_path.exists():
        try:
            if not run_oauth_flow():
                print("OAuth flow failed")
                sys.exit(1)
        except Exception as e:
            print(f"OAuth error: {e}")
            sys.exit(1)

    # Step 3: Test
    try:
        if test_gmail():
            print("\nSetup complete! Gmail API is operational.")
    except Exception as e:
        print(f"Test failed: {e}")
