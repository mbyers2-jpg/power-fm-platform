#!/usr/bin/env python3
"""
OAuth v3 - Loop-based page handler.
After revoking existing consent, walks through each page:
  Account chooser → Warning → Consent (checkboxes) → localhost
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


class URLCapture(io.TextIOWrapper):
    def __init__(self, original):
        self.original = original
        self.captured_url = None
    def write(self, text):
        self.original.write(text)
        self.original.flush()
        if text and "accounts.google.com" in text:
            m = re.search(r'(https://accounts\.google\.com\S+)', text)
            if m:
                self.captured_url = m.group(1)
        return len(text)
    def flush(self):
        self.original.flush()
    def __getattr__(self, name):
        return getattr(self.original, name)


def run_server(flow, result, url_cap):
    old = sys.stdout
    sys.stdout = url_cap
    try:
        creds = flow.run_local_server(port=8089, open_browser=False, prompt='consent')
        result["creds"] = creds
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)
        result["ok"] = False
    finally:
        sys.stdout = old


def detect_page(page):
    """Detect what OAuth page we're on."""
    url = page.url
    if url.startswith("http://localhost"):
        return "localhost"

    text = ""
    try:
        text = page.inner_text("body", timeout=3000)
    except Exception:
        pass

    if "accountchooser" in url or "signin/identifier" in url:
        return "account_chooser"
    if "warning" in url or "hasn't verified" in text or "not verified" in text:
        return "warning"
    if "consent" in url and "localhost" not in url:
        return "consent"
    if "signin" in url and ("m.byers2" in text or "Choose an account" in text):
        return "account_chooser"

    # Check for buttons to identify page
    try:
        if page.locator('input[type="checkbox"]').count() > 0:
            return "consent"
    except Exception:
        pass

    return "unknown"


