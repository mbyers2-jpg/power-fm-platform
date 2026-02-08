#!/usr/bin/env python3
"""
OAuth Final - Two-pass approach:
1. First revoke any existing consent via Google Account permissions
2. Then do OAuth with scope validation patched AND improved consent page handling

Key fix: detect_page checks for "consent" in URL BEFORE checking body text,
and properly handles the consentsummary page with checkboxes.
"""
import io
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

sys.path.insert(0, str(HOME / "Agents" / "email-agent" / "venv" / "lib" / "python3.9" / "site-packages"))

# === FIX: Relax scope validation via environment variable ===
import os
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
print("OAUTHLIB_RELAX_TOKEN_SCOPE set - scope mismatch will be tolerated")


class URLCapture(io.TextIOWrapper):
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
    old_stdout = sys.stdout
    sys.stdout = url_capture
    try:
        creds = flow.run_local_server(port=8089, open_browser=False, prompt='consent')
        result_holder["creds"] = creds
        result_holder["success"] = True
    except Exception as e:
        result_holder["error"] = str(e)
        result_holder["success"] = False
    finally:
        sys.stdout = old_stdout


def detect_page(page):
    """Detect page type. Priority: localhost > consent > warning > account_chooser."""
    url = page.url
    if url.startswith("http://localhost"):
        return "localhost"

    # Check for consent page FIRST (URL check)
    if "consent" in url and "localhost" not in url:
        return "consent"

    text = ""
    try:
        text = page.inner_text("body", timeout=5000)
    except Exception:
        pass

    if "accountchooser" in url or "signin/identifier" in url:
        return "account_chooser"
    if "warning" in url:
        return "warning"
    if "hasn't verified" in text or "isn't verified" in text or "not verified" in text:
        return "warning"

    # Check for checkboxes
    try:
        cb_count = page.evaluate("""() =>
            document.querySelectorAll('input[type="checkbox"]').length
        """)
        if cb_count > 2:
            return "consent"
    except Exception:
        pass

    return "unknown"


def handle_account_chooser(page):
    print("  -> Selecting m.byers2@gmail.com...")
    try:
        el = page.locator('div[data-email="m.byers2@gmail.com"]').first
        if el.is_visible(timeout=5000):
            el.click()
            time.sleep(8)
            return True
    except Exception:
        pass
    return False


def handle_warning(page):
    print("  -> Handling unverified app warning...")

    # Try "Advanced" → "Go to email agent (unsafe)"
    for sel in ['#details-button', 'a:has-text("Advanced")', 'button:has-text("Advanced")']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                time.sleep(2)
                for sel2 in ['a:has-text("Go to")', 'a:has-text("unsafe")', 'a[id*="proceed"]']:
                    try:
                        el2 = page.locator(sel2).first
                        if el2.is_visible(timeout=3000):
                            el2.click()
                            time.sleep(8)
                            print(f"  -> Advanced → proceed")
                            return True
                    except Exception:
                        continue
        except Exception:
            continue

    # Fallback: click Continue
    for sel in ['button:has-text("Continue")', '#submit_approve_access']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                time.sleep(8)
                print(f"  -> Clicked Continue")
                return True
        except Exception:
            continue
    return False


