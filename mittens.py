"""
Mittens - Your AI Assistant That Makes Sure You Show Up
========================================================
Runs as a web server on Railway (free tier).
Receives GPS from your iPhone, monitors Google Calendar,
sends ntfy push notifications that trigger iPhone alarms.

Architecture:
  - Flask web server (receives iPhone location POSTs, health checks)
  - Background thread (polls calendar, checks if you need to leave)
  - ntfy.sh (sends push notifications → iPhone Automation sets alarm)
"""

import os
import json
import time
import hmac
import logging
import requests
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from functools import wraps

from dotenv import load_dotenv
load_dotenv()  # loads .env for local dev; no-op on Railway

from flask import Flask, request, jsonify, abort

from calendar_client import GoogleCalendarClient
from travel import TravelTimeEstimator
from alerts import AlertManager
from memory import MittensMemory

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("mittens")

# ---------------------------------------------------------------------------
# Config - from environment variables (Railway sets these)
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """
    Load config from environment variables.
    On Railway, set these in your project's Variables tab.
    For local dev, use a .env file or export them.
    """
    config = {
        "google": {
            "credentials_json": os.environ.get("GOOGLE_CREDENTIALS_JSON", ""),
            "token_json": os.environ.get("GOOGLE_TOKEN_JSON", ""),
            "calendar_ids": os.environ.get("CALENDAR_IDS", "primary").split(","),
        },
        "email": {
            "resend_api_key": os.environ.get("RESEND_API_KEY", ""),
            "from_email": os.environ.get("FROM_EMAIL", "system@sheyoufashion.com"),
            "to_email": os.environ.get("TO_EMAIL", ""),
        },
        "maps_api_key": os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        "buffer_minutes": int(os.environ.get("BUFFER_MINUTES", "5")),
        "poll_interval": int(os.environ.get("POLL_INTERVAL", "60")),
        # Sleep hours: bedtime = next sunrise - SLEEP_HOURS (0 = disabled)
        "sleep_hours": int(os.environ.get("SLEEP_HOURS", "0")),
        # Security: API key for authenticating iPhone requests
        "api_key": os.environ.get("MITTENS_API_KEY", ""),
    }

    if not config["email"]["resend_api_key"]:
        logger.warning("RESEND_API_KEY not set! Mittens can't send email alerts.")

    if not config["api_key"]:
        logger.warning(
            "MITTENS_API_KEY not set! Endpoints are UNPROTECTED. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return config


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------
def require_api_key(f):
    """
    Decorator that checks for a valid API key.
    The key can be sent as:
      - Header: Authorization: Bearer <key>
      - Query param: ?key=<key>  (convenient for iPhone Shortcuts)
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = app.config.get("MITTENS_API_KEY", "")

        # If no key is configured, allow all requests (dev mode)
        # but log a warning
        if not api_key:
            return f(*args, **kwargs)

        # Check header first
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_key = auth_header[7:]
        else:
            # Fall back to query param
            provided_key = request.args.get("key", "")

        if not provided_key:
            logger.warning(f"Unauthorized request to {request.path} (no key)")
            abort(401, description="Missing API key")

        # Constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(provided_key, api_key):
            logger.warning(f"Unauthorized request to {request.path} (bad key)")
            abort(403, description="Invalid API key")

        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Flask App (web server for location webhook + health checks)
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Shared state
current_location = {"lat": None, "lon": None, "updated": None}
active_alerts = {}  # event_id -> alert state
shared_calendar = None  # set by MittensMonitor.__init__


@app.route("/", methods=["GET"])
def health():
    """Health check for Railway. Public - no sensitive info."""
    return jsonify({
        "status": "awake",
        "service": "mittens",
    })


@app.route("/location", methods=["POST"])
@require_api_key
def receive_location():
    """
    Receive GPS from iPhone Shortcut.
    POST JSON: {"lat": 40.7128, "lon": -74.0060}
    """
    data = request.get_json(silent=True)
    if data and "lat" in data and "lon" in data:
        current_location["lat"] = float(data["lat"])
        current_location["lon"] = float(data["lon"])
        current_location["updated"] = datetime.now()
        logger.debug(
            f"Location updated: {current_location['lat']:.4f}, "
            f"{current_location['lon']:.4f}"
        )
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "need lat and lon"}), 400


@app.route("/location", methods=["GET"])
@require_api_key
def get_location():
    """Debug: check what Mittens thinks your location is."""
    if current_location["lat"] is not None:
        return jsonify({
            "lat": current_location["lat"],
            "lon": current_location["lon"],
            "updated": current_location["updated"].isoformat() if current_location["updated"] else None,
        })
    return jsonify({"error": "no location yet"}), 404


@app.route("/test", methods=["POST"])
@require_api_key
def test_alert():
    """Send a test email to verify Resend integration."""
    config = load_config()
    alerts = AlertManager(config["email"])
    alerts.test()
    return jsonify({"status": "test sent"})


@app.route("/stats", methods=["GET"])
@require_api_key
def stats():
    """View attendance stats from memory."""
    memory = MittensMemory()
    return jsonify({
        "overall": memory.get_attendance_stats(),
        "recent_alerts": memory.get_recent_alerts(5),
    })


@app.route("/check", methods=["POST"])
@require_api_key
def check_alarm():
    """
    Called by iPhone Shortcut after sending location.
    Returns whether an alarm should be set right now.

    Response: {"alarm": true/false, "message": "...", "event": "..."}
    """
    if current_location["lat"] is None:
        return jsonify({"alarm": False, "message": "no location yet"})

    if shared_calendar is None:
        return jsonify({"alarm": False, "message": "calendar not ready"})

    config = load_config()
    travel = TravelTimeEstimator(config.get("maps_api_key") or None)
    buffer = config.get("buffer_minutes", 5)
    events = shared_calendar.get_upcoming_events(hours_ahead=2)
    location_events = [e for e in events if e.get("location")]

    if not location_events:
        return jsonify({"alarm": False, "message": "no upcoming events"})

    my_loc = {"lat": current_location["lat"], "lon": current_location["lon"]}
    now = datetime.now()

    for event in location_events:
        event_start = event["start_time"]
        event_summary = event.get("summary", "Appointment")

        if event_start.tzinfo is not None:
            now_aware = now.astimezone()
            minutes_until = (event_start - now_aware).total_seconds() / 60
        else:
            minutes_until = (event_start - now).total_seconds() / 60

        if minutes_until < -15:
            continue

        travel_minutes = travel.get_travel_time(
            origin=my_loc, destination=event["location"]
        )
        if travel_minutes is None:
            continue

        need_to_leave_in = minutes_until - travel_minutes - buffer

        if travel_minutes <= 2:
            continue

        if need_to_leave_in <= 0:
            message = (
                f"{event_summary} in {minutes_until:.0f} min — "
                f"you're {travel_minutes:.0f} min away. GO!"
            )
            return jsonify({
                "alarm": True,
                "message": message,
                "event": event_summary,
                "minutes_until": round(minutes_until),
                "travel_minutes": round(travel_minutes),
            })

    return jsonify({"alarm": False, "message": "you're on track"})


@app.route("/calendar/webhook", methods=["POST"])
def calendar_webhook():
    """
    Receives Google Calendar push notifications.
    Google sends headers (not JSON body) with change info.
    No auth required — Google won't send our API key.
    We validate via channel ID matching instead.
    """
    channel_id = request.headers.get("X-Goog-Channel-ID", "")
    resource_id = request.headers.get("X-Goog-Resource-ID", "")
    resource_state = request.headers.get("X-Goog-Resource-State", "")

    logger.info(
        f"📡 Webhook received: state={resource_state}, "
        f"channel={channel_id}"
    )

    if shared_calendar:
        shared_calendar.handle_webhook(channel_id, resource_id, resource_state)

    # Google expects 200 OK, otherwise it retries
    return "", 200



# ---------------------------------------------------------------------------
# Background Monitor (the brain)
# ---------------------------------------------------------------------------
class MittensMonitor:
    """
    Runs in a background thread. Every POLL_INTERVAL seconds:
    1. Fetch upcoming calendar events with locations
    2. Calculate travel time from current GPS
    3. If you should have left already → send ntfy alarm
    """

    ESCALATION = [
        ("alarm", 0),          # immediately: ALARM (triggers iPhone timer)
        ("alarm", 5),          # 5 min later: alarm again
        ("alarm", 10),         # 10 min later: one more
    ]

    def __init__(self, config: dict):
        global shared_calendar

        self.config = config
        self.buffer = config.get("buffer_minutes", 5)
        self.poll_interval = config.get("poll_interval", 60)
        self.calendar = None
        self.travel = TravelTimeEstimator(config.get("maps_api_key") or None)
        self.alerts = AlertManager(config["email"])
        self.memory = MittensMemory()
        self._location_requested_at = None  # track when we last asked for GPS
        self._last_watch_renewal = None     # track webhook channel renewal

        # Bedtime config: dynamic based on sunrise
        self.sleep_hours = config.get("sleep_hours", 0)
        self.home_lat = float(os.environ.get("HOME_LAT", "0"))
        self.home_lon = float(os.environ.get("HOME_LON", "0"))
        self._cached_sunrise = {}  # {date: sunrise_datetime}
        if self.sleep_hours > 0:
            logger.info(
                f"🛏️ Sleep target: {self.sleep_hours}h. "
                f"Bedtime = tomorrow's sunrise - {self.sleep_hours}h."
            )
        else:
            logger.info("SLEEP_HOURS=0. Bedtime alerts disabled.")

        # Initialize Google Calendar (fetches events once + sets up webhooks)
        try:
            self.calendar = GoogleCalendarClient(config["google"])
            shared_calendar = self.calendar  # expose to Flask endpoints
            logger.info("Google Calendar connected (events cached, webhooks active).")
        except Exception as e:
            logger.error(f"Google Calendar init failed: {e}")
            logger.error("Calendar monitoring disabled. Fix credentials and restart.")

        # Track which date we've scheduled meals for
        self._meals_scheduled_date = None
        # Track daily morning calendar fetch (initial fetch happens in calendar __init__)
        self._morning_fetch_date = datetime.now().date()

    def run(self):
        """
        Main monitoring loop - runs forever in background thread.
        Events are served from cache (populated on startup + webhook).
        This loop just checks travel times against cached events.
        """
        logger.info(
            f"Monitor started. Checking every {self.poll_interval}s, "
            f"buffer: {self.buffer}min. "
            f"Calendar events fetched via cache + webhooks."
        )

        while True:
            try:
                if self.calendar:
                    self._morning_fetch_if_needed()
                    self._schedule_meals_if_needed()
                    self._renew_watches_if_needed()
                    self._tick()
            except Exception as e:
                logger.error(f"Monitor error: {e}", exc_info=True)

            time.sleep(self.poll_interval)

    def _renew_watches_if_needed(self):
        """Renew webhook watch channels every 20 hours (they expire at 24h)."""
        now = datetime.now()
        if self._last_watch_renewal is None:
            self._last_watch_renewal = now
            return

        hours_since = (now - self._last_watch_renewal).total_seconds() / 3600
        if hours_since >= 20:
            self.calendar.renew_watches()
            self._last_watch_renewal = now

    def _morning_fetch_if_needed(self):
        """At sunrise each day, pull the full day's calendar from Google."""
        today = datetime.now().date()
        if self._morning_fetch_date == today:
            return  # already fetched today

        # Wait until sunrise before fetching
        sunrise = self._get_sunrise(today)
        now = datetime.now()
        if sunrise is not None and now < sunrise:
            return  # not sunrise yet

        self.calendar.do_morning_fetch()
        self._morning_fetch_date = today

    def _schedule_meals_if_needed(self):
        """Create meal, bedtime, and sunrise events in Health calendar for 3 days."""
        if self.sleep_hours <= 0:
            return  # sunrise not configured

        today = datetime.now().date()
        if self._meals_scheduled_date == today:
            return  # already done for today

        # Find the Health calendar (fall back to primary)
        health_cal = os.environ.get("HEALTH_CALENDAR", "Health")
        cal_id = self.calendar.find_calendar_id_by_name(health_cal)
        if cal_id:
            logger.info(f"📅 Using '{health_cal}' calendar for health events.")
        else:
            cal_id = "primary"
            logger.info(f"📅 '{health_cal}' not found, using primary calendar.")

        tz_str = os.environ.get("TIMEZONE", "America/New_York")
        days_ahead = 3

        for day_offset in range(days_ahead):
            target_date = today + timedelta(days=day_offset)
            target_dt = datetime.combine(target_date, datetime.min.time())

            # Clean up any existing [Mittens] events for this date
            self.calendar.delete_events_by_prefix(
                "[Mittens]", target_dt, calendar_id=cal_id
            )
            # Also clean up old events on primary if using a different calendar
            if cal_id != "primary":
                self.calendar.delete_events_by_prefix(
                    "[Mittens]", target_dt, calendar_id="primary"
                )

            # Get sunrise for this date
            sunrise = self._get_sunrise(target_date)
            if sunrise is None:
                continue

            # Calculate bedtime (for the night before: tomorrow's sunrise - sleep_hours)
            # For today's events, we show tonight's bedtime
            next_sunrise = self._get_sunrise(target_date + timedelta(days=1))
            bedtime = None
            if next_sunrise:
                bedtime = next_sunrise - timedelta(hours=self.sleep_hours)

            # Build events for this day
            # 12-hour eating window: breakfast → dinner ends at sunrise + 12h
            meal_duration = 20
            dinner_start = sunrise + timedelta(hours=12) - timedelta(minutes=meal_duration)
            lunch_start = sunrise + timedelta(hours=5, minutes=50)
            events_to_create = [
                ("🍳 [Mittens] Breakfast", sunrise, meal_duration,
                 "Eat within 30 min of waking."),
                ("🥗 [Mittens] Lunch", lunch_start, meal_duration,
                 "Midday fuel."),
                ("🍽️ [Mittens] Dinner", dinner_start, meal_duration,
                 "Last meal — eating window closes."),
            ]

            # Add bedtime (starts 30 min early for getting ready)
            if bedtime:
                bedtime_prep = bedtime - timedelta(minutes=30)
                events_to_create.append(
                    ("😴 [Mittens] Bedtime", bedtime_prep, 60,
                     f"Start winding down. Lights out by {bedtime.strftime('%I:%M %p')}.")
                )

            for summary, start_time, duration, desc in events_to_create:
                self.calendar.create_event(
                    summary=summary,
                    start_dt=start_time,
                    duration_minutes=duration,
                    description=desc,
                    calendar_id=cal_id,
                    timezone_str=tz_str,
                )

            bedtime_str = bedtime.strftime('%I:%M %p') if bedtime else "N/A"
            logger.info(
                f"🍽️ Health events for {target_date}: "
                f"B {sunrise.strftime('%I:%M %p')}, "
                f"L {lunch_start.strftime('%I:%M %p')}, "
                f"D {dinner_start.strftime('%I:%M %p')}, "
                f"Bed {bedtime_str}"
            )

        self._meals_scheduled_date = today

    def _tick(self):
        """Single check cycle."""
        now = datetime.now()

        events = self.calendar.get_upcoming_events(hours_ahead=2)
        location_events = [e for e in events if e.get("location")]
        virtual_only_events = [
            e for e in events
            if not e.get("location")
            and TravelTimeEstimator.is_virtual_location(e.get("description", ""))
        ]

        # Virtual-only events (Zoom in description, no location) — no GPS needed
        for event in virtual_only_events:
            self._check_virtual_only_event(event, now)

        # Check if we need GPS (for physical events or bedtime)
        needs_gps = bool(location_events) or self._bedtime_needs_check(now)
        if not needs_gps:
            return

        # Determine location: fresh GPS > request GPS > home fallback
        my_loc = None
        if current_location["lat"] is not None:
            my_loc = {"lat": current_location["lat"], "lon": current_location["lon"]}
            # If GPS is stale (>30 min), request a fresh one but keep using it
            if current_location["updated"]:
                age = (now - current_location["updated"]).total_seconds()
                if age > 1800:
                    logger.info(f"GPS is {age/60:.0f}min old, requesting fresh location.")
                    self._request_location_if_needed(now)
        else:
            # No GPS at all — request it via email and wait for response
            self._request_location_if_needed(now)

            # Wait up to 45s for iPhone to send GPS back
            for i in range(9):
                time.sleep(5)
                if current_location["lat"] is not None:
                    logger.info("GPS received from iPhone!")
                    my_loc = {"lat": current_location["lat"], "lon": current_location["lon"]}
                    break
            else:
                # Still no GPS — fall back to home location
                if self.home_lat == 0 and self.home_lon == 0:
                    logger.warning("No GPS and no HOME_LAT/HOME_LON set. Skipping.")
                    return
                logger.info("No GPS after waiting, using home location.")
                my_loc = {"lat": self.home_lat, "lon": self.home_lon}

        if my_loc is None:
            return

        for event in location_events:
            self._check_event(event, my_loc, now)

        # Check bedtime: do you need to head home?
        self._check_bedtime(my_loc, now)

    def _check_event(self, event: dict, my_location: dict, now: datetime):
        event_id = event["id"]
        event_start = event["start_time"]
        event_location = event["location"]
        event_summary = event.get("summary", "Appointment")

        # Make both datetimes naive for comparison (or both aware)
        if event_start.tzinfo is not None:
            from datetime import timezone
            now_aware = now.astimezone()
            minutes_until = (event_start - now_aware).total_seconds() / 60
        else:
            minutes_until = (event_start - now).total_seconds() / 60

        # Skip events that already started
        if minutes_until < -15:
            if event_id in active_alerts:
                del active_alerts[event_id]
            return

        # Calculate travel time
        travel_minutes = self.travel.get_travel_time(
            origin=my_location,
            destination=event_location,
        )

        if travel_minutes is None:
            if TravelTimeEstimator.is_virtual_location(event_location) or \
               TravelTimeEstimator.is_virtual_location(event.get("description", "")):
                self._handle_virtual_meeting(
                    event_id, event_summary, minutes_until,
                    event_location, event.get("description", "")
                )
            else:
                logger.warning(f"Could not calc travel to '{event_summary}'")
            return

        need_to_leave_in = minutes_until - travel_minutes - self.buffer

        logger.info(
            f"{event_summary} in {minutes_until:.0f}min | "
            f"Travel: {travel_minutes:.0f}min | "
            f"Leave in: {need_to_leave_in:.0f}min"
        )

        # Log to memory
        self.memory.log_check(
            event_id=event_id,
            event_summary=event_summary,
            minutes_until=minutes_until,
            travel_minutes=travel_minutes,
            location=my_location,
        )

        # Already near destination?
        if travel_minutes <= 2:
            if event_id in active_alerts:
                logger.info(f"You're at/near '{event_summary}'. Nice!")
                self.memory.log_arrival(event_id, event_summary)
                del active_alerts[event_id]
            return

        # Should you have left already?
        if need_to_leave_in <= 0:
            self._escalate(event_id, event_summary, travel_minutes, minutes_until, event_location)

    def _check_virtual_only_event(self, event: dict, now: datetime):
        """Handle events with virtual meeting info in description but no location."""
        event_id = event["id"]
        event_start = event["start_time"]
        event_summary = event.get("summary", "Appointment")

        if event_start.tzinfo is not None:
            now_aware = now.astimezone()
            minutes_until = (event_start - now_aware).total_seconds() / 60
        else:
            minutes_until = (event_start - now).total_seconds() / 60

        if minutes_until < -15:
            if event_id in active_alerts:
                del active_alerts[event_id]
            return

        self._handle_virtual_meeting(
            event_id, event_summary, minutes_until,
            event.get("location", ""), event.get("description", "")
        )

    def _handle_virtual_meeting(self, event_id: str, event_summary: str,
                                minutes_until: float, location: str,
                                description: str = ""):
        """Send a MITTENS_ZOOM email ~5 min before a virtual meeting (once per event)."""
        # Only send when we're in the 3-7 min window (catches it within one poll cycle)
        if not (3 <= minutes_until <= 7):
            if minutes_until > 7:
                logger.info(
                    f"💻 Virtual meeting '{event_summary}' in {minutes_until:.0f}min, "
                    f"will remind at ~5min."
                )
            return

        # Only send once per event
        if event_id in active_alerts and active_alerts[event_id].get("zoom_reminded"):
            return

        # Extract meeting link from location or description
        zoom_link = ""
        for text in [location, description]:
            for word in text.split():
                if word.startswith("http") and TravelTimeEstimator.is_virtual_location(word):
                    zoom_link = word
                    break
            if zoom_link:
                break

        logger.info(f"💻 Sending Zoom reminder for '{event_summary}' ({minutes_until:.0f}min away)")
        self.alerts.send_zoom_reminder(event_summary, minutes_until, zoom_link)

        # Mark as reminded so we don't spam
        if event_id not in active_alerts:
            active_alerts[event_id] = {"level": -1, "first_alert_time": datetime.now()}
        active_alerts[event_id]["zoom_reminded"] = True

    def _escalate(self, event_id: str, summary: str, travel_min: float, minutes_until: float, location: str = ""):
        now = datetime.now()

        if event_id not in active_alerts:
            active_alerts[event_id] = {
                "level": -1,
                "first_alert_time": now,
            }

        state = active_alerts[event_id]
        minutes_since_first = (now - state["first_alert_time"]).total_seconds() / 60

        logger.info(
            f"Escalation check: level={state['level']}, "
            f"min_since_first={minutes_since_first:.1f}"
        )

        # Find next escalation level
        for i, (action, delay) in enumerate(self.ESCALATION):
            if i > state["level"] and minutes_since_first >= delay:
                state["level"] = i
                message = (
                    f"{summary} is in {minutes_until:.0f} minutes "
                    f"and you're {travel_min:.0f} minutes away. "
                    f"Get up and go!"
                )

                if action == "notification":
                    self.alerts.send_notification(
                        message, summary, minutes_until, travel_min
                    )
                else:
                    self.alerts.send_alarm(
                        summary, minutes_until, travel_min,
                        location=location
                    )

                self.memory.log_alert(summary, action, message)
                logger.info(f"Escalation fired: level={i}, action={action}")
                break
        else:
            logger.info(f"No escalation to fire (max level reached)")

    def _bedtime_needs_check(self, now: datetime) -> bool:
        """Check if we're within 2 hours of bedtime (worth checking travel)."""
        if self.sleep_hours <= 0:
            return False
        bedtime = self._get_bedtime(now)
        if bedtime is None:
            return False
        minutes_until = (bedtime - now).total_seconds() / 60
        return -15 < minutes_until <= 120

    def _get_sunrise(self, for_date) -> datetime | None:
        """
        Fetch sunrise time from sunrise-sunset.org API.
        Caches result per day to avoid repeated API calls.
        """
        # Return cached value if we already fetched for this date
        if for_date in self._cached_sunrise:
            return self._cached_sunrise[for_date]

        if self.home_lat == 0 and self.home_lon == 0:
            return None

        try:
            resp = requests.get(
                "https://api.sunrise-sunset.org/json",
                params={
                    "lat": self.home_lat,
                    "lng": self.home_lon,
                    "date": for_date.isoformat(),
                    "formatted": 0,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("status") != "OK":
                logger.error(f"Sunrise API error: {data}")
                return None

            sunrise_utc = datetime.fromisoformat(data["results"]["sunrise"])
            # Convert to user's local timezone (not server's)
            user_tz = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
            sunrise_local = sunrise_utc.astimezone(user_tz)
            # Make naive for comparison with naive datetime.now()
            sunrise_local = sunrise_local.replace(tzinfo=None)

            self._cached_sunrise[for_date] = sunrise_local
            logger.info(
                f"\U0001f305 Sunrise on {for_date}: {sunrise_local.strftime('%I:%M %p')}"
            )
            return sunrise_local

        except Exception as e:
            logger.error(f"Sunrise API failed: {e}")
            return None

    def _get_bedtime(self, now: datetime) -> datetime | None:
        """Calculate tonight's bedtime = tomorrow's sunrise - SLEEP_HOURS."""
        tomorrow = (now + timedelta(days=1)).date()
        sunrise = self._get_sunrise(tomorrow)
        if sunrise is None:
            return None
        return sunrise - timedelta(hours=self.sleep_hours)

    def _check_bedtime(self, my_location: dict, now: datetime):
        """Check if you need to head home for bedtime."""
        if self.sleep_hours <= 0:
            return
        if self.home_lat == 0 and self.home_lon == 0:
            return

        bedtime = self._get_bedtime(now)
        if bedtime is None:
            return
        minutes_until = (bedtime - now).total_seconds() / 60

        # Only check 2 hours before bedtime, stop 15 min after
        if minutes_until < -15 or minutes_until > 120:
            return

        # Send downtime alert 30 min before bedtime (once per night)
        downtime_key = f"downtime_{now.date()}"
        if 25 <= minutes_until <= 35 and downtime_key not in active_alerts:
            bedtime_str = bedtime.strftime("%I:%M %p")
            logger.info(f"🌙 Sending downtime alert — bedtime at {bedtime_str}")
            self.alerts.send_downtime_alert(bedtime_str)
            active_alerts[downtime_key] = {"sent": True}

        # Already home? No need to alert for travel
        travel_minutes = self.travel.get_travel_time(
            origin=my_location,
            destination=f"{self.home_lat},{self.home_lon}",
        )

        if travel_minutes is None or travel_minutes <= 5:
            if travel_minutes is not None and travel_minutes <= 5:
                logger.debug("🛏️ Already home, no bedtime alert needed.")
                if "bedtime" in active_alerts:
                    del active_alerts["bedtime"]
            return

        # 30 min buffer to get ready for bed after arriving home
        bedtime_prep = 30
        need_to_leave_in = minutes_until - travel_minutes - bedtime_prep

        logger.info(
            f"🛏️ Bedtime at {bedtime.strftime('%I:%M %p')} ({minutes_until:.0f}min) | "
            f"Home: {travel_minutes:.0f}min away | "
            f"Leave in: {need_to_leave_in:.0f}min"
        )

        if need_to_leave_in <= 0:
            self._escalate(
                "bedtime", "Bedtime (head home!)",
                travel_minutes, minutes_until,
                location="Home"
            )

    def _request_location_if_needed(self, now):
        """Send email requesting GPS, but max once per 10 minutes."""
        if self._location_requested_at:
            elapsed = (now - self._location_requested_at).total_seconds()
            if elapsed < 600:  # 10 min cooldown
                return
        self._location_requested_at = now
        self.alerts.request_location()


# ---------------------------------------------------------------------------
# Start everything
# ---------------------------------------------------------------------------
def start_monitor(config):
    """Start the background monitoring thread."""
    monitor = MittensMonitor(config)
    thread = threading.Thread(target=monitor.run, daemon=True)
    thread.start()
    logger.info("Background monitor thread started.")


# Load config and start monitor when the app starts
config = load_config()
app.config["MITTENS_API_KEY"] = config.get("api_key", "")
start_monitor(config)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5555))
    logger.info(f"Mittens starting on port {port}")
    app.run(host="0.0.0.0", port=port)
