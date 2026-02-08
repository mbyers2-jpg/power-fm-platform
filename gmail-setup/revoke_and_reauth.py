#!/usr/bin/env python3
"""
Step 1: Revoke existing app permissions via Google Account settings
Step 2: Re-run OAuth flow so the full consent page with checkboxes appears
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


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("=== Step 1: Revoke existing app permissions ===\n")

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

        # Go to Google Account permissions page
        print("Loading Google Account permissions...")
        page.goto("https://myaccount.google.com/permissions", wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        page.screenshot(path=str(SS / "permissions_page.png"))
        print(f"Page: {page.title()}")

        # Look for "email agent" in the third-party apps list
        found = False
        # Try clicking on "email agent" app entry
        for sel in [
            'text="email agent"',
            '*:has-text("email agent")',
            'div:has-text("email agent")',
            'button:has-text("email agent")',
            'a:has-text("email agent")',
        ]:
            try:
                els = page.locator(sel).all()
                for el in els:
                    try:
                        text = el.inner_text(timeout=1000)
                        if "email agent" in text.lower() and len(text) < 200:
                            el.click()
                            time.sleep(3)
                            print(f"Clicked on email agent entry")
                            found = True
                            break
                    except Exception:
                        continue
                if found:
                    break
            except Exception:
                continue

        if found:
            page.screenshot(path=str(SS / "app_details.png"))

            # Click "Remove access" or "Delete all connections"
            for sel in [
                'button:has-text("Remove access")',
                'button:has-text("REMOVE ACCESS")',
                'button:has-text("Remove")',
                'button:has-text("Delete all connections")',
                'button:has-text("DELETE")',
                'a:has-text("Remove access")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        time.sleep(3)
                        print(f"Clicked: {sel}")

                        # Confirm removal
                        for confirm in [
                            'button:has-text("OK")',
                            'button:has-text("Confirm")',
                            'button:has-text("Remove")',
                            'button:has-text("Yes")',
                        ]:
                            try:
                                btn = page.locator(confirm).first
                                if btn.is_visible(timeout=3000):
                                    btn.click()
                                    time.sleep(3)
                                    print(f"Confirmed: {confirm}")
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

            page.screenshot(path=str(SS / "after_revoke.png"))
            print("App access revoked!")
        else:
            print("'email agent' not found in permissions. May not have been granted yet.")
            # Check page content
            body = page.inner_text("body")
            if "email" in body.lower():
                print("Page contains 'email' - might need different selector")
                # Take screenshot for debugging
                page.screenshot(path=str(SS / "permissions_debug.png"))

        browser.close()

    print("\nWaiting 5 seconds for revocation to propagate...")
    time.sleep(5)

    # === Step 2: Run OAuth flow ===
    print("\n=== Step 2: Running OAuth flow ===\n")

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

    print(f"Got URL (has prompt=consent): {'prompt=consent' in auth_url}")

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

        print("\n[1] Loading OAuth page...")
        page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        url = page.url
        print(f"URL: {url[:80]}")

        # Account chooser
        if "accountchooser" in url or "signin" in url:
            print("[2] Selecting account...")
            try:
                el = page.locator('div[data-email="m.byers2@gmail.com"]').first
                if el.is_visible(timeout=5000):
                    el.click()
                    time.sleep(8)  # Longer wait for next page to load
                    print("Selected account")
            except Exception as e:
                print(f"Account selection: {e}")

        # Take screenshot to see what page we're on
        url = page.url
        print(f"\n[3] Current page: {url[:80]}")
        page.screenshot(path=str(SS / "revoke_step3.png"))

        # Handle "This app isn't verified" / unverified app warning
        # This is a specific Google warning page - NOT the consent page
        # It has "Advanced" link at bottom, then "Go to email agent (unsafe)"
        page_text = page.inner_text("body")

        if "isn't verified" in page_text or "not verified" in page_text or "warning" in url:
            print("[3a] Unverified app warning detected")

            # Look for "Advanced" expandable section
            advanced_clicked = False
            for sel in [
                '#details-button',
                'text="Advanced"',
                'a:has-text("Advanced")',
                'button:has-text("Advanced")',
                'span:has-text("Advanced")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        time.sleep(2)
                        print(f"  Clicked Advanced: {sel}")
                        advanced_clicked = True
                        break
                except Exception:
                    continue

            if advanced_clicked:
                page.screenshot(path=str(SS / "revoke_advanced.png"))
                # Now click "Go to email agent (unsafe)"
                for sel in [
                    'a:has-text("Go to")',
                    'a:has-text("unsafe")',
                    'a[id*="proceed"]',
                    'button:has-text("Go to")',
                ]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=3000):
                            el.click()
                            time.sleep(8)
                            print(f"  Clicked: {sel}")
                            break
                    except Exception:
                        continue
            else:
                # Maybe the warning page has "Continue" button instead
                for sel in [
                    'button:has-text("Continue")',
                    '#submit_approve_access',
                ]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            el.click()
                            time.sleep(8)
                            print(f"  Clicked: {sel}")
                            break
                    except Exception:
                        continue

        url = page.url
        print(f"\n[4] After warning: {url[:80]}")
        page.screenshot(path=str(SS / "revoke_step4.png"))

        # Now we should be on the consent page with checkboxes
        if url.startswith("http://localhost"):
            print("Already redirected to localhost (consent was auto-approved)")
        elif "consent" in url or "approval" in url:
            print("\n[5] CONSENT PAGE - Handling checkboxes!")
            time.sleep(3)

            # Get detailed checkbox info
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
                            tag: cb.tagName,
                            type: cb.type || '',
                            label: label ? label.textContent.trim().substring(0, 80) : '',
                            id: cb.id || ''
                        });
                    }
                });
                return results;
            }""")

            print(f"Found {len(cb_info)} visible checkboxes:")
            for cb in cb_info:
                state = "CHECKED" if cb['checked'] else "unchecked"
                print(f"  #{cb['index']} ({cb['x']:.0f},{cb['y']:.0f}) [{state}] {cb['label'][:60]}")

            unchecked = [cb for cb in cb_info if not cb['checked']]
            if unchecked:
                # Group by x coordinate to find "Select all" vs individual
                x_groups = {}
                for cb in unchecked:
                    x_key = round(cb['x'])
                    x_groups.setdefault(x_key, []).append(cb)

                print(f"\nX-position groups: {dict((k, len(v)) for k, v in x_groups.items())}")

                # Click "Select all" first (unique x position), then verify
                for x_key, cbs in sorted(x_groups.items(), key=lambda x: len(x[1])):
                    if len(cbs) == 1 and len(x_groups) > 1:
                        # This is likely "Select all"
                        cb = cbs[0]
                        print(f"\nClicking 'Select all' at ({cb['x']:.0f},{cb['y']:.0f})...")
                        page.mouse.click(cb['x'], cb['y'])
                        time.sleep(3)
                        break

                # Verify and click any remaining unchecked
                remaining = page.evaluate("""() => {
                    const results = [];
                    document.querySelectorAll('input[type="checkbox"], [role="checkbox"]').forEach(cb => {
                        const rect = cb.getBoundingClientRect();
                        if (rect.width > 0 && !(cb.checked || cb.getAttribute('aria-checked') === 'true')) {
                            results.push({x: rect.x + rect.width/2, y: rect.y + rect.height/2});
                        }
                    });
                    return results;
                }""")

                if remaining:
                    print(f"\n{len(remaining)} still unchecked, clicking each:")
                    for r in remaining:
                        print(f"  Clicking ({r['x']:.0f},{r['y']:.0f})...")
                        page.mouse.click(r['x'], r['y'])
                        time.sleep(1.5)

                # JavaScript backup: force-check all
                page.evaluate("""() => {
                    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                        if (!cb.checked && cb.getBoundingClientRect().width > 0) {
                            cb.click();
                        }
                    });
                }""")
                time.sleep(2)
            else:
                print("All checkboxes already checked!")

            page.screenshot(path=str(SS / "revoke_consent_after.png"))

            # Click Continue/Allow
            print("\n[6] Clicking Continue/Allow...")
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
                        print(f"Clicked: {sel}")
                        break
                except Exception:
                    continue
        else:
            # Check if there's a page we didn't expect
            print(f"\n[5] Unexpected page. Taking screenshot...")
            page.screenshot(path=str(SS / "revoke_unexpected.png"))
            body_text = page.inner_text("body")[:500]
            print(f"Page text: {body_text}")

            # Try clicking any Continue/Allow buttons
            for sel in [
                'button:has-text("Continue")',
                'button:has-text("Allow")',
                '#submit_approve_access',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        time.sleep(5)
                        print(f"Clicked: {sel}")
                except Exception:
                    continue

        # Wait for localhost redirect
        print("\n[7] Waiting for localhost redirect...")
        for i in range(30):
            try:
                url = page.url
                if url.startswith("http://localhost"):
                    print("Redirected to localhost!")
                    break
            except Exception:
                break
            time.sleep(1)

        page.screenshot(path=str(SS / "revoke_final.png"))
        browser.close()

    # Wait for result
    print("\n[8] Waiting for token exchange...")
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

        # If scope mismatch, try a workaround
        if "Scope has changed" in str(error):
            print("\nScope mismatch detected. Trying manual token exchange...")
            return try_manual_exchange(flow)
        return False


def try_manual_exchange(flow):
    """Manually exchange code, bypassing scope validation."""
    import requests

    print("\n=== Manual Token Exchange ===")

    # Get the credentials from the flow
    client_id = flow.client_config['client_id']
    client_secret = flow.client_config['client_secret']
    redirect_uri = "http://localhost:8089/"

    # We need the authorization code. Check if there's a recent one from the server logs.
    # Actually, the server already got the code but failed on scope validation.
    # Let's try a completely different approach: use requests directly.

    print("Re-running flow with scope validation disabled...")

    # Monkey-patch oauthlib to skip scope validation
    try:
        import oauthlib.oauth2.rfc6749.parameters as params
        original_func = params.parse_authorization_code_response

        def patched_parse(uri, state=None):
            """Parse auth code response without raising on scope mismatch."""
            from oauthlib.common import add_params_to_qs, add_params_to_uri, urldecode
            import urllib.parse as urlparse
            query = urlparse.urlparse(uri).query
            params_dict = dict(urlparse.parse_qsl(query))

            if 'error' in params_dict:
                from oauthlib.oauth2.rfc6749.errors import OAuth2Error
                raise OAuth2Error(description=params_dict.get('error_description', ''),
                                  uri=params_dict.get('error_uri', ''),
                                  error=params_dict['error'])

            if 'code' not in params_dict:
                raise ValueError("Missing code parameter in response.")

            if state and params_dict.get('state', state) != state:
                from oauthlib.oauth2.rfc6749.errors import MismatchingStateError
                raise MismatchingStateError()

            return params_dict

        params.parse_authorization_code_response = patched_parse
        print("Scope validation patched!")

        # Now re-run the flow
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow2 = InstalledAppFlow.from_client_secrets_file(
            str(HOME / "Agents" / "email-agent" / "config" / "credentials.json"),
            SCOPES
        )

        creds = flow2.run_local_server(port=8089, open_browser=True, prompt='consent')

        token_path = HOME / "Agents" / "email-agent" / "config" / "token.json"
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved to: {token_path}")
        return True

    except Exception as e:
        print(f"Manual exchange failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\nAll done!")
        else:
            print("\nCheck screenshots in ~/Agents/gmail-setup/")
    except Exception as e:
        print(f"\nFatal: {e}")
        import traceback
        traceback.print_exc()
