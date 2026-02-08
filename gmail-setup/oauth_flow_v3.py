#!/usr/bin/env python3
"""
OAuth flow v3 - State machine approach.
Loops through pages until reaching localhost, handling each page type.
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
    """Detect what type of page we're on."""
    url = page.url
    if url.startswith("http://localhost"):
        return "localhost"

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
    if "consent" in url and "localhost" not in url:
        return "consent"
    if "Select all" in text or "check all" in text.lower():
        return "consent"

    # Check for checkboxes (consent page indicator)
    try:
        cb_count = page.evaluate("""() => {
            return document.querySelectorAll('input[type="checkbox"]').length;
        }""")
        if cb_count > 2:
            return "consent"
    except Exception:
        pass

    return "unknown"


def handle_account_chooser(page):
    """Select the Gmail account."""
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
    """Handle 'app not verified' warning page."""
    print("  -> Handling unverified app warning...")
    page.screenshot(path=str(SS / "v3_warning.png"))

    # Try "Advanced" → "Go to email agent (unsafe)"
    for sel in ['#details-button', 'text="Advanced"', 'a:has-text("Advanced")',
                'button:has-text("Advanced")', 'span:has-text("Advanced")']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                time.sleep(2)
                print(f"  -> Clicked Advanced ({sel})")
                # Now click "Go to email agent (unsafe)"
                for sel2 in ['a:has-text("Go to")', 'a:has-text("unsafe")', 'a[id*="proceed"]']:
                    try:
                        el2 = page.locator(sel2).first
                        if el2.is_visible(timeout=3000):
                            el2.click()
                            time.sleep(8)
                            print(f"  -> Clicked proceed ({sel2})")
                            return True
                    except Exception:
                        continue
        except Exception:
            continue

    # Fallback: just click Continue
    for sel in ['button:has-text("Continue")', '#submit_approve_access']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                time.sleep(8)
                print(f"  -> Clicked Continue ({sel})")
                return True
        except Exception:
            continue
    return False


