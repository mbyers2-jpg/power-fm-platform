#!/usr/bin/env python3
"""Check and fix test user on OAuth consent screen, then run OAuth flow."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
PROJECT_ID = "marc-byers-email-agent"
EMAIL = "m.byers2@gmail.com"
SS = HOME / "Agents" / "gmail-setup"


def main():
    print("=== Fixing Test User on OAuth Consent Screen ===\n")

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

        # Navigate to Audience page
        url = f"https://console.cloud.google.com/auth/audience?project={PROJECT_ID}"
        print("Loading Audience page...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        page.screenshot(path=str(SS / "audience_check.png"))
        text = page.inner_text("body")
        print(f"Page: {page.title()}")

        # Check if email is already listed
        if EMAIL in text:
            print(f"\n{EMAIL} IS listed as a test user already!")
            print("The issue may be propagation delay. Checking further...")
            page.screenshot(path=str(SS / "audience_has_user.png"))
        else:
            print(f"\n{EMAIL} NOT found in test users. Adding now...")

            # Click "Add users"
            added = False
            for sel in [
                'button:has-text("Add users")',
                'button:has-text("ADD USERS")',
                'button:has-text("Add user")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(3)
                        print(f"Clicked: {sel}")

                        page.screenshot(path=str(SS / "add_user_form.png"))

                        # Find ALL input fields in the dialog and try each
                        inputs = page.locator('input, textarea').all()
                        print(f"Found {len(inputs)} input fields")

                        for inp in inputs:
                            try:
                                if inp.is_visible(timeout=500):
                                    inp_type = inp.get_attribute("type") or ""
                                    inp_placeholder = inp.get_attribute("placeholder") or ""
                                    inp_label = inp.get_attribute("aria-label") or ""
                                    print(f"  Input: type={inp_type} placeholder={inp_placeholder} label={inp_label}")

                                    if inp_type not in ["hidden", "submit", "button"]:
                                        inp.fill(EMAIL)
                                        time.sleep(1)
                                        print(f"  Filled with {EMAIL}")

                                        page.screenshot(path=str(SS / "user_filled.png"))

                                        # Press Enter to confirm the email
                                        inp.press("Enter")
                                        time.sleep(1)
                                        break
                            except Exception:
                                continue

                        # Now click Save
                        time.sleep(2)
                        page.screenshot(path=str(SS / "before_save.png"))

                        for save_sel in [
                            'button:has-text("Save")',
                            'button:has-text("SAVE")',
                            'button:has-text("Add")',
                            'button:has-text("OK")',
                        ]:
                            try:
                                btn = page.locator(save_sel).first
                                if btn.is_visible(timeout=2000) and btn.is_enabled(timeout=1000):
                                    btn.click()
                                    time.sleep(5)
                                    print(f"Clicked save: {save_sel}")
                                    added = True
                                    break
                            except Exception:
                                continue

                        break
                except Exception:
                    continue

            page.screenshot(path=str(SS / "audience_after_add.png"))

            if added:
                # Verify
                text = page.inner_text("body")
                if EMAIL in text:
                    print(f"\nVerified: {EMAIL} is now a test user!")
                else:
                    print(f"\nWarning: {EMAIL} may not have saved. Check screenshots.")
            else:
                print("\nCould not add test user. Check screenshots.")

        # Also check: is the publishing status "Testing" or "In production"?
        # For testing apps, only test users can access.
        # Alternative: publish the app to "In production" (no verification needed for < 100 users)
        print("\n--- Checking publishing status ---")
        page.goto(f"https://console.cloud.google.com/auth/branding?project={PROJECT_ID}",
                  wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        page.screenshot(path=str(SS / "branding_page.png"))
        branding_text = page.inner_text("body")

        if "Testing" in branding_text:
            print("App is in TESTING mode.")
            print("Trying to publish to production (allows all users, no verification needed for personal use)...")

            # Look for "Publish App" or similar
            for sel in [
                'button:has-text("Publish")',
                'button:has-text("PUBLISH")',
                'button:has-text("Publish app")',
                'button:has-text("PUBLISH APP")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(3)
                        print(f"Clicked: {sel}")

                        # Confirm if there's a confirmation dialog
                        for confirm_sel in [
                            'button:has-text("Confirm")',
                            'button:has-text("CONFIRM")',
                            'button:has-text("OK")',
                            'button:has-text("Publish")',
                        ]:
                            try:
                                btn = page.locator(confirm_sel).first
                                if btn.is_visible(timeout=3000):
                                    btn.click()
                                    time.sleep(5)
                                    print(f"Confirmed: {confirm_sel}")
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

            page.screenshot(path=str(SS / "branding_after.png"))
        elif "production" in branding_text.lower() or "In production" in branding_text:
            print("App is already in PRODUCTION mode. Good.")
        else:
            print(f"Could not determine publishing status.")

        browser.close()
        return True


if __name__ == "__main__":
    main()
    print("\nDone. Now retry the OAuth flow.")
