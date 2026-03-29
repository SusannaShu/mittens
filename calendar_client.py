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

from travel import TravelTimeEstimator

logger = logging.getLogger("mittens.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]



class GoogleCalendarClient:
    # Calendar types to skip when auto-discovering (they don't have locations)
    SKIP_CALENDAR_TYPES = {"holiday", "birthday"}


    def __init__(self, config: dict):
        """
        config:
          - credentials_json: the raw JSON string of OAuth credentials
          - token_json: the raw JSON string of the OAuth token (from auth_helper.py)
          - calendar_ids: list of calendar IDs to monitor
                          Use ["all"] to auto-discover all writable calendars.
        """
        self._config_calendar_ids = config.get("calendar_ids", ["primary"])
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
                logger.info("Token refreshed successfully.")

            self.service = build("calendar", "v3", credentials=creds)
            logger.info("Google Calendar connected.")

        except Exception as e:
            logger.error(f"Google Calendar auth failed: {e}")
            raise

        # Resolve calendar IDs (auto-discover if "all")
        self.calendar_ids = self._resolve_calendar_ids()

    def _resolve_calendar_ids(self) -> list[str]:
        """
        If CALENDAR_IDS contains 'all', auto-discover all calendars.
        Skips holiday/birthday calendars to reduce noise.
        """
        if "all" not in self._config_calendar_ids:
            return self._config_calendar_ids

        try:
            result = self.service.calendarList().list().execute()
            calendars = result.get("items", [])

            selected = []
            for cal in calendars:
                cal_id = cal.get("id", "")
                summary = cal.get("summary", "")
                access_role = cal.get("accessRole", "")

                # Skip holiday and birthday calendars
                if any(skip in summary.lower() for skip in self.SKIP_CALENDAR_TYPES):
                    logger.info(f"  Skipping calendar: '{summary}' ({cal_id})")
                    continue
                # Skip calendars with '#' in ID (Google system calendars like holidays)
                if "#" in cal_id:
                    logger.info(f"  Skipping system calendar: '{summary}' ({cal_id})")
                    continue

                selected.append(cal_id)
                logger.info(
                    f"  Monitoring calendar: '{summary}' ({cal_id}) "
                    f"[{access_role}]"
                )

            logger.info(f"Auto-discovered {len(selected)} calendars to monitor.")
            return selected

        except Exception as e:
            logger.error(f"Failed to auto-discover calendars: {e}")
            logger.info("Falling back to 'primary' only.")
            return ["primary"]

    def find_calendar_id_by_name(self, name: str) -> str | None:
        """Find a calendar ID by its display name (case-insensitive)."""
        if not self.service:
            return None
        try:
            result = self.service.calendarList().list().execute()
            for cal in result.get("items", []):
                if cal.get("summary", "").lower() == name.lower():
                    return cal.get("id")
        except Exception as e:
            logger.error(f"Failed to look up calendar '{name}': {e}")
        return None

    def get_upcoming_events(self, hours_ahead: int = 2) -> list[dict]:
        """Fetch events in the next N hours that have a location or a virtual meeting link."""
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
                        maxResults=50,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )

                raw_items = result.get("items", [])
                logger.info(
                    f"Calendar '{cal_id}': fetched {len(raw_items)} raw events"
                )

                for event in raw_items:
                    # Log every event we see for debugging
                    summary = event.get("summary", "Untitled")
                    status = event.get("status", "unknown")
                    organizer = event.get("organizer", {}).get("email", "unknown")
                    is_self_organized = event.get("organizer", {}).get("self", False)
                    location = event.get("location", "")
                    logger.debug(
                        f"  Raw event: '{summary}' | status={status} | "
                        f"organizer={organizer} (self={is_self_organized}) | "
                        f"location='{location or 'none'}'"
                    )

                    # Skip cancelled events
                    if status == "cancelled":
                        logger.debug(f"  Skipping cancelled event: '{summary}'")
                        continue

                    parsed = self._parse_event(event)
                    if not parsed:
                        logger.debug(f"  Skipping unparseable event: '{summary}'")
                        continue

                    has_location = bool(parsed.get("location"))
                    has_virtual = self._has_virtual_meeting(parsed)
                    if has_location or has_virtual:
                        all_events.append(parsed)
                    else:
                        logger.debug(
                            f"  Skipping event without location/virtual: '{summary}'"
                        )

            except Exception as e:
                logger.error(f"Error fetching calendar {cal_id}: {e}")

        logger.info(f"Found {len(all_events)} upcoming events with locations or virtual links.")
        return all_events

    @staticmethod
    def _has_virtual_meeting(event: dict) -> bool:
        """Check if an event has virtual meeting info in location or description."""
        location = event.get("location") or ""
        description = event.get("description") or ""
        # Also check conferenceData-derived hangout/meet links
        hangout_link = event.get("hangout_link") or ""
        return (
            TravelTimeEstimator.is_virtual_location(location)
            or TravelTimeEstimator.is_virtual_location(description)
            or TravelTimeEstimator.is_virtual_location(hangout_link)
        )

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

        # Extract hangout/Meet link from conferenceData if available
        hangout_link = event.get("hangoutLink", "")

        # Build description with hangout link appended if present
        description = event.get("description", "")
        if hangout_link and hangout_link not in description:
            description = f"{description}\n{hangout_link}".strip()

        return {
            "id": event.get("id", ""),
            "summary": event.get("summary", "Untitled Event"),
            "location": event.get("location"),
            "start_time": start_time,
            "description": description,
            "hangout_link": hangout_link,
            "organizer": event.get("organizer", {}).get("email", ""),
        }

    def create_event(self, summary: str, start_dt: datetime,
                     duration_minutes: int = 30, description: str = "",
                     calendar_id: str = "primary",
                     timezone_str: str = "America/New_York") -> str | None:
        """
        Create a calendar event. Returns event ID if successful.
        """
        if not self.service:
            return None

        end_dt = start_dt + timedelta(minutes=duration_minutes)

        event_body = {
            "summary": summary,
            "start": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": timezone_str,
            },
            "end": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": timezone_str,
            },
            "description": description,
        }

        try:
            result = self.service.events().insert(
                calendarId=calendar_id, body=event_body
            ).execute()
            event_id = result.get("id", "")
            logger.info(f"Created event: '{summary}' at {start_dt.strftime('%I:%M %p')}")
            return event_id
        except Exception as e:
            logger.error(f"Failed to create event '{summary}': {e}")
            return None

    def find_events_by_prefix(self, prefix: str, date: datetime,
                              calendar_id: str = "primary") -> list:
        """
        Find events on a given date whose summary starts with a prefix.
        Used to check if meal events already exist for today.
        """
        if not self.service:
            return []

        start_of_day = date.replace(hour=0, minute=0, second=0).isoformat() + "Z"
        end_of_day = date.replace(hour=23, minute=59, second=59).isoformat() + "Z"

        try:
            result = self.service.events().list(
                calendarId=calendar_id,
                timeMin=start_of_day,
                timeMax=end_of_day,
                q=prefix,
                maxResults=10,
                singleEvents=True,
            ).execute()
            return result.get("items", [])
        except Exception as e:
            logger.error(f"Failed to search events: {e}")
            return []