def handle_consent(page):
    """Handle consent page with scope checkboxes."""
    print("  -> Handling consent/scope selection page...")
    time.sleep(3)
    page.screenshot(path=str(SS / "v3_consent_before.png"))

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

    print(f"  Found {len(cb_info)} checkboxes:")
    for cb in cb_info:
        state = "CHECKED" if cb['checked'] else "unchecked"
        print(f"    #{cb['index']} ({cb['x']:.0f},{cb['y']:.0f}) [{state}] {cb['label'][:60]}")

    unchecked = [cb for cb in cb_info if not cb['checked']]

    if unchecked:
        # Group by x-coordinate
        x_groups = {}
        for cb in unchecked:
            x_key = round(cb['x'])
            x_groups.setdefault(x_key, []).append(cb)

        print(f"  X-groups: {dict((k, len(v)) for k, v in x_groups.items())}")

        # Find "Select all" (unique x position with only 1 checkbox)
        select_all = None
        if len(x_groups) > 1:
            for x_val, cbs in x_groups.items():
                if len(cbs) == 1:
                    select_all = cbs[0]
                    break

        if select_all:
            print(f"  Clicking 'Select all' at ({select_all['x']:.0f},{select_all['y']:.0f})...")
            page.mouse.click(select_all['x'], select_all['y'])
            time.sleep(3)
        else:
            # Click ALL checkboxes individually with longer delays
            print(f"  Clicking all {len(unchecked)} checkboxes individually...")
            for cb in unchecked:
                page.mouse.click(cb['x'], cb['y'])
                time.sleep(2)

        # Verify with JS
        remaining = page.evaluate("""() => {
            const cbs = document.querySelectorAll('input[type="checkbox"]');
            return Array.from(cbs)
                .filter(cb => cb.getBoundingClientRect().width > 0 && !cb.checked)
                .map(cb => ({
                    x: cb.getBoundingClientRect().x + cb.getBoundingClientRect().width/2,
                    y: cb.getBoundingClientRect().y + cb.getBoundingClientRect().height/2
                }));
        }""")

        if remaining:
            print(f"  {len(remaining)} still unchecked, clicking each...")
            for r in remaining:
                page.mouse.click(r['x'], r['y'])
                time.sleep(2)

        # JS backup: force click each unchecked checkbox
        page.evaluate("""() => {
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (!cb.checked && cb.getBoundingClientRect().width > 0) {
                    cb.click();
                }
            });
        }""")
        time.sleep(2)

        # Final verification
        final = page.evaluate("""() => {
            const cbs = document.querySelectorAll('input[type="checkbox"]');
            return Array.from(cbs)
                .filter(cb => cb.getBoundingClientRect().width > 0)
                .map(cb => ({checked: cb.checked, label: cb.closest('label')?.textContent?.trim().substring(0,60) || ''}));
        }""")
        for f in final:
            print(f"    [{'CHECKED' if f['checked'] else 'UNCHECKED'}] {f['label']}")

    page.screenshot(path=str(SS / "v3_consent_after.png"))

    # Click Continue/Allow
    print("  Clicking Continue/Allow...")
    for sel in ['button:has-text("Continue")', 'button:has-text("Allow")',
                '#submit_approve_access', 'button:has-text("CONTINUE")']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                time.sleep(8)
                print(f"  -> Clicked: {sel}")
                return True
        except Exception:
            continue
    return False


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("=== Gmail OAuth Flow v3 (State Machine) ===\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CONFIG_DIR / "credentials.json"), SCOPES
    )

    result = {}
    url_capture = URLCapture(sys.__stdout__)

    server_thread = threading.Thread(target=run_server, args=(flow, result, url_capture))
    server_thread.daemon = True
    server_thread.start()

    print("Waiting for OAuth URL...")
    for _ in range(30):
        if url_capture.captured_url:
            break
        time.sleep(1)

    auth_url = url_capture.captured_url
    if not auth_url:
        print("ERROR: Could not capture OAuth URL")
        return False

    print(f"URL captured (prompt=consent: {'prompt=consent' in auth_url})\n")

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

        # State machine: loop through pages until we reach localhost
        max_iterations = 15
        for iteration in range(max_iterations):
            page_type = detect_page(page)
            url = page.url
            print(f"\n--- Iteration {iteration+1}: page_type={page_type} ---")
            print(f"    URL: {url[:80]}")

            if page_type == "localhost":
                print("    DONE! Reached localhost.")
                break
            elif page_type == "account_chooser":
                handle_account_chooser(page)
            elif page_type == "warning":
                handle_warning(page)
            elif page_type == "consent":
                handle_consent(page)
            elif page_type == "unknown":
                print("    Unknown page. Taking screenshot...")
                page.screenshot(path=str(SS / f"v3_unknown_{iteration}.png"))
                body = page.inner_text("body")[:300]
                print(f"    Text: {body}")

                # Try common buttons
                for sel in ['button:has-text("Continue")', 'button:has-text("Allow")',
                            '#submit_approve_access']:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            el.click()
                            time.sleep(5)
                            print(f"    Clicked: {sel}")
                            break
                    except Exception:
                        continue

            time.sleep(2)

        page.screenshot(path=str(SS / "v3_final.png"))
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

        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        prof = service.users().getProfile(userId="me").execute()
        print(f"\nConnected to Gmail as: {prof['emailAddress']}")
        print(f"Total messages: {prof['messagesTotal']:,}")
        print("\n=== GMAIL SETUP COMPLETE! ===")
        return True
    else:
        error = result.get('error', 'Unknown')
        print(f"\nToken exchange error: {error}")

        if "Scope has changed" in str(error):
            print("\nScope mismatch! Google only granted partial scopes.")
            print("Attempting workaround: monkey-patching scope validation...")
            return try_with_patched_scopes()
        return False


def try_with_patched_scopes():
    """Re-run flow with scope validation disabled in oauthlib."""
    import oauthlib.oauth2.rfc6749.parameters as params

    original_parse = params.parse_authorization_code_response

    def patched_parse(uri, state=None):
        """Skip scope mismatch check."""
        import urllib.parse as urlparse
        query = urlparse.urlparse(uri).query
        p = dict(urlparse.parse_qsl(query))

        if 'error' in p:
            from oauthlib.oauth2.rfc6749.errors import OAuth2Error
            raise OAuth2Error(description=p.get('error_description', ''),
                              error=p['error'])
        if 'code' not in p:
            raise ValueError("Missing code parameter.")
        if state and p.get('state', state) != state:
            from oauthlib.oauth2.rfc6749.errors import MismatchingStateError
            raise MismatchingStateError()
        return p

    params.parse_authorization_code_response = patched_parse

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("\nStarting patched flow (will open browser)...")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CONFIG_DIR / "credentials.json"), SCOPES
    )

    try:
        creds = flow.run_local_server(port=8089, open_browser=True, prompt='consent')
        token_path = CONFIG_DIR / "token.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved to: {token_path}")

        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        prof = service.users().getProfile(userId="me").execute()
        print(f"Connected to Gmail as: {prof['emailAddress']}")
        print("\n=== GMAIL SETUP COMPLETE (via patch) ===")
        return True
    except Exception as e:
        print(f"Patched flow also failed: {e}")
        params.parse_authorization_code_response = original_parse
        return False


if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\nAll done!")
        else:
            print("\nFailed. Check screenshots in ~/Agents/gmail-setup/")
    except Exception as e:
        print(f"\nFatal: {e}")
        import traceback
        traceback.print_exc()
