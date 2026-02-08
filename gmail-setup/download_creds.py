#!/usr/bin/env python3
"""Download the credentials JSON for the already-created OAuth client."""
import json
import re
import shutil
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
CONFIG_DIR = HOME / "Agents" / "email-agent" / "config"
PROJECT_ID = "marc-byers-email-agent"
CLIENT_ID = "911650340923-nsssfh1o73r1th2m599gv6dp0p0sflno.apps.googleusercontent.com"


def main():
    print("Downloading OAuth client credentials...")

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

        # Navigate to the clients list
        clients_url = f"https://console.cloud.google.com/auth/clients?project={PROJECT_ID}"
        print(f"Navigating to clients list...")
        page.goto(clients_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        page.screenshot(path=str(HOME / "Agents" / "gmail-setup" / "clients_list.png"))
        print(f"Page: {page.title()}")

        # Try to find and click the Desktop client to open its detail page
        found = False
        for selector in [
            f'text="{CLIENT_ID[:30]}"',
            'text="Desktop client 3"',
            'text="Desktop client"',
            'a:has-text("Desktop")',
            f'a[href*="nsssfh1"]',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(5)
                    found = True
                    print(f"Clicked client: {selector}")
                    break
            except Exception:
                continue

        if not found:
            # Try navigating directly to the client edit page
            # The URL pattern is: auth/clients/{client_id}/edit
            encoded_id = CLIENT_ID.replace(".", "%2E")
            detail_url = f"https://console.cloud.google.com/auth/clients/{CLIENT_ID}?project={PROJECT_ID}"
            print(f"Navigating directly to client detail...")
            page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)

        page.screenshot(path=str(HOME / "Agents" / "gmail-setup" / "client_detail.png"))
        print(f"Detail page: {page.title()}")
        print(f"URL: {page.url}")

        # Try to find Download JSON on this page
        downloaded = False
        for selector in [
            'button:has-text("Download JSON")',
            'button:has-text("DOWNLOAD JSON")',
            'a:has-text("Download JSON")',
            'text="Download JSON"',
            '[aria-label*="Download"]',
            'button:has-text("Download")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=3000):
                    print(f"Found download: {selector}")
                    try:
                        with page.expect_download(timeout=15000) as dl:
                            el.click()
                        d = dl.value
                        save_path = HOME / "Downloads" / d.suggested_filename
                        d.save_as(str(save_path))
                        print(f"Downloaded: {save_path}")
                        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(save_path, CONFIG_DIR / "credentials.json")
                        print(f"Saved to: {CONFIG_DIR / 'credentials.json'}")
                        downloaded = True
                        break
                    except Exception as e:
                        # Download might not trigger as file download - might be a blob URL
                        print(f"  Download event failed: {e}")
                        el.click()
                        time.sleep(3)
            except Exception:
                continue

        if not downloaded:
            # Extract from page text
            print("\nExtracting credentials from page...")
            text = page.inner_text("body")

            # Find client secret
            secret_match = re.search(r'(GOCSPX-[\w-]+)', text)

            # Also try other secret patterns
            if not secret_match:
                # Look for "Client secret" label followed by value
                secret_match = re.search(r'Client secret\s*([\w-]+)', text)

            if secret_match:
                client_secret = secret_match.group(1)
                print(f"Found secret: {client_secret[:15]}...")

                creds = {
                    "installed": {
                        "client_id": CLIENT_ID,
                        "project_id": PROJECT_ID,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                        "client_secret": client_secret,
                        "redirect_uris": ["http://localhost"]
                    }
                }
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(CONFIG_DIR / "credentials.json", "w") as f:
                    json.dump(creds, f, indent=2)
                print(f"Credentials saved to: {CONFIG_DIR / 'credentials.json'}")
                downloaded = True
            else:
                print("Could not find client secret in page text")
                # Save page text for debugging
                with open(HOME / "Agents" / "gmail-setup" / "page_text.txt", "w") as f:
                    f.write(text)
                print("Page text saved to page_text.txt")

        browser.close()
        return downloaded


if __name__ == "__main__":
    if main():
        print("\nCredentials ready!")
    else:
        print("\nFailed - check screenshots")
