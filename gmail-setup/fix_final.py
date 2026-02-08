#!/usr/bin/env python3
"""
Properly add test user AND publish to production.
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
PROJECT_ID = "marc-byers-email-agent"
EMAIL = "m.byers2@gmail.com"
SS = HOME / "Agents" / "gmail-setup"


def main():
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

        # === Fix 1: Add test user ===
        print("=== Adding test user ===")
        page.goto(
            f"https://console.cloud.google.com/auth/audience?project={PROJECT_ID}",
            wait_until="domcontentloaded", timeout=60000
        )
        time.sleep(8)

        # Click "+ Add users"
        try:
            page.locator('button:has-text("Add users")').first.click()
            time.sleep(3)
            print("Opened Add users dialog")
        except Exception as e:
            print(f"Could not click Add users: {e}")

        # The dialog has an input field with "0 / 100" counter
        # It's NOT the search bar - it's inside the side panel/dialog
        # Use a more specific selector for the dialog input
        page.screenshot(path=str(SS / "dialog_open.png"))

        filled = False
        # Try to find the input within the dialog panel (not the top search bar)
        for sel in [
            'div.cdk-overlay-pane input',
            'mat-drawer input',
            'mat-sidenav input',
            'aside input',
            'div[role="dialog"] input',
            'div.mat-drawer-inner-container input',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.fill(EMAIL)
                    time.sleep(1)
                    print(f"Filled email via: {sel}")
                    filled = True
                    break
            except Exception:
                continue

        if not filled:
            # Try: click in the area below the warning text and above Save
            # The input field is at approximately the middle of the dialog
            # Get the Save button's position and click above it
            try:
                save_btn = page.locator('button:has-text("Save")').first
                box = save_btn.bounding_box()
                if box:
                    # Click about 70px above the Save button (where the input should be)
                    page.mouse.click(box['x'] + box['width'] / 2, box['y'] - 70)
                    time.sleep(1)
                    page.keyboard.type(EMAIL)
                    time.sleep(1)
                    filled = True
                    print("Filled email by clicking near Save button")
            except Exception as e:
                print(f"Position click failed: {e}")

        if filled:
            page.screenshot(path=str(SS / "email_entered.png"))
            # Press Enter to confirm the email chip
            page.keyboard.press("Enter")
            time.sleep(1)
            page.keyboard.press("Tab")
            time.sleep(1)

            # Click Save
            try:
                page.locator('button:has-text("Save")').first.click()
                time.sleep(5)
                print("Clicked Save")
            except Exception as e:
                print(f"Save failed: {e}")

        page.screenshot(path=str(SS / "after_add_user.png"))

        # Verify
        time.sleep(2)
        text = page.inner_text("body")
        if EMAIL in text:
            print(f"Verified: {EMAIL} is a test user!")
        else:
            print(f"Warning: {EMAIL} not visible in page text")

        # === Fix 2: Publish to production ===
        print("\n=== Publishing app to production ===")
        page.goto(
            f"https://console.cloud.google.com/auth/audience?project={PROJECT_ID}",
            wait_until="domcontentloaded", timeout=60000
        )
        time.sleep(8)

        # Look for "Make internal" or the publishing status toggle
        # On the Audience page, there might be a "Make internal" button
        # Or we need to look for a "Publish app" / "In production" toggle
        page.screenshot(path=str(SS / "audience_for_publish.png"))

        # Try clicking "Testing status" link on the branding page
        page.goto(
            f"https://console.cloud.google.com/auth/branding?project={PROJECT_ID}",
            wait_until="domcontentloaded", timeout=60000
        )
        time.sleep(8)

        # Look for "Testing status" link or "Publish" button
        for sel in [
            'a:has-text("Testing status")',
            'button:has-text("Publish")',
            'button:has-text("PUBLISH")',
            'a:has-text("Publish")',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(5)
                    print(f"Clicked: {sel}")
                    page.screenshot(path=str(SS / "publish_dialog.png"))

                    # Confirm publication
                    for confirm_sel in [
                        'button:has-text("Confirm")',
                        'button:has-text("CONFIRM")',
                        'button:has-text("Publish")',
                        'button:has-text("OK")',
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

        page.screenshot(path=str(SS / "final_state.png"))
        browser.close()

    return True


if __name__ == "__main__":
    main()
    print("\nDone! Check screenshots, then retry OAuth flow.")
