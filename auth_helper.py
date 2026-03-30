"""
Google Calendar Auth Helper
============================
Run this ONCE on your MacBook to authorize Mittens with Google Calendar.
It will open a browser, you log in, and it prints a JSON token.
Paste that token into Railway's GOOGLE_TOKEN_JSON environment variable.

Usage:
  1. Download OAuth credentials from Google Cloud Console
  2. Save as credentials.json in this directory
  3. Run: python auth_helper.py
  4. Log in via browser
  5. Copy the printed JSON → paste into Railway env var GOOGLE_TOKEN_JSON
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    print("Mittens - Google Calendar Authorization")
    print("=" * 45)
    print()
    print("This will open your browser to log in with Google.")
    print("After authorizing, the token will be printed here.")
    print()

    creds_file = input("Path to credentials.json [./credentials.json]: ").strip()
    if not creds_file:
        creds_file = "./credentials.json"

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    creds = flow.run_local_server(port=0)

    # Serialize the token
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }

    token_json = json.dumps(token_data)

    print()
    print("=" * 45)
    print("SUCCESS! Copy everything between the lines below")
    print("and paste it as GOOGLE_TOKEN_JSON in Railway:")
    print("=" * 45)
    print(token_json)
    print("=" * 45)
    print()
    print("Also set GOOGLE_CREDENTIALS_JSON in Railway to the contents")
    print(f"of {creds_file} (the whole JSON file).")


if __name__ == "__main__":
    main()
