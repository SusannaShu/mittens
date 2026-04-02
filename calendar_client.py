"""
Google Calendar integration for Mittens.
Credentials loaded from environment variables (for Railway deployment).

Setup:
1. Create OAuth credentials at https://console.cloud.google.com/apis/credentials
2. Run `python auth_helper.py` locally to get a token
3. Set GOOGLE_CREDENTIALS_JSON and GOOGLE_TOKEN_JSON as Railway env vars

Calendar Sync Strategy:
- At sunrise each morning, fetch the full day's calendar events.
- Google Calendar push notifications (webhooks) trigger re-fetch on changes.
- The background _tick() loop reads from cache, calculates travel from live GPS.
- No constant polling — events update only via morning fetch or webhook.
"""

import json
import logging
import os
import uuid
import threading
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

import requests as http_requests

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from travel import TravelTimeEstimator

logger = logging.getLogger("mittens.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

TOKEN_ALERT_EMAIL = "susanna.xinshu@gmail.com"
RESEND_API_URL = "https://api.resend.com/emails"


# How far ahead to watch for changes (Google max is ~30 days)
WATCH_EXPIRY_HOURS = 24



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

        # Token error alert (1 per day max)
        self._last_token_alert_date = None

        # Event cache
        self._cached_events = []          # list of parsed events
        self._cache_fetched_at = None     # datetime of last fetch
        self._cache_lock = threading.Lock()
        self._cache_dirty = True          # start dirty so first call fetches

        # Webhook state
        self._watch_channels = {}  # cal_id -> {"id": ..., "resourceId": ...}
        self._webhook_base_url = os.environ.get("WEBHOOK_BASE_URL", "")

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
            if "invalid_grant" in str(e) or "expired" in str(e).lower():
                self._send_token_error_alert(str(e))
            raise

        # Resolve calendar IDs (auto-discover if "all")
        self.calendar_ids = self._resolve_calendar_ids()

        # Initial fetch + set up webhooks
        self._do_fetch_events()
        self._setup_watches()

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

    # -------------------------------------------------------------------
    # Event cache: fetch once, serve from memory, re-fetch on webhook
    # -------------------------------------------------------------------

    def get_upcoming_events(self, hours_ahead: int = 2) -> list[dict]:
        """
        Return cached events. Re-fetches from Google only if:
        - Cache is marked dirty (webhook received)
        - Cache is empty (first call)
        Daily refresh is handled by do_morning_fetch() at sunrise.
        """
        with self._cache_lock:
            needs_refresh = (
                self._cache_dirty
                or self._cache_fetched_at is None
            )

        if needs_refresh:
            self._do_fetch_events()

        # Filter cached events to the requested window
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(hours=hours_ahead)

        with self._cache_lock:
            filtered = []
            for event in self._cached_events:
                start = event["start_time"]
                # Ensure timezone-aware for comparison
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if now - timedelta(minutes=15) <= start <= time_max:
                    filtered.append(event)

        return filtered

    def invalidate_cache(self):
        """Mark cache as dirty so next get_upcoming_events() re-fetches."""
        with self._cache_lock:
            self._cache_dirty = True
        logger.info("📅 Cache invalidated — will re-fetch on next read.")

    def do_morning_fetch(self):
        """Public trigger for the daily sunrise fetch."""
        logger.info("🌅 Morning fetch — pulling today's full schedule.")
        self._do_fetch_events()

    def _do_fetch_events(self):
        """Fetch today's events from Google Calendar API into cache."""
        tz = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
        now_local = datetime.now(tz)
        start_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        all_events = []

        for cal_id in self.calendar_ids:
            try:
                result = (
                    self.service.events()
                    .list(
                        calendarId=cal_id,
                        timeMin=start_of_day.isoformat(),
                        timeMax=end_of_day.isoformat(),
                        maxResults=50,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )

                raw_items = result.get("items", [])
                logger.info(
                    f"Calendar '{cal_id}': fetched {len(raw_items)} events "
                    f"for {now_local.strftime('%Y-%m-%d')}"
                )

                for event in raw_items:
                    summary = event.get("summary", "Untitled")
                    status = event.get("status", "unknown")
                    location = event.get("location", "")
                    logger.debug(
                        f"  Raw event: '{summary}' | status={status} | "
                        f"location='{location or 'none'}'"
                    )

                    if status == "cancelled":
                        continue

                    parsed = self._parse_event(event)
                    if not parsed:
                        continue

                    has_location = bool(parsed.get("location"))
                    has_virtual = self._has_virtual_meeting(parsed)
                    if has_location or has_virtual:
                        all_events.append(parsed)

            except Exception as e:
                logger.error(f"Error fetching calendar {cal_id}: {e}")
                if "invalid_grant" in str(e) or "expired" in str(e).lower():
                    self._send_token_error_alert(f"Calendar {cal_id}: {e}")

        with self._cache_lock:
            self._cached_events = all_events
            self._cache_fetched_at = datetime.now()
            self._cache_dirty = False

        logger.info(
            f"📅 Cache refreshed: {len(all_events)} events with locations/virtual "
            f"links for {now_local.strftime('%Y-%m-%d')}."
        )

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
        Find events on a given date whose summary contains prefix.
        Used to check if health events already exist for a date.
        """
        if not self.service:
            return []

        from zoneinfo import ZoneInfo
        tz_str = os.environ.get("TIMEZONE", "America/New_York")
        tz = ZoneInfo(tz_str)

        # Create timezone-aware start/end of day (RFC3339 required by API)
        start_of_day = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=tz)
        end_of_day = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=tz)

        # Search for "Mittens" without brackets for more reliable matching
        search_term = prefix.strip("[]")

        try:
            result = self.service.events().list(
                calendarId=calendar_id,
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                q=search_term,
                maxResults=20,
                singleEvents=True,
            ).execute()
            items = result.get("items", [])
            logger.debug(f"Search '{search_term}' on {date.date()} in {calendar_id}: found {len(items)}")
            return items
        except Exception as e:
            logger.error(f"Failed to search events: {e}")
            return []

    def delete_events_by_prefix(self, prefix: str, date: datetime,
                                calendar_id: str = "primary") -> int:
        """Delete all events matching prefix on a given date. Returns count deleted."""
        events = self.find_events_by_prefix(prefix, date, calendar_id)
        deleted = 0
        for event in events:
            try:
                self.service.events().delete(
                    calendarId=calendar_id, eventId=event["id"]
                ).execute()
                deleted += 1
            except Exception as e:
                logger.error(f"Failed to delete event {event.get('id')}: {e}")
        if deleted:
            logger.info(f"Deleted {deleted} old '{prefix}' events on {date.date()} from {calendar_id}")
        return deleted

    # -------------------------------------------------------------------
    # Google Calendar Push Notifications (Webhooks)
    # -------------------------------------------------------------------

    def _setup_watches(self):
        """
        Register push notification channels for each monitored calendar.
        Google will POST to our webhook URL when events change.

        Requires WEBHOOK_BASE_URL env var (e.g. https://mittens.up.railway.app).
        If not set, falls back to cache TTL polling (still better than every-tick).
        """
        if not self._webhook_base_url:
            logger.info(
                "📅 WEBHOOK_BASE_URL not set — using cache-only mode "
                f"(re-fetch every {CACHE_TTL_MINUTES}min)."
            )
            return

        webhook_url = f"{self._webhook_base_url.rstrip('/')}/calendar/webhook"
        expiry_ms = int(
            (datetime.now(timezone.utc) + timedelta(hours=WATCH_EXPIRY_HOURS))
            .timestamp() * 1000
        )

        for cal_id in self.calendar_ids:
            try:
                channel_id = f"mittens-{uuid.uuid4().hex[:12]}"
                body = {
                    "id": channel_id,
                    "type": "web_hook",
                    "address": webhook_url,
                    "expiration": expiry_ms,
                }
                result = self.service.events().watch(
                    calendarId=cal_id, body=body
                ).execute()

                self._watch_channels[cal_id] = {
                    "id": channel_id,
                    "resourceId": result.get("resourceId", ""),
                }
                logger.info(
                    f"📡 Watching calendar '{cal_id}' — "
                    f"channel={channel_id}, expires in {WATCH_EXPIRY_HOURS}h"
                )
            except Exception as e:
                logger.error(f"Failed to watch calendar '{cal_id}': {e}")

        if self._watch_channels:
            logger.info(
                f"📡 Webhook watches active for {len(self._watch_channels)} calendars. "
                f"Listening at {webhook_url}"
            )

    def renew_watches(self):
        """Re-register all watch channels. Call this before they expire."""
        logger.info("📡 Renewing calendar watch channels...")
        self.stop_watches()
        self._setup_watches()

    def stop_watches(self):
        """Stop all active watch channels (cleanup on shutdown)."""
        for cal_id, channel in self._watch_channels.items():
            try:
                self.service.channels().stop(body={
                    "id": channel["id"],
                    "resourceId": channel["resourceId"],
                }).execute()
                logger.info(f"📡 Stopped watch for '{cal_id}'")
            except Exception as e:
                logger.debug(f"Failed to stop watch for '{cal_id}': {e}")
        self._watch_channels.clear()

    def handle_webhook(self, channel_id: str, resource_id: str, resource_state: str):
        """
        Process an incoming Google Calendar push notification.
        Called by the /calendar/webhook Flask endpoint.

        resource_state values:
          - 'sync': initial sync confirmation (ignore)
          - 'exists': an event was created/updated
          - 'not_exists': an event was deleted
        """
        if resource_state == "sync":
            logger.info(f"📡 Webhook sync confirmation for channel {channel_id}")
            return

        logger.info(
            f"📡 Webhook: calendar changed! "
            f"state={resource_state}, channel={channel_id}"
        )
        self.invalidate_cache()

    # -------------------------------------------------------------------
    # Token error alert (1 email per day max)
    # -------------------------------------------------------------------

    def _send_token_error_alert(self, error_msg: str):
        """Send a one-time daily email if the Google token is broken."""
        today = date.today()
        if self._last_token_alert_date == today:
            return  # already sent today

        api_key = os.environ.get("RESEND_API_KEY", "")
        from_email = os.environ.get("FROM_EMAIL", "")
        if not api_key or not from_email:
            logger.warning("Can't send token alert — RESEND_API_KEY or FROM_EMAIL missing.")
            return

        body = (
            f"Mittens' Google Calendar token is broken!\n\n"
            f"Error: {error_msg}\n\n"
            f"Fix it:\n"
            f"1. Run `python auth_helper.py` locally\n"
            f"2. Log in via browser\n"
            f"3. Paste the new token JSON into Railway → GOOGLE_TOKEN_JSON\n\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
        )

        try:
            resp = http_requests.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": from_email,
                    "to": [TOKEN_ALERT_EMAIL],
                    "subject": "🚨 Mittens: Google Calendar token expired",
                    "text": body,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                self._last_token_alert_date = today
                logger.info(f"Token error alert sent to {TOKEN_ALERT_EMAIL}")
            else:
                logger.error(f"Token alert email failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Token alert email send error: {e}")
