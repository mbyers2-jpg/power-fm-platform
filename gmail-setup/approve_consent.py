#!/usr/bin/env python3
"""Automate the OAuth consent approval in the Playwright browser."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
SS = HOME / "Agents" / "gmail-setup"
EMAIL = "m.byers2@gmail.com"

# The OAuth URL from Chrome
OAUTH_URL = "https://accounts.google.com/o/oauth2/auth?access_type=offline&client_id=911650340923-nsssfh1o73r1th2m599gv6dp0p0sflno.apps.googleusercontent.com&redirect_uri=http%3A%2F%2Flocalhost%3A8089%2F&response_type=code&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.modify+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.labels+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.compose+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send&state=omsZ2syJSjkf682gMcdfrQOeyZOpBg"


def main():
    print("Automating OAuth consent approval...")

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

        # Navigate to the OAuth URL
        print("Opening OAuth consent page...")
        page.goto(OAUTH_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        page.screenshot(path=str(SS / "consent_start.png"))
        print(f"Page: {page.title()}")
        print(f"URL: {page.url[:100]}")

        # Step 1: Select account or sign in
        if "accountchooser" in page.url or "signin" in page.url:
            # Try clicking on the email account
            for sel in [
                f'div[data-email="{EMAIL}"]',
                f'text="{EMAIL}"',
                f'li:has-text("{EMAIL}")',
                f'div:has-text("{EMAIL}")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(5)
                        print(f"Selected account: {sel}")
                        break
                except Exception:
                    continue

            # If prompted for password
            if "challenge" in page.url or "pwd" in page.url:
                print("Password required - waiting for manual entry...")
                for i in range(60):
                    time.sleep(2)
                    if "consent" in page.url or "localhost" in page.url:
                        break
                    if i % 10 == 9:
                        print(f"  Still waiting... ({(i+1)*2}s)")

        page.screenshot(path=str(SS / "consent_mid.png"))
        print(f"After account select: {page.url[:100]}")

        # Step 2: Handle "Google hasn't verified this app" warning
        if "consent" in page.url:
            # Look for "Advanced" or "Show Advanced"
            for sel in [
                'text="Advanced"',
                'a:has-text("Advanced")',
                'button:has-text("Advanced")',
                '#details-button',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(2)
                        print(f"Clicked Advanced: {sel}")
                        break
                except Exception:
                    continue

            # Click "Go to <app name> (unsafe)"
            for sel in [
                'a:has-text("Go to")',
                'a:has-text("unsafe")',
                'text="Go to Desktop client 3 (unsafe)"',
                'a:has-text("Desktop client")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(5)
                        print(f"Clicked go to app: {sel}")
                        break
                except Exception:
                    continue

        page.screenshot(path=str(SS / "consent_perms.png"))
        print(f"After advanced: {page.url[:100]}")

        # Step 3: Allow permissions
        for sel in [
            'button:has-text("Allow")',
            'button:has-text("ALLOW")',
            'button:has-text("Continue")',
            'button:has-text("CONTINUE")',
            '#submit_approve_access',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(3)
                    print(f"Clicked: {sel}")
            except Exception:
                continue

        # There might be a second Allow button
        time.sleep(2)
        for sel in [
            'button:has-text("Allow")',
            'button:has-text("ALLOW")',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(3)
                    print(f"Clicked second: {sel}")
            except Exception:
                continue

        time.sleep(5)
        page.screenshot(path=str(SS / "consent_done.png"))
        print(f"Final URL: {page.url[:100]}")

        if "localhost" in page.url:
            print("\nOAuth callback received! Flow should complete.")
        else:
            print(f"\nNot on localhost yet. Current: {page.url[:100]}")

        browser.close()
        return "localhost" in page.url


if __name__ == "__main__":
    if main():
        print("\nConsent approved!")
    else:
        print("\nCheck screenshots for current state")
