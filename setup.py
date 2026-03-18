"""
Mittens Setup
=============
Run this first to configure Mittens.
Creates ~/.mittens/config.json with your API keys and preferences.

Usage: python setup.py
"""

import json
from pathlib import Path


def setup():
    config_dir = Path.home() / ".mittens"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.json"

    print("🐱 Mittens Setup")
    print("=" * 40)
    print()

    # --- Google Calendar ---
    print("📅 GOOGLE CALENDAR")
    print("You need OAuth credentials from Google Cloud Console.")
    print("1. Go to https://console.cloud.google.com/apis/credentials")
    print("2. Create an OAuth 2.0 Client ID (Desktop app)")
    print("3. Download the JSON file")
    print()
    creds_file = input("Path to Google OAuth credentials JSON [./credentials.json]: ").strip()
    if not creds_file:
        creds_file = "./credentials.json"

    calendar_ids = input("Calendar IDs to monitor (comma-separated) [primary]: ").strip()
    if not calendar_ids:
        calendar_ids = ["primary"]
    else:
        calendar_ids = [c.strip() for c in calendar_ids.split(",")]

    # --- Phone Number ---
    print()
    print("📞 YOUR PHONE")
    phone = input("Your phone number (e.g., +12125551234): ").strip()

    # --- Twilio ---
    print()
    print("📞 TWILIO (for phone calls - optional but recommended)")
    print("Sign up at https://www.twilio.com/try-twilio")
    print("Free tier gives you enough for testing.")
    print()
    twilio_sid = input("Twilio Account SID (leave blank to skip): ").strip()
    twilio_config = {}
    if twilio_sid:
        twilio_token = input("Twilio Auth Token: ").strip()
        twilio_from = input("Twilio Phone Number (e.g., +1234567890): ").strip()
        twilio_config = {
            "account_sid": twilio_sid,
            "auth_token": twilio_token,
            "from_number": twilio_from,
        }

    # --- Maps ---
    print()
    print("🗺️  GOOGLE MAPS (for travel time - optional)")
    print("Enable Directions API at https://console.cloud.google.com/apis/library/directions-backend.googleapis.com")
    maps_key = input("Google Maps API Key (leave blank for estimates): ").strip()

    # --- Location ---
    print()
    print("📍 LOCATION METHOD")
    print("Recommended: 'webhook' - set up an iPhone Shortcut to POST your location")
    print("The webhook server will run on port 5555.")
    print()
    method = input("Location method [webhook]: ").strip() or "webhook"

    # Build config
    config = {
        "google": {
            "credentials_file": creds_file,
            "calendar_ids": calendar_ids,
        },
        "phone_number": phone,
        "twilio": twilio_config,
        "maps_api_key": maps_key or None,
        "location": {
            "method": method,
            "webhook_port": 5555,
        },
        "buffer_minutes": 5,
    }

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print()
    print(f"✅ Config saved to {config_path}")
    print()
    print("NEXT STEPS:")
    print("1. Place your Google credentials.json in the project directory")
    print("2. Run: pip install -r requirements.txt")
    print("3. Run: python mittens.py")
    print("4. On first run, a browser will open for Google Calendar auth")
    print()

    if method == "webhook":
        print("📱 IPHONE SHORTCUT SETUP:")
        print("Create a new Shortcut with these steps:")
        print("  1. Get Current Location")
        print("  2. Dictionary:")
        print("       lat → Current Location.Latitude")
        print("       lon → Current Location.Longitude")
        print("  3. Get Contents of URL:")
        print(f"       URL: http://<your-mac-ip>:5555/location")
        print("       Method: POST")
        print("       Body: JSON → the dictionary from step 2")
        print()
        print("Then create a Personal Automation:")
        print("  Trigger: Time of Day → every 5 minutes (or use 'Repeat' action)")
        print("  Action: Run Shortcut → the one you just created")
        print()

    if not twilio_config:
        print("⚠️  No Twilio configured - Mittens can only send desktop notifications.")
        print("   For phone calls, add Twilio credentials to ~/.mittens/config.json")


if __name__ == "__main__":
    setup()