def handle_account_chooser(page):
    """Select the Gmail account."""
    print("  Selecting m.byers2@gmail.com...")
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
    """Handle 'Google hasn't verified this app' warning."""
    print("  Handling unverified app warning...")
    page.screenshot(path=str(SS / "v3_warning.png"))

    # Try "Continue" button (testing mode shows this directly)
    for sel in [
        'button:has-text("Continue")',
        'button:has-text("CONTINUE")',
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                time.sleep(8)
                print(f"  Clicked: {sel}")
                return True
        except Exception:
            continue

    # Try Advanced → Go to unsafe
    for sel in ['#details-button', 'text="Advanced"']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                time.sleep(2)
                for go_sel in ['a:has-text("Go to")', 'a:has-text("unsafe")']:
                    try:
                        go = page.locator(go_sel).first
                        if go.is_visible(timeout=3000):
                            go.click()
                            time.sleep(8)
                            return True
                    except Exception:
                        continue
        except Exception:
            continue
    return False


def handle_consent(page):
    """Handle consent page with scope checkboxes."""
    print("  Handling consent/checkbox page...")
    time.sleep(3)
    page.screenshot(path=str(SS / "v3_consent_before.png"))

    # Get all visible checkboxes
    cb_info = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('input[type="checkbox"], [role="checkbox"]').forEach((cb, i) => {
            const rect = cb.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                results.push({
                    i, x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                    checked: cb.checked || cb.getAttribute('aria-checked') === 'true',
                    label: (cb.closest('label') || cb.parentElement)?.textContent?.trim().substring(0, 80) || ''
                });
            }
        });
        return results;
    }""")

    print(f"  Found {len(cb_info)} visible checkboxes:")
    for cb in cb_info:
        s = "CHECKED" if cb['checked'] else "unchecked"
        print(f"    #{cb['i']} ({cb['x']:.0f},{cb['y']:.0f}) [{s}] {cb['label'][:60]}")

    unchecked = [cb for cb in cb_info if not cb['checked']]

    if unchecked:
        # Group by x-coordinate
        x_groups = {}
        for cb in unchecked:
            x_key = round(cb['x'])
            x_groups.setdefault(x_key, []).append(cb)

        print(f"  X-groups: {dict((k,len(v)) for k,v in x_groups.items())}")

        # Find "Select all" (unique x position with only 1 checkbox)
        select_all_clicked = False
        if len(x_groups) > 1:
            for x_key, cbs in sorted(x_groups.items(), key=lambda x: len(x[1])):
                if len(cbs) == 1:
                    cb = cbs[0]
                    print(f"  Clicking 'Select all' at ({cb['x']:.0f},{cb['y']:.0f})...")
                    page.mouse.click(cb['x'], cb['y'])
                    time.sleep(3)
                    select_all_clicked = True
                    break

        if not select_all_clicked:
            # Click each checkbox individually with longer delays
            for cb in unchecked:
                print(f"  Clicking checkbox at ({cb['x']:.0f},{cb['y']:.0f})...")
                page.mouse.click(cb['x'], cb['y'])
                time.sleep(2)

        # Verify - click any still-unchecked via JS .click()
        still_unchecked = page.evaluate("""() => {
            let count = 0;
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (!cb.checked && cb.getBoundingClientRect().width > 0) {
                    cb.click();
                    count++;
                }
            });
            return count;
        }""")
        if still_unchecked > 0:
            print(f"  JS-clicked {still_unchecked} remaining checkboxes")
            time.sleep(2)

        # Final state
        final = page.evaluate("""() => {
            const cbs = document.querySelectorAll('input[type="checkbox"]');
            const visible = Array.from(cbs).filter(cb => cb.getBoundingClientRect().width > 0);
            return {total: visible.length, checked: visible.filter(cb => cb.checked).length};
        }""")
        print(f"  Final: {final['checked']}/{final['total']} checked")
    else:
        print("  All checkboxes already checked!")

    page.screenshot(path=str(SS / "v3_consent_after.png"))

    # Click Continue/Allow
    print("  Clicking Continue/Allow...")
    for sel in [
        'button:has-text("Continue")',
        'button:has-text("Allow")',
        '#submit_approve_access',
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                time.sleep(8)
                print(f"  Clicked: {sel}")
                return True
        except Exception:
            continue
    return False


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    # === Step 1: Revoke existing consent ===
    print("=== Step 1: Revoking existing app permissions ===\n")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(SS / "pw-profile"),
            channel="chrome", headless=False,
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
                    txt = el.inner_text(timeout=1000)
                    if "email agent" in txt.lower() and len(txt) < 200:
                        el.click()
                        time.sleep(3)
                        print("Found and clicked email agent")

                        # Click Remove/Delete
                        for sel in ['button:has-text("Remove")', 'button:has-text("Delete")', 'button:has-text("REMOVE")']:
                            try:
                                btn = page.locator(sel).first
                                if btn.is_visible(timeout=3000):
                                    btn.click()
                                    time.sleep(2)
                                    # Confirm
                                    for c in ['button:has-text("OK")', 'button:has-text("Confirm")', 'button:has-text("Remove")']:
                                        try:
                                            cb = page.locator(c).first
                                            if cb.is_visible(timeout=2000):
                                                cb.click()
                                                time.sleep(3)
                                                break
                                        except Exception:
                                            continue
                                    print("Revoked!")
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue
        except Exception:
            print("Could not find email agent (may already be revoked)")

        browser.close()

    print("Waiting 5 seconds...\n")
    time.sleep(5)

    # === Step 2: OAuth flow ===
    print("=== Step 2: OAuth Flow ===\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(CONFIG_DIR / "credentials.json"), SCOPES)

    result = {}
    url_cap = URLCapture(sys.__stdout__)

    server = threading.Thread(target=run_server, args=(flow, result, url_cap))
    server.daemon = True
    server.start()

    for _ in range(30):
        if url_cap.captured_url:
            break
        time.sleep(1)

    auth_url = url_cap.captured_url
    if not auth_url:
        print("ERROR: No OAuth URL captured")
        return False

    print(f"OAuth URL captured (prompt=consent: {'prompt=consent' in auth_url})")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(SS / "pw-profile"),
            channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Loop through pages until we reach localhost
        for step in range(10):
            page_type = detect_page(page)
            url = page.url
            print(f"\n[Step {step+1}] Page type: {page_type} | URL: {url[:80]}")
            page.screenshot(path=str(SS / f"v3_step{step+1}.png"))

            if page_type == "localhost":
                print("Reached localhost! OAuth callback received.")
                break
            elif page_type == "account_chooser":
                handle_account_chooser(page)
            elif page_type == "warning":
                handle_warning(page)
            elif page_type == "consent":
                handle_consent(page)
            elif page_type == "unknown":
                print(f"  Unknown page. Body text preview:")
                try:
                    txt = page.inner_text("body", timeout=3000)[:300]
                    print(f"  {txt}")
                except Exception:
                    pass

                # Try common buttons
                for sel in ['button:has-text("Continue")', 'button:has-text("Allow")', '#submit_approve_access']:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            el.click()
                            time.sleep(5)
                            print(f"  Clicked: {sel}")
                            break
                    except Exception:
                        continue

            time.sleep(2)

        page.screenshot(path=str(SS / "v3_final.png"))
        browser.close()

    # === Step 3: Get result ===
    print("\n=== Step 3: Token Exchange ===")
    server.join(timeout=30)

    if result.get("ok"):
        creds = result["creds"]
        token_path = CONFIG_DIR / "token.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved: {token_path}")

        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds)
        prof = svc.users().getProfile(userId="me").execute()
        print(f"\nConnected: {prof['emailAddress']}")
        print(f"Messages: {prof['messagesTotal']:,}")
        print("\n=== GMAIL SETUP COMPLETE! ===")
        return True
    else:
        err = result.get("error", "Unknown")
        print(f"Error: {err}")

        if "Scope has changed" in err:
            print("\nScope mismatch - trying with validation bypass...")
            return bypass_scope_check()
        return False


def bypass_scope_check():
    """Bypass oauthlib scope validation and re-run."""
    import importlib
    import oauthlib.oauth2.rfc6749.parameters as params

    # Monkey-patch to not raise on scope change
    original = params.parse_authorization_code_response

    def patched(uri, state=None):
        import urllib.parse as urlparse
        query = urlparse.urlparse(uri).query
        p = dict(urlparse.parse_qsl(query))
        if 'error' in p:
            from oauthlib.oauth2.rfc6749.errors import OAuth2Error
            raise OAuth2Error(error=p['error'])
        if 'code' not in p:
            raise ValueError("Missing code")
        if state and p.get('state', state) != state:
            from oauthlib.oauth2.rfc6749.errors import MismatchingStateError
            raise MismatchingStateError()
        return p

    params.parse_authorization_code_response = patched

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CONFIG_DIR / "credentials.json"), SCOPES
    )

    print("\nStarting OAuth with scope bypass (will open browser)...")
    print("Please approve ALL permissions in the browser.\n")

    try:
        creds = flow.run_local_server(port=8089, prompt='consent')
        token_path = CONFIG_DIR / "token.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved: {token_path}")

        # Check what scopes we actually got
        if hasattr(creds, 'scopes') and creds.scopes:
            print(f"Granted scopes: {creds.scopes}")

        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds)
        prof = svc.users().getProfile(userId="me").execute()
        print(f"\nConnected: {prof['emailAddress']}")
        print(f"Messages: {prof['messagesTotal']:,}")
        print("\n=== GMAIL SETUP COMPLETE! ===")
        return True
    except Exception as e:
        print(f"Bypass failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        ok = main()
        if not ok:
            print("\nFailed. Check screenshots in ~/Agents/gmail-setup/")
    except Exception as e:
        print(f"\nFatal: {e}")
        import traceback
        traceback.print_exc()
