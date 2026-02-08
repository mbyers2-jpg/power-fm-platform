#!/usr/bin/env python3
"""
Complete OAuth flow:
1. Start local server
2. Get the OAuth URL
3. Open it in Playwright (which is already logged in)
4. Approve consent automatically
5. Callback hits localhost, flow completes
"""
import json
import threading
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
CONFIG_DIR = HOME / "Agents" / "email-agent" / "config"
SS = HOME / "Agents" / "gmail-setup"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]

# Add email-agent venv to path
import sys
sys.path.insert(0, str(HOME / "Agents" / "email-agent" / "venv" / "lib" / "python3.9" / "site-packages"))


def approve_in_playwright(auth_url):
    """Open auth URL in Playwright browser and approve consent."""
    print(f"\n[Playwright] Opening consent page...")

    with sync_playwright() as p:
        temp_profile = HOME / "Agents" / "gmail-setup" / "pw-profile"
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(temp_profile),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        url = page.url
        print(f"[Playwright] Page: {page.title()}")
        print(f"[Playwright] URL: {url[:80]}")

        # Select account if on account chooser
        if "accountchooser" in url or "signin" in url:
            try:
                el = page.locator('div[data-email="m.byers2@gmail.com"]').first
                if el.is_visible(timeout=5000):
                    el.click()
                    time.sleep(5)
                    print("[Playwright] Selected account")
            except Exception:
                pass

        # Handle "app not verified" warning
        url = page.url
        if "warning" in url or "consent" in url:
            page.screenshot(path=str(SS / "oauth_warning.png"))

            # Click "Continue" (for unverified app warning)
            for sel in [
                'button:has-text("Continue")',
                'button:has-text("CONTINUE")',
                '#submit_approve_access',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(3)
                        print(f"[Playwright] Clicked: {sel}")
                except Exception:
                    continue

            # Handle scope approval page - check all checkboxes and click Allow
            time.sleep(2)
            url = page.url
            if "consent" in url and "localhost" not in url:
                page.screenshot(path=str(SS / "oauth_scopes.png"))

                # Check all scope checkboxes
                checkboxes = page.locator('input[type="checkbox"]').all()
                for cb in checkboxes:
                    try:
                        if cb.is_visible(timeout=500) and not cb.is_checked():
                            cb.check()
                            time.sleep(0.5)
                    except Exception:
                        continue

                # Click Continue/Allow
                for sel in [
                    'button:has-text("Continue")',
                    'button:has-text("Allow")',
                    'button:has-text("CONTINUE")',
                    'button:has-text("ALLOW")',
                    '#submit_approve_access',
                ]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=3000):
                            el.click()
                            time.sleep(5)
                            print(f"[Playwright] Clicked: {sel}")
                    except Exception:
                        continue

        # Wait for redirect to localhost
        for i in range(30):
            try:
                url = page.url
                if "localhost" in url:
                    print(f"[Playwright] Redirected to localhost!")
                    break
            except Exception:
                # Page might have closed/navigated
                break
            time.sleep(1)

        try:
            browser.close()
        except Exception:
            pass

    print("[Playwright] Done!")


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("=== Gmail OAuth Complete Flow ===\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CONFIG_DIR / "credentials.json"), SCOPES
    )

    # Build the auth URL manually
    auth_url, _ = flow.authorization_url(access_type="offline")
    print(f"Auth URL generated: {auth_url[:80]}...")

    # Start Playwright approval in a thread
    pw_thread = threading.Thread(target=approve_in_playwright, args=(auth_url,))
    pw_thread.start()

    # Run the local server (this blocks until callback is received)
    print("\n[Server] Waiting for OAuth callback on port 8089...")
    creds = flow.run_local_server(port=8089, open_browser=False)

    print("\n[Server] Got credentials!")

    # Save token
    token_path = CONFIG_DIR / "token.json"
    with open(token_path, "w") as f:
        f.write(creds.to_json())
    print(f"Token saved to: {token_path}")

    # Test Gmail
    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    print(f"\nConnected to Gmail as: {profile['emailAddress']}")
    print(f"Total messages: {profile['messagesTotal']:,}")
    print("\nGMAIL SETUP COMPLETE!")

    # Wait for playwright thread
    pw_thread.join(timeout=10)

    return True


if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\nAll done!")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
