#!/usr/bin/env python3
"""
oauth_setup.py — One-time helper to obtain the YouTube OAuth 2.0 refresh token.

Scopes requested:
  - https://www.googleapis.com/auth/youtube.upload       (Publish Shorts)
  - https://www.googleapis.com/auth/youtube.force-ssl     (Comment reading/replying)
  - https://www.googleapis.com/auth/yt-analytics.readonly (Channel metrics)

Prerequisites:
  1. Google Cloud project with YouTube Data API v3 and YouTube Analytics API enabled.
  2. OAuth Consent Screen set to 'External' and 'In production' (prevents 7-day token expiry).
  3. OAuth 2.0 Client ID created with application type: Desktop App.
  4. Client ID and Client Secret set in environment or typed when prompted.

Usage:
  python scripts/oauth_setup.py
"""
import json
import os
import sys

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


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

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("\n❌ Error: Missing google-auth-oauthlib. Run: pip install google-auth-oauthlib")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost", "urn:ietf:wg:oauth:2.0:oob"],
        }
    }

    print("\nStarting local authentication server...")
    print("A browser window will open. Sign in with your YouTube channel's Google account.")
    print("Note: If you see an 'unverified app' screen, click 'Advanced' -> 'Go to app (unsafe)'.")

    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=SCOPES,
    )

    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        print("\n⚠️ Warning: No refresh token returned. Did you approve with prompt=consent?")
        print("Try revoking existing app permissions at https://myaccount.google.com/permissions and retry.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("✅ SUCCESS! OAuth Refresh Token obtained successfully.")
    print("=" * 70)
    print(f"\nYT_REFRESH_TOKEN:\n{creds.refresh_token}\n")
    print("-" * 70)
    print("NEXT STEPS:")
    print("1. Go to your GitHub repository -> Settings -> Secrets and variables -> Actions")
    print("2. Add this secret:")
    print("   Name:  YT_REFRESH_TOKEN")
    print("   Value: [the token printed above]")
    print("3. Also ensure YT_CLIENT_ID and YT_CLIENT_SECRET are added as secrets.")
    print("4. Never commit this token into Git or share it.")
    print("=" * 70)


if __name__ == "__main__":
    main()
