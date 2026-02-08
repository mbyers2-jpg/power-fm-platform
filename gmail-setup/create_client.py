#!/usr/bin/env python3
"""
Create OAuth Desktop client via Playwright.
Handles Google login, then automates form creation.
"""
import json
import shutil
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
CONFIG_DIR = HOME / "Agents" / "email-agent" / "config"
PROJECT_ID = "marc-byers-email-agent"
EMAIL = "m.byers2@gmail.com"
SCREENSHOT_DIR = HOME / "Agents" / "gmail-setup"


def screenshot(page, name):
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    return path


def wait_for_console(page, timeout_s=180):
    """Wait until we're on console.cloud.google.com (not sign-in)."""
    for i in range(timeout_s // 2):
        time.sleep(2)
        url = page.url
        title = page.title()
        # Must be on console AND not on a signin redirect
        if "console.cloud.google.com" in url and "accounts.google.com" not in url:
            return True
        if i % 15 == 14:
            print(f"  Still waiting for login... ({(i+1)*2}s) - {url[:80]}")
    return False


def main():
    print("=" * 50)
    print("  Gmail OAuth Client Creator")
    print("=" * 50)

    with sync_playwright() as p:
        temp_profile = HOME / "Agents" / "gmail-setup" / "pw-profile"
        temp_profile.mkdir(parents=True, exist_ok=True)

        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(temp_profile),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled",
                  "--window-size=1200,900"],
            ignore_default_args=["--enable-automation"],
            accept_downloads=True,
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # Navigate to OAuth client creation (new Auth Platform URL)
        target = f"https://console.cloud.google.com/auth/clients/create?project={PROJECT_ID}"
        print(f"\nNavigating to Cloud Console...")
        page.goto(target, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Handle login if needed
        url = page.url
        if "accounts.google.com" in url or "signin" in url.lower():
            print("\nGoogle login required. Entering email...")

            # Try to fill email
            try:
                email_input = page.locator('input[type="email"]').first
                if email_input.is_visible(timeout=5000):
                    email_input.fill(EMAIL)
                    time.sleep(1)
                    # Click Next
                    page.locator('button:has-text("Next")').first.click()
                    time.sleep(3)
                    print(f"  Email entered: {EMAIL}")
                    print()
                    print(">>> Please enter your PASSWORD in the browser window <<<")
                    print(">>> Then click Next / approve any prompts <<<")
                    print()
            except Exception as e:
                print(f"  Could not auto-fill email: {e}")
                print(">>> Please log in manually in the browser window <<<")

            # Wait for login to complete
            if not wait_for_console(page):
                screenshot(page, "login_timeout")
                print("Login timed out. Screenshot saved.")
                browser.close()
                return False

            print("Login successful!")
            time.sleep(5)

        # Ensure we're on the OAuth client creation page
        url = page.url
        print(f"\nCurrent: {url[:100]}")
        if "clients/create" not in url:
            print("Navigating to OAuth client creation...")
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)

        print(f"Page: {page.title()}")
        ss = screenshot(page, "form_loaded")
        print(f"Screenshot: {ss}")

        # === FILL THE FORM ===

        # Step 1: Application type dropdown
        print("\n--- Step 1: Select 'Desktop app' ---")
        dropdown_opened = False

        for selector in [
            'div[role="listbox"]',
            '[aria-label="Application type"]',
            'mat-select',
            'div.mat-mdc-select-trigger',
            'mat-form-field:has-text("Application type")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=2000):
                    el.click()
                    time.sleep(2)
                    dropdown_opened = True
                    print(f"  Opened dropdown: {selector}")
                    break
            except Exception:
                continue

        if not dropdown_opened:
            # Fallback: try clicking the text label
            try:
                page.click('text="Application type"', timeout=3000)
                time.sleep(2)
                dropdown_opened = True
                print("  Opened dropdown via label text")
            except Exception:
                print("  Could not find dropdown. Taking screenshot...")
                screenshot(page, "no_dropdown")

        # Select Desktop app
        desktop_selected = False
        for selector in [
            'mat-option:has-text("Desktop app")',
            'div[role="option"]:has-text("Desktop app")',
            'li:has-text("Desktop app")',
            'text="Desktop app"',
            'span:has-text("Desktop app")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(2)
                    desktop_selected = True
                    print(f"  Selected: {selector}")
                    break
            except Exception:
                continue

        if not desktop_selected:
            print("  Desktop app not found in dropdown options")
            screenshot(page, "dropdown_open")

        # Step 2: Name
        print("\n--- Step 2: Set name ---")
        for selector in [
            'input[formcontrolname="name"]',
            'input[aria-label="Name"]',
            'input[name="displayName"]',
            'input[name="name"]',
            'input[type="text"]',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=2000):
                    el.triple_click()  # Select all existing text
                    el.fill("Marc Agents")
                    print(f"  Name set via: {selector}")
                    break
            except Exception:
                continue

        screenshot(page, "form_filled")

        # Step 3: Create
        print("\n--- Step 3: Click Create ---")
        time.sleep(1)
        created = False
        for selector in [
            'button:has-text("Create")',
            'button:has-text("CREATE")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=3000):
                    el.click()
                    print(f"  Clicked: {selector}")
                    created = True
                    time.sleep(10)
                    break
            except Exception:
                continue

        if not created:
            print("  Create button not found")
            screenshot(page, "no_create")
            browser.close()
            return False

        screenshot(page, "after_create")
        print(f"  Page now: {page.title()}")

        # Step 4: Download
        print("\n--- Step 4: Download JSON ---")
        downloaded = False

        for selector in [
            'button:has-text("DOWNLOAD JSON")',
            'button:has-text("Download JSON")',
            'a:has-text("download")',
            'button:has-text("Download")',
            '[aria-label*="Download"]',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=5000):
                    with page.expect_download(timeout=30000) as dl:
                        el.click()
                    d = dl.value
                    save_path = HOME / "Downloads" / d.suggested_filename
                    d.save_as(str(save_path))
                    print(f"  Downloaded: {save_path}")
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(save_path, CONFIG_DIR / "credentials.json")
                    print(f"  Saved to: {CONFIG_DIR / 'credentials.json'}")
                    downloaded = True
                    break
            except Exception as e:
                print(f"  {selector}: {e}")
                continue

        if not downloaded:
            # Try extracting client_id and secret from page
            print("\n  Extracting from page text...")
            import re
            try:
                text = page.inner_text("body")
                cid = re.search(r'(\d+-[\w]+\.apps\.googleusercontent\.com)', text)
                csec = re.search(r'(GOCSPX-[\w-]+)', text)
                if cid and csec:
                    creds = {
                        "installed": {
                            "client_id": cid.group(1),
                            "project_id": PROJECT_ID,
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                            "client_secret": csec.group(1),
                            "redirect_uris": ["http://localhost"]
                        }
                    }
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    with open(CONFIG_DIR / "credentials.json", "w") as f:
                        json.dump(creds, f, indent=2)
                    print(f"  Built from page text!")
                    downloaded = True
                else:
                    print(f"  client_id found: {bool(cid)}, secret found: {bool(csec)}")
            except Exception as e:
                print(f"  {e}")

        if not downloaded:
            screenshot(page, "download_failed")
            print("\n  Waiting 60s for manual download...")
            existing = set()
            if (HOME / "Downloads").exists():
                existing = {f.name for f in (HOME / "Downloads").iterdir()}
            for i in range(30):
                time.sleep(2)
                for f in (HOME / "Downloads").iterdir():
                    if f.name not in existing and f.suffix == ".json":
                        try:
                            data = json.load(open(f))
                            if "installed" in data or "web" in data:
                                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(f, CONFIG_DIR / "credentials.json")
                                print(f"\n  Found: {f.name}")
                                downloaded = True
                                break
                        except Exception:
                            continue
                if downloaded:
                    break

        browser.close()
        return downloaded


if __name__ == "__main__":
    success = main()
    if success:
        print("\nCredentials ready!")
    else:
        print("\nFailed - check screenshots in ~/Agents/gmail-setup/")
