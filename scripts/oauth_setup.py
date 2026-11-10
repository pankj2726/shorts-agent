#!/usr/bin/env python3
"""
oauth_setup.py — One-time helper to obtain the YouTube OAuth 2.0 refresh token.

Zero external dependencies required (uses Python standard library).

Scopes requested:
  - https://www.googleapis.com/auth/youtube.upload       (Publish Shorts)
  - https://www.googleapis.com/auth/youtube.force-ssl     (Comment reading/replying)
  - https://www.googleapis.com/auth/yt-analytics.readonly (Channel metrics)

Prerequisites:
  1. Google Cloud project with YouTube Data API v3 and YouTube Analytics API enabled.
  2. OAuth Consent Screen set to 'External' and 'In production' (prevents 7-day token expiry).
  3. OAuth 2.0 Client ID created with application type: Desktop App.
  4. Client ID and Client Secret set in .env or typed when prompted.

Usage:
  python scripts/oauth_setup.py
"""
import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

def _load_env_file() -> None:
    candidates = [
        Path(r"C:\Secrets\shorts-agent.env"),
        Path(r"C:\Secrets\.env"),
        Path(__file__).parent.parent / ".env",
        Path(".env"),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and not os.environ.get(k):
                            os.environ[k] = v
            except Exception:
                pass
            break

_load_env_file()

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class _OAuthHandler(http.server.BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in query:
            _OAuthHandler.code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<html><body style="font-family:sans-serif;text-align:center;padding:50px;">
            <h1 style="color:#10b981;">Authentication Successful!</h1>
            <p>You can close this tab and return to your terminal.</p>
            </body></html>"""
            self.wfile.write(html.encode("utf-8"))
        elif "error" in query:
            _OAuthHandler.error = query["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authentication failed. You can close this window.")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def _pure_python_oauth(client_id: str, client_secret: str) -> str:
    port = 8088
    redirect_uri = f"http://localhost:{port}/"

    scope_str = "%20".join(urllib.parse.quote(s, safe="") for s in SCOPES)
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={urllib.parse.quote(client_id)}&"
        f"redirect_uri={urllib.parse.quote(redirect_uri)}&"
        f"response_type=code&"
        f"scope={scope_str}&"
        f"access_type=offline&"
        f"prompt=consent"
    )

    server = http.server.HTTPServer(("localhost", port), _OAuthHandler)
    server.timeout = 180

    print("\nStarting local authentication server...")
    print("Opening your browser to Google authorization...")
    print("\nIf the browser does not open automatically, visit this URL:")
    print(f"\n{auth_url}\n")
    print("Note: If you see an 'unverified app' warning, click 'Advanced' -> 'Go to app (unsafe)'.")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("\nWaiting for authentication approval in browser...")
    while _OAuthHandler.code is None and _OAuthHandler.error is None:
        server.handle_request()

    if _OAuthHandler.error:
        print(f"\n❌ Error from Google: {_OAuthHandler.error}")
        sys.exit(1)

    code = _OAuthHandler.code
    if not code:
        print("\n❌ Error: Failed to receive authorization code.")
        sys.exit(1)

    # Exchange authorization code for refresh token
    token_url = "https://oauth2.googleapis.com/token"
    token_params = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    data = urllib.parse.urlencode(token_params).encode("utf-8")
    req = urllib.request.Request(token_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req) as resp:
            tokens = json.loads(resp.read().decode("utf-8"))
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                print("\n⚠️ Warning: No refresh token returned. Did you approve with prompt=consent?")
                print("Try revoking existing app permissions at https://myaccount.google.com/permissions and retry.")
                sys.exit(1)
            return refresh_token
    except Exception as e:
        print(f"\n❌ Error exchanging code for tokens: {e}")
        sys.exit(1)


def main() -> None:
    print("=" * 70)
    print(" YouTube Shorts Agent — One-Time OAuth 2.0 Token Generator")
    print("=" * 70)

    client_id = os.environ.get("YT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YT_CLIENT_SECRET", "").strip()

    if not client_id:
        client_id = input("\nEnter your YouTube Client ID: ").strip()
    if not client_secret:
        client_secret = input("Enter your YouTube Client Secret: ").strip()

    if not client_id or not client_secret:
        print("\n❌ Error: Both Client ID and Client Secret are required.")
        sys.exit(1)

    refresh_token = _pure_python_oauth(client_id, client_secret)

    print("\n" + "=" * 70)
    print("✅ SUCCESS! OAuth Refresh Token obtained successfully.")
    print("=" * 70)
    print(f"\nYT_REFRESH_TOKEN:\n{refresh_token}\n")
    print("-" * 70)
    print("NEXT STEPS:")
    print("1. Copy this token.")
    print("2. Paste it into your ENV_FILE secret or local .env.")
    print("3. Never commit this token into Git.")
    print("=" * 70)


if __name__ == "__main__":
    main()
