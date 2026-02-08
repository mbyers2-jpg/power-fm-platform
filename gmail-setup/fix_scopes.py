#!/usr/bin/env python3
"""Add Gmail scopes to the OAuth consent screen's Data Access settings."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HOME = Path.home()
PROJECT_ID = "marc-byers-email-agent"
SS = HOME / "Agents" / "gmail-setup"

GMAIL_SCOPES = [
    "gmail.readonly",
    "gmail.modify",
    "gmail.labels",
    "gmail.compose",
    "gmail.send",
]


def main():
    print("Fixing OAuth consent screen - adding Gmail scopes...")

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

        # Navigate to Data Access page (scopes config)
        url = f"https://console.cloud.google.com/auth/scopes?project={PROJECT_ID}"
        print(f"Loading Data Access / Scopes page...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        page.screenshot(path=str(SS / "scopes_page.png"))
        print(f"Page: {page.title()}")
        print(f"URL: {page.url}")

        # Look for "Add or remove scopes" button
        for selector in [
            'button:has-text("Add or remove scopes")',
            'button:has-text("ADD OR REMOVE SCOPES")',
            'button:has-text("Add scopes")',
            'button:has-text("Edit")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(3)
                    print(f"Clicked: {selector}")
                    break
            except Exception:
                continue

        page.screenshot(path=str(SS / "scopes_dialog.png"))

        # Try to find a text input to type scopes or search for Gmail
        for input_sel in [
            'input[type="text"]',
            'input[placeholder*="filter"]',
            'input[placeholder*="search"]',
            'input[aria-label*="filter"]',
            'input[aria-label*="search"]',
        ]:
            try:
                el = page.locator(input_sel).first
                if el.is_visible(timeout=2000):
                    el.fill("gmail")
                    time.sleep(2)
                    print(f"Typed 'gmail' in: {input_sel}")
                    page.screenshot(path=str(SS / "scopes_search.png"))
                    break
            except Exception:
                continue

        # Check all Gmail scope checkboxes
        time.sleep(2)
        checkboxes = page.locator('mat-checkbox, input[type="checkbox"]').all()
        print(f"Found {len(checkboxes)} checkboxes")

        checked_count = 0
        for cb in checkboxes:
            try:
                if cb.is_visible(timeout=500):
                    # Check if it's related to gmail
                    parent_text = cb.evaluate("el => el.closest('tr, div, label')?.textContent || ''")
                    if "gmail" in parent_text.lower() or "mail" in parent_text.lower():
                        # Check if not already checked
                        is_checked = cb.evaluate("el => el.classList.contains('mat-mdc-checkbox-checked') || el.checked || el.getAttribute('aria-checked') === 'true'")
                        if not is_checked:
                            cb.click()
                            time.sleep(0.5)
                            checked_count += 1
                            print(f"  Checked: {parent_text[:60]}")
            except Exception:
                continue

        print(f"Checked {checked_count} Gmail scope checkboxes")
        page.screenshot(path=str(SS / "scopes_checked.png"))

        # If no checkboxes found, try manually entering scopes
        if checked_count == 0:
            print("No checkboxes found. Trying manual scope entry...")
            for input_sel in [
                'textarea',
                'input[placeholder*="scope"]',
                'input[aria-label*="scope"]',
            ]:
                try:
                    el = page.locator(input_sel).first
                    if el.is_visible(timeout=2000):
                        scopes_text = "\n".join([
                            "https://www.googleapis.com/auth/gmail.readonly",
                            "https://www.googleapis.com/auth/gmail.modify",
                            "https://www.googleapis.com/auth/gmail.labels",
                            "https://www.googleapis.com/auth/gmail.compose",
                            "https://www.googleapis.com/auth/gmail.send",
                        ])
                        el.fill(scopes_text)
                        time.sleep(1)
                        print(f"Entered scopes manually in: {input_sel}")
                        break
                except Exception:
                    continue

        # Click Update/Save
        for selector in [
            'button:has-text("Update")',
            'button:has-text("UPDATE")',
            'button:has-text("Save")',
            'button:has-text("SAVE")',
            'button:has-text("Add")',
            'button:has-text("Confirm")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(3)
                    print(f"Clicked save: {selector}")
                    break
            except Exception:
                continue

        # Check if there's a final Save on the main page
        time.sleep(2)
        for selector in [
            'button:has-text("Save")',
            'button:has-text("SAVE")',
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=2000):
                    el.click()
                    time.sleep(3)
                    print(f"Final save: {selector}")
                    break
            except Exception:
                continue

        page.screenshot(path=str(SS / "scopes_saved.png"))
        browser.close()
        print("Done!")
        return True


if __name__ == "__main__":
    main()
