#!/usr/bin/env python3
"""Navigate to client detail, scroll down to get the secret."""
import json
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
CONFIG_DIR = HOME / "Agents" / "email-agent" / "config"
PROJECT_ID = "marc-byers-email-agent"
CLIENT_ID = "911650340923-nsssfh1o73r1th2m599gv6dp0p0sflno.apps.googleusercontent.com"
SS = HOME / "Agents" / "gmail-setup"


def main():
    print("Getting client secret...")

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

        # Go to client detail page
        url = f"https://console.cloud.google.com/auth/clients/{CLIENT_ID}?project={PROJECT_ID}"
        print(f"Loading client detail page...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        # Scroll down to see Client secrets section
        print("Scrolling to Client secrets section...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)
        page.screenshot(path=str(SS / "scrolled_detail.png"))

        # Try to find the secret value - might need to click a show/copy button
        page_text = page.inner_text("body")

        # Save full page text for debugging
        with open(SS / "detail_text.txt", "w") as f:
            f.write(page_text)

        # Look for GOCSPX pattern (standard Google client secret format)
        secret_match = re.search(r'(GOCSPX-[\w-]+)', page_text)

        if not secret_match:
            # Try to click "show secret" or copy button near Client secrets
            print("Looking for show/copy secret button...")
            for selector in [
                'button[aria-label*="secret"]',
                'button[aria-label*="copy"]',
                'button:near(:text("Client secrets"))',
                'mat-icon:has-text("content_copy")',
                'button:has-text("Copy")',
                '[aria-label="Copy client secret"]',
                'button:has-text("Show")',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        time.sleep(2)
                        print(f"  Clicked: {selector}")
                        break
                except Exception:
                    continue

            # Re-read page text
            time.sleep(2)
            page_text = page.inner_text("body")
            secret_match = re.search(r'(GOCSPX-[\w-]+)', page_text)

        if not secret_match:
            # Try to find any copy buttons and get value from clipboard
            print("Trying copy buttons...")
            copy_buttons = page.locator('button[aria-label*="opy"]').all()
            print(f"  Found {len(copy_buttons)} copy buttons")

            # Also try the icon buttons near the secrets section
            icon_buttons = page.locator('mat-icon:has-text("content_copy")').all()
            print(f"  Found {len(icon_buttons)} copy icon buttons")

            for btn in icon_buttons + copy_buttons:
                try:
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        time.sleep(1)
                        # Try to get clipboard content
                        clip = page.evaluate("navigator.clipboard.readText().catch(() => '')")
                        if clip and len(clip) > 10:
                            print(f"  Got from clipboard: {clip[:20]}...")
                            if "GOCSPX" in clip:
                                secret_match = re.search(r'(GOCSPX-[\w-]+)', clip)
                                break
                except Exception:
                    continue

        if not secret_match:
            # Last resort: look for any long alphanumeric string near "secret"
            print("Searching for secret pattern in page text...")
            # Find text between "Client secrets" and next section
            secrets_section = re.search(r'Client secrets(.*?)(?:$|Additional|Delete)', page_text, re.DOTALL)
            if secrets_section:
                section_text = secrets_section.group(1)
                print(f"  Secrets section text: {section_text[:200]}")
                # Any GOCSPX or long token-like string
                any_secret = re.search(r'([A-Za-z0-9_-]{20,})', section_text)
                if any_secret:
                    print(f"  Potential secret: {any_secret.group(1)[:20]}...")

            # Also try: download JSON from the clients list
            print("\nTrying to download JSON from clients list page...")
            page.goto(f"https://console.cloud.google.com/auth/clients?project={PROJECT_ID}",
                      wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)

            page.screenshot(path=str(SS / "clients_list2.png"))

            # Look for download icons in the table
            download_links = page.locator('a:has-text("Download"), button[aria-label*="ownload"]').all()
            print(f"  Found {len(download_links)} download elements")

            # Try the three-dot menu or action buttons
            action_buttons = page.locator('button[aria-label*="action"], button[aria-label*="Action"], mat-icon:has-text("more_vert")').all()
            print(f"  Found {len(action_buttons)} action buttons")

            for btn in action_buttons:
                try:
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        time.sleep(2)
                        page.screenshot(path=str(SS / "action_menu.png"))
                        # Look for download in the menu
                        dl = page.locator('text="Download JSON"').first
                        if dl.is_visible(timeout=2000):
                            try:
                                with page.expect_download(timeout=15000) as download:
                                    dl.click()
                                d = download.value
                                save_path = HOME / "Downloads" / d.suggested_filename
                                d.save_as(str(save_path))
                                print(f"  Downloaded: {save_path}")

                                data = json.load(open(save_path))
                                secret = data.get("installed", data.get("web", {})).get("client_secret", "")
                                if secret:
                                    print(f"  Got secret from JSON: {secret[:15]}...")
                                    secret_match = re.match(r'(.*)', secret)
                                break
                            except Exception as e:
                                print(f"  Download failed: {e}")
                except Exception:
                    continue

        if secret_match:
            client_secret = secret_match.group(1)
            print(f"\nClient secret: {client_secret[:15]}...")

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
            print(f"Saved to: {CONFIG_DIR / 'credentials.json'}")
            browser.close()
            return True
        else:
            page.screenshot(path=str(SS / "no_secret.png"))
            print("\nCould not find client secret.")
            print("Check screenshots in ~/Agents/gmail-setup/")
            browser.close()
            return False


if __name__ == "__main__":
    if main():
        print("\nDone!")
    else:
        print("\nFailed")
