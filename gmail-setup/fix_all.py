#!/usr/bin/env python3
"""
Fix all OAuth issues:
1. Ensure Gmail API is enabled
2. Add Gmail scopes to consent screen
3. Then run the OAuth flow
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
PROJECT_ID = "marc-byers-email-agent"
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

        # Step 1: Enable Gmail API
        print("=== Step 1: Enabling Gmail API ===")
        page.goto(
            f"https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={PROJECT_ID}",
            wait_until="domcontentloaded", timeout=60000
        )
        time.sleep(8)
        page.screenshot(path=str(SS / "gmail_api.png"))

        # Check if already enabled or needs enabling
        page_text = page.inner_text("body")
        if "MANAGE" in page_text or "Manage" in page_text or "API enabled" in page_text:
            print("  Gmail API already enabled!")
        else:
            for sel in ['button:has-text("Enable")', 'button:has-text("ENABLE")']:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(8)
                        print(f"  Enabled Gmail API via: {sel}")
                        break
                except Exception:
                    continue

        # Step 2: Add Gmail scopes to Data Access
        print("\n=== Step 2: Adding Gmail scopes ===")
        page.goto(
            f"https://console.cloud.google.com/auth/scopes?project={PROJECT_ID}",
            wait_until="domcontentloaded", timeout=60000
        )
        time.sleep(8)

        # Click "Add or remove scopes"
        try:
            page.locator('button:has-text("Add or remove scopes")').first.click()
            time.sleep(5)
            print("  Opened scopes dialog")
        except Exception as e:
            print(f"  Could not open scopes dialog: {e}")
            browser.close()
            return False

        page.screenshot(path=str(SS / "scopes_dialog2.png"))

        # The dialog has a filter/search and a table with checkboxes
        # First clear any existing filter
        try:
            search = page.locator('input[aria-label*="ilter"], input[aria-label*="earch"]').first
            if search.is_visible(timeout=2000):
                search.fill("")
                time.sleep(1)
                search.fill("gmail")
                time.sleep(3)
                print("  Filtered for 'gmail'")
        except Exception:
            pass

        page.screenshot(path=str(SS / "scopes_filtered.png"))

        # Now look for checkboxes in the table rows
        # The table has rows with: checkbox, API, Scope, User-facing description
        # Try to check all visible unchecked checkboxes after filtering for gmail
        rows = page.locator('table tbody tr, mat-row, div[role="row"]').all()
        print(f"  Found {len(rows)} rows")

        checked = 0
        for row in rows:
            try:
                text = row.inner_text()
                if "gmail" in text.lower():
                    # Find checkbox in this row
                    cb = row.locator('mat-checkbox, input[type="checkbox"], div[role="checkbox"]').first
                    if cb.is_visible(timeout=500):
                        is_checked = cb.get_attribute("aria-checked") == "true" or \
                                     "checked" in (cb.get_attribute("class") or "")
                        if not is_checked:
                            cb.click()
                            time.sleep(0.3)
                            checked += 1
                            print(f"  Checked: {text[:80]}")
            except Exception:
                continue

        if checked == 0:
            # Try alternative: maybe rows aren't in a table, look for any checkboxes
            print("  No table rows with gmail found. Trying all checkboxes...")

            # Scroll the dialog content
            dialog = page.locator('mat-dialog-container, div[role="dialog"], .cdk-overlay-pane').first
            try:
                dialog.evaluate("el => el.scrollTop = 0")
            except Exception:
                pass

            # Get all checkbox-like elements
            all_cbs = page.locator('[role="checkbox"], mat-checkbox, input[type="checkbox"]').all()
            print(f"  Found {len(all_cbs)} checkboxes total")

            for cb in all_cbs:
                try:
                    if cb.is_visible(timeout=300):
                        # Get nearby text
                        nearby = cb.evaluate("""el => {
                            let row = el.closest('tr, [role="row"], .scope-row, div[class*="row"]');
                            return row ? row.textContent : el.parentElement.textContent;
                        }""")
                        if "gmail" in nearby.lower():
                            is_checked = cb.get_attribute("aria-checked") == "true"
                            if not is_checked:
                                cb.click()
                                time.sleep(0.3)
                                checked += 1
                                print(f"  Checked: {nearby[:80]}")
                except Exception:
                    continue

        if checked == 0:
            # Last resort: manually enter scopes in the text area
            print("  Trying manual scope entry in textarea...")
            try:
                textarea = page.locator('textarea').first
                if textarea.is_visible(timeout=2000):
                    textarea.fill(
                        "https://www.googleapis.com/auth/gmail.readonly\n"
                        "https://www.googleapis.com/auth/gmail.modify\n"
                        "https://www.googleapis.com/auth/gmail.labels\n"
                        "https://www.googleapis.com/auth/gmail.compose\n"
                        "https://www.googleapis.com/auth/gmail.send"
                    )
                    time.sleep(1)

                    # Click "Add to table" or similar
                    for sel in [
                        'button:has-text("Add to table")',
                        'button:has-text("ADD TO TABLE")',
                        'button:has-text("Add")',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if btn.is_visible(timeout=2000):
                                btn.click()
                                time.sleep(2)
                                print(f"  Added scopes via: {sel}")
                                checked = 5
                                break
                        except Exception:
                            continue
            except Exception:
                pass

        page.screenshot(path=str(SS / "scopes_after_check.png"))

        # Click Update button
        print(f"  Checked {checked} scopes. Clicking Update...")
        for sel in [
            'button:has-text("Update")',
            'button:has-text("UPDATE")',
            'button:has-text("Save")',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(5)
                    print(f"  Clicked: {sel}")
                    break
            except Exception:
                continue

        # Final save on main page if needed
        for sel in ['button:has-text("Save")', 'button:has-text("SAVE")']:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    time.sleep(3)
                    print(f"  Final save: {sel}")
                    break
            except Exception:
                continue

        page.screenshot(path=str(SS / "scopes_final.png"))
        browser.close()

    return True


if __name__ == "__main__":
    if main():
        print("\nScopes configured! Check screenshots to verify.")
