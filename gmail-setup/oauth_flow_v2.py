#!/usr/bin/env python3
"""
OAuth flow v2 - fixes checkbox handling on consent page.
Strategy: Click "Select all" checkbox, verify all checked, then Continue.
"""
import io
import json
import re
import sys
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
sys.path.insert(0, str(HOME / "Agents" / "email-agent" / "venv" / "lib" / "python3.9" / "site-packages"))


class URLCapture(io.TextIOWrapper):
    """Captures the OAuth URL printed by run_local_server."""
    def __init__(self, original):
        self.original = original
        self.captured_url = None

    def write(self, text):
        self.original.write(text)
        self.original.flush()
        if text and "accounts.google.com" in text:
            match = re.search(r'(https://accounts\.google\.com\S+)', text)
            if match:
                self.captured_url = match.group(1)
        return len(text)

    def flush(self):
        self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


def run_server(flow, result_holder, url_capture):
    """Run OAuth server in background thread."""
    old_stdout = sys.stdout
    sys.stdout = url_capture
    try:
        # prompt='consent' forces re-consent even if previously approved with fewer scopes
        creds = flow.run_local_server(port=8089, open_browser=False, prompt='consent')
        result_holder["creds"] = creds
        result_holder["success"] = True
    except Exception as e:
        result_holder["error"] = str(e)
        result_holder["success"] = False
    finally:
        sys.stdout = old_stdout


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("=== Gmail OAuth Flow v2 ===\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CONFIG_DIR / "credentials.json"), SCOPES
    )

    result = {}
    url_capture = URLCapture(sys.__stdout__)

    # Start server thread
    server_thread = threading.Thread(target=run_server, args=(flow, result, url_capture))
    server_thread.daemon = True
    server_thread.start()

    # Wait for URL to appear
    print("Waiting for OAuth URL...")
    for _ in range(30):
        if url_capture.captured_url:
            break
        time.sleep(1)

    auth_url = url_capture.captured_url
    if not auth_url:
        print("ERROR: Could not capture OAuth URL")
        return False

    print(f"Got URL: {auth_url[:80]}...")

    # Now use Playwright to approve consent
    with sync_playwright() as p:
        profile = SS / "pw-profile"
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # Navigate to OAuth URL
        print("\n[1] Loading consent page...")
        page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        url = page.url
        print(f"Page: {page.title()}")
        print(f"URL: {url[:80]}")

        # Step: Account chooser
        if "accountchooser" in url or "signin" in url:
            print("\n[2] Selecting account...")
            try:
                el = page.locator('div[data-email="m.byers2@gmail.com"]').first
                if el.is_visible(timeout=5000):
                    el.click()
                    time.sleep(5)
                    print("Selected m.byers2@gmail.com")
            except Exception:
                pass

        # Step: Unverified app warning - click Advanced then "Go to email agent (unsafe)"
        url = page.url
        print(f"\n[3] Current URL: {url[:80]}")
        page.screenshot(path=str(SS / "v2_step3.png"))

        # Look for "Advanced" link first (unverified app warning page)
        for sel in [
            '#details-button',                    # "Advanced" expander
            'button:has-text("Advanced")',
            'a:has-text("Advanced")',
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

        # Click "Go to email agent (unsafe)" link
        for sel in [
            'a:has-text("Go to")',
            'a:has-text("unsafe")',
            'a[id*="proceed"]',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(5)
                    print(f"Clicked proceed: {sel}")
                    break
            except Exception:
                continue

        # Also try clicking Continue button (alternative warning page style)
        for sel in [
            'button:has-text("Continue")',
            'button:has-text("CONTINUE")',
            '#submit_approve_access',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    time.sleep(3)
                    print(f"Clicked: {sel}")
            except Exception:
                continue

        url = page.url
        print(f"\n[4] After warning handling, URL: {url[:80]}")
        page.screenshot(path=str(SS / "v2_step4.png"))

        # Step: Consent/scopes page - check all checkboxes
        if "consent" in url and not url.startswith("http://localhost"):
            print("\n[5] On consent page - handling checkboxes...")
            time.sleep(3)
            page.screenshot(path=str(SS / "v2_consent_before.png"))

            # Strategy 1: Find and click "Select all" checkbox first
            # It's typically the first checkbox or has different x-position

            # Get all checkbox info
            checkbox_info = page.evaluate("""() => {
                const results = [];
                // Check for input[type=checkbox]
                document.querySelectorAll('input[type="checkbox"]').forEach((cb, i) => {
                    const rect = cb.getBoundingClientRect();
                    const label = cb.closest('label');
                    const labelText = label ? label.textContent.trim().substring(0, 50) : '';
                    results.push({
                        type: 'input', index: i,
                        x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                        width: rect.width, height: rect.height,
                        visible: rect.width > 0 && rect.height > 0,
                        checked: cb.checked,
                        id: cb.id || '',
                        name: cb.name || '',
                        label: labelText
                    });
                });
                // Check for role=checkbox
                document.querySelectorAll('[role="checkbox"]').forEach((cb, i) => {
                    const rect = cb.getBoundingClientRect();
                    results.push({
                        type: 'role', index: i,
                        x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                        width: rect.width, height: rect.height,
                        visible: rect.width > 0 && rect.height > 0,
                        checked: cb.getAttribute('aria-checked') === 'true',
                        id: cb.id || '',
                        label: cb.textContent?.trim().substring(0, 50) || ''
                    });
                });
                return results;
            }""")

            print(f"Found {len(checkbox_info)} checkbox elements:")
            for cb in checkbox_info:
                print(f"  [{cb.get('type')}#{cb.get('index')}] pos=({cb['x']:.0f},{cb['y']:.0f}) "
                      f"visible={cb['visible']} checked={cb['checked']} "
                      f"label='{cb.get('label','')[:40]}'")

            visible_unchecked = [cb for cb in checkbox_info if cb['visible'] and not cb['checked']]
            print(f"\nVisible unchecked: {len(visible_unchecked)}")

            if visible_unchecked:
                # Find the "Select all" - it's usually at a different x position or the first one
                # Group by x-coordinate to find the outlier
                x_positions = [cb['x'] for cb in visible_unchecked]
                from collections import Counter
                x_counts = Counter([round(x) for x in x_positions])

                # The "Select all" is likely the one with a unique x position
                # Or it's simply the first checkbox
                select_all = None
                if len(x_counts) > 1:
                    # Find the x position that appears only once (likely Select all)
                    for x_val, count in x_counts.items():
                        if count == 1:
                            for cb in visible_unchecked:
                                if round(cb['x']) == x_val:
                                    select_all = cb
                                    break
                            if select_all:
                                break

                if select_all:
                    print(f"\nClicking 'Select all' at ({select_all['x']:.0f}, {select_all['y']:.0f})...")
                    # Click with small delay and verify
                    page.mouse.click(select_all['x'], select_all['y'])
                    time.sleep(2)

                    # Verify
                    after = page.evaluate("""() => {
                        const cbs = document.querySelectorAll('input[type="checkbox"]');
                        const checked = Array.from(cbs).filter(cb => cb.checked && cb.getBoundingClientRect().width > 0).length;
                        const total = Array.from(cbs).filter(cb => cb.getBoundingClientRect().width > 0).length;
                        return {checked, total};
                    }""")
                    print(f"After Select all: {after['checked']}/{after['total']} checked")

                    if after['checked'] < after['total']:
                        print("Select all didn't check everything. Clicking remaining...")
                        # Click individual remaining unchecked checkboxes
                        remaining = page.evaluate("""() => {
                            const results = [];
                            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                                const rect = cb.getBoundingClientRect();
                                if (rect.width > 0 && !cb.checked) {
                                    results.push({x: rect.x + rect.width/2, y: rect.y + rect.height/2});
                                }
                            });
                            return results;
                        }""")
                        for r in remaining:
                            page.mouse.click(r['x'], r['y'])
                            time.sleep(1)
                else:
                    print("\nNo distinct 'Select all' found. Clicking all checkboxes individually...")
                    for i, cb in enumerate(visible_unchecked):
                        print(f"  Clicking checkbox {i+1}/{len(visible_unchecked)} at ({cb['x']:.0f}, {cb['y']:.0f})...")
                        page.mouse.click(cb['x'], cb['y'])
                        time.sleep(1.5)  # Longer delay between clicks

                # Also try using JavaScript to force-check all checkboxes
                print("\nForce-checking via JavaScript as backup...")
                page.evaluate("""() => {
                    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                        if (!cb.checked && cb.getBoundingClientRect().width > 0) {
                            cb.checked = true;
                            cb.dispatchEvent(new Event('change', {bubbles: true}));
                            cb.dispatchEvent(new Event('click', {bubbles: true}));
                            cb.dispatchEvent(new Event('input', {bubbles: true}));
                        }
                    });
                }""")
                time.sleep(2)

                # Final verification
                final_check = page.evaluate("""() => {
                    const cbs = document.querySelectorAll('input[type="checkbox"]');
                    return Array.from(cbs).filter(cb => cb.getBoundingClientRect().width > 0).map(cb => ({
                        checked: cb.checked,
                        label: cb.closest('label')?.textContent?.trim().substring(0, 60) || cb.name || 'unknown'
                    }));
                }""")
                print(f"\nFinal checkbox states:")
                all_checked = True
                for fc in final_check:
                    status = "CHECKED" if fc['checked'] else "UNCHECKED"
                    print(f"  [{status}] {fc['label']}")
                    if not fc['checked']:
                        all_checked = False

                if not all_checked:
                    print("\nWARNING: Not all checkboxes checked! Trying label clicks...")
                    # Try clicking the label elements instead
                    page.evaluate("""() => {
                        document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                            if (!cb.checked) {
                                const label = cb.closest('label') || document.querySelector(`label[for="${cb.id}"]`);
                                if (label) label.click();
                            }
                        });
                    }""")
                    time.sleep(2)

            page.screenshot(path=str(SS / "v2_consent_checked.png"))

            # Now click Continue/Allow
            print("\n[6] Clicking Continue/Allow...")
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
                        print(f"Clicked: {sel}")
                        break
                except Exception:
                    continue

        # Wait for redirect to localhost
        print("\n[7] Waiting for redirect to localhost...")
        for i in range(30):
            try:
                url = page.url
                if url.startswith("http://localhost"):
                    print(f"Redirected to localhost!")
                    break
            except Exception:
                break
            time.sleep(1)

        page.screenshot(path=str(SS / "v2_final.png"))

        try:
            browser.close()
        except Exception:
            pass

    # Wait for server thread
    print("\n[8] Waiting for token exchange...")
    server_thread.join(timeout=30)

    if result.get("success"):
        creds = result["creds"]
        token_path = CONFIG_DIR / "token.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"\nToken saved to: {token_path}")

        # Verify Gmail access
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        print(f"\nConnected to Gmail as: {profile['emailAddress']}")
        print(f"Total messages: {profile['messagesTotal']:,}")
        print("\n=== GMAIL SETUP COMPLETE! ===")
        return True
    else:
        print(f"\nERROR: {result.get('error', 'Unknown error')}")
        return False


if __name__ == "__main__":
    try:
        success = main()
        if not success:
            print("\nFailed. Check screenshots in ~/Agents/gmail-setup/")
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
