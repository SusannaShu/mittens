"""
Google Calendar integration for Mittens.
Credentials loaded from environment variables (for Railway deployment).

Setup:
1. Create OAuth credentials at https://console.cloud.google.com/apis/credentials
2. Run `python auth_helper.py` locally to get a token
3. Set GOOGLE_CREDENTIALS_JSON and GOOGLE_TOKEN_JSON as Railway env vars
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger("mittens.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


class GoogleCalendarClient:
    def __init__(self, config: dict):
        """
        config:
          - credentials_json: the raw JSON string of OAuth credentials
          - token_json: the raw JSON string of the OAuth token (from auth_helper.py)
          - calendar_ids: list of calendar IDs to monitor
        """
        self.calendar_ids = config.get("calendar_ids", ["primary"])
        self.service = None

        token_json = config.get("token_json") or os.environ.get("GOOGLE_TOKEN_JSON", "")

        if not token_json:
            raise ValueError(
                "GOOGLE_TOKEN_JSON not set. Run auth_helper.py locally first, "
                "then paste the token JSON into Railway's environment variables."
            )

        try:
            token_data = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)

            if creds.expired and creds.refresh_token:
                logger.info("Refreshing Google Calendar token...")
                creds.refresh(Request())
                # Update the env var with refreshed token
                # (Railway persists env vars, but the refreshed token
                #  will only last until next deploy. The refresh_token
                #  itself is long-lived so this keeps working.)
                logger.info("Token refreshed successfully.")

            self.service = build("calendar", "v3", credentials=creds)
            logger.info("Google Calendar connected.")

        except Exception as e:
            logger.error(f"Google Calendar auth failed: {e}")
            raise

    def get_upcoming_events(self, hours_ahead: int = 2) -> list[dict]:
        """Fetch events in the next N hours that have a location."""
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(hours=hours_ahead)

        all_events = []

        for cal_id in self.calendar_ids:
            try:
                result = (
                    self.service.events()
                    .list(
                        calendarId=cal_id,
                        timeMin=now.isoformat(),
                        timeMax=time_max.isoformat(),
                        maxResults=10,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )

                for event in result.get("items", []):
                    parsed = self._parse_event(event)
                    if parsed and parsed.get("location"):
                        all_events.append(parsed)

            except Exception as e:
                logger.error(f"Error fetching calendar {cal_id}: {e}")

        logger.info(f"Found {len(all_events)} upcoming events with locations.")
        return all_events

    def _parse_event(self, event: dict) -> dict | None:
        """Parse a Google Calendar event into Mittens format."""
        start = event.get("start", {})
        start_str = start.get("dateTime") or start.get("date")

        if not start_str:
            return None

        try:
            if "T" in start_str:
                start_time = datetime.fromisoformat(start_str)
            else:
                return None  # all-day event, skip
        except ValueError:
            return None

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        return {
            "id": event.get("id", ""),
            "summary": event.get("summary", "Untitled Event"),
            "location": event.get("location"),
            "start_time": start_time,
            "description": event.get("description", ""),
        }