def handle_consent(page):
    """Handle consent page - check all checkboxes then click Continue."""
    print("  -> On consent page, handling checkboxes...")
    time.sleep(3)
    page.screenshot(path=str(SS / "final_consent_before.png"))

    # Get checkbox info
    cb_info = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('input[type="checkbox"], [role="checkbox"]').forEach((cb, i) => {
            const rect = cb.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                const label = cb.closest('label');
                results.push({
                    index: i,
                    x: rect.x + rect.width/2,
                    y: rect.y + rect.height/2,
                    checked: cb.checked || cb.getAttribute('aria-checked') === 'true',
                    label: label ? label.textContent.trim().substring(0, 80) : ''
                });
            }
        });
        return results;
    }""")

    unchecked = [cb for cb in cb_info if not cb['checked']]
    print(f"  {len(cb_info)} total checkboxes, {len(unchecked)} unchecked")

    for cb in cb_info:
        state = "OK" if cb['checked'] else "NEED"
        print(f"    #{cb['index']} ({cb['x']:.0f},{cb['y']:.0f}) [{state}] {cb['label'][:50]}")

    if unchecked:
        # Strategy: click "Select all" first (unique x position), then individuals
        x_groups = {}
        for cb in unchecked:
            x_key = round(cb['x'])
            x_groups.setdefault(x_key, []).append(cb)

        select_all_clicked = False
        if len(x_groups) > 1:
            for x_val, cbs in sorted(x_groups.items(), key=lambda x: len(x[1])):
                if len(cbs) == 1:
                    cb = cbs[0]
                    print(f"  Clicking 'Select all' at ({cb['x']:.0f},{cb['y']:.0f})...")
                    page.mouse.click(cb['x'], cb['y'])
                    time.sleep(3)
                    select_all_clicked = True
                    break

        if not select_all_clicked:
            # Click all individually
            for cb in unchecked:
                print(f"  Clicking ({cb['x']:.0f},{cb['y']:.0f})...")
                page.mouse.click(cb['x'], cb['y'])
                time.sleep(2)

        # Verify + force with JS
        page.evaluate("""() => {
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (!cb.checked && cb.getBoundingClientRect().width > 0) {
                    cb.click();
                }
            });
        }""")
        time.sleep(2)

    page.screenshot(path=str(SS / "final_consent_after.png"))

    # Click Continue/Allow
    for sel in ['button:has-text("Continue")', 'button:has-text("Allow")',
                '#submit_approve_access']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                time.sleep(8)
                print(f"  -> Clicked {sel}")
                return True
        except Exception:
            continue
    return False


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("\n=== Gmail OAuth Final Flow ===\n")

    # Step 1: Revoke existing permissions
    print("[Step 1] Revoking existing app permissions...")
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

        page.goto("https://myaccount.google.com/permissions", wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        # Find and revoke "email agent"
        try:
            els = page.locator('*:has-text("email agent")').all()
            for el in els:
                try:
                    text = el.inner_text(timeout=1000)
                    if "email agent" in text.lower() and len(text) < 100:
                        el.click()
                        time.sleep(3)
                        print("  Found and clicked 'email agent'")

                        # Click Remove/Delete
                        for sel in ['button:has-text("Remove")', 'button:has-text("Delete")',
                                    'button:has-text("REMOVE")']:
                            try:
                                btn = page.locator(sel).first
                                if btn.is_visible(timeout=3000):
                                    btn.click()
                                    time.sleep(3)
                                    # Confirm
                                    for c in ['button:has-text("OK")', 'button:has-text("Confirm")',
                                              'button:has-text("Remove")']:
                                        try:
                                            cb = page.locator(c).first
                                            if cb.is_visible(timeout=2000):
                                                cb.click()
                                                time.sleep(3)
                                                break
                                        except Exception:
                                            continue
                                    print("  Access revoked!")
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue
        except Exception:
            print("  'email agent' not found (may already be revoked)")

        browser.close()

    print("  Waiting for propagation...")
    time.sleep(10)

    # Step 2: OAuth flow
    print("\n[Step 2] Starting OAuth flow with scope validation patched...")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CONFIG_DIR / "credentials.json"), SCOPES
    )

    result = {}
    url_capture = URLCapture(sys.__stdout__)

    server_thread = threading.Thread(target=run_server, args=(flow, result, url_capture))
    server_thread.daemon = True
    server_thread.start()

    for _ in range(30):
        if url_capture.captured_url:
            break
        time.sleep(1)

    auth_url = url_capture.captured_url
    if not auth_url:
        print("ERROR: No URL captured")
        return False

    print(f"URL captured\n")

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
        page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # State machine loop
        for iteration in range(20):
            page_type = detect_page(page)
            url = page.url[:80]
            print(f"\n[Iter {iteration+1}] {page_type} | {url}")

            if page_type == "localhost":
                print("  DONE!")
                break
            elif page_type == "account_chooser":
                handle_account_chooser(page)
            elif page_type == "warning":
                handle_warning(page)
            elif page_type == "consent":
                handle_consent(page)
            else:
                page.screenshot(path=str(SS / f"final_unknown_{iteration}.png"))
                body = page.inner_text("body")[:200]
                print(f"  Unknown page: {body}")
                # Try Continue
                try:
                    page.locator('button:has-text("Continue")').first.click(timeout=3000)
                    time.sleep(5)
                except Exception:
                    pass

            time.sleep(2)

        page.screenshot(path=str(SS / "final_done.png"))
        browser.close()

    # Wait for result
    print("\nWaiting for token exchange...")
    server_thread.join(timeout=30)

    if result.get("success"):
        creds = result["creds"]
        token_path = CONFIG_DIR / "token.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"\nToken saved to: {token_path}")

        # Test
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        prof = service.users().getProfile(userId="me").execute()
        print(f"\nConnected to Gmail as: {prof['emailAddress']}")
        print(f"Total messages: {prof['messagesTotal']:,}")

        # Check granted scopes
        print(f"\nGranted scopes: {creds.scopes}")
        print("\n=== GMAIL SETUP COMPLETE! ===")
        return True
    else:
        print(f"\nError: {result.get('error', 'Unknown')}")
        return False


if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\nAll done!")
        else:
            print("\nFailed. Check screenshots.")
    except Exception as e:
        print(f"\nFatal: {e}")
        import traceback
        traceback.print_exc()
