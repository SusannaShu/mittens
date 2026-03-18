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
import threading
from datetime import datetime, timedelta
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

    config = load_config()
    try:
        calendar = GoogleCalendarClient(config["google"])
    except Exception:
        return jsonify({"alarm": False, "message": "calendar error"})

    travel = TravelTimeEstimator(config.get("maps_api_key") or None)
    buffer = config.get("buffer_minutes", 5)
    events = calendar.get_upcoming_events(hours_ahead=2)
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
        self.config = config
        self.buffer = config.get("buffer_minutes", 5)
        self.poll_interval = config.get("poll_interval", 60)
        self.calendar = None
        self.travel = TravelTimeEstimator(config.get("maps_api_key") or None)
        self.alerts = AlertManager(config["email"])
        self.memory = MittensMemory()
        self._location_requested_at = None  # track when we last asked for GPS

        # Initialize Google Calendar
        try:
            self.calendar = GoogleCalendarClient(config["google"])
            logger.info("Google Calendar connected.")
        except Exception as e:
            logger.error(f"Google Calendar init failed: {e}")
            logger.error("Calendar monitoring disabled. Fix credentials and restart.")

    def run(self):
        """Main monitoring loop - runs forever in background thread."""
        logger.info(
            f"Monitor started. Polling every {self.poll_interval}s, "
            f"buffer: {self.buffer}min."
        )

        while True:
            try:
                if self.calendar:
                    self._tick()
            except Exception as e:
                logger.error(f"Monitor error: {e}", exc_info=True)

            time.sleep(self.poll_interval)

    def _tick(self):
        """Single check cycle."""
        now = datetime.now()

        events = self.calendar.get_upcoming_events(hours_ahead=2)
        location_events = [e for e in events if e.get("location")]

        if not location_events:
            return

        # Determine location: fresh GPS > request GPS > home fallback
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
                home_lat = float(os.environ.get("HOME_LAT", "0"))
                home_lon = float(os.environ.get("HOME_LON", "0"))
                if home_lat == 0 and home_lon == 0:
                    logger.warning("No GPS and no HOME_LAT/HOME_LON set. Skipping.")
                    return
                logger.info("No GPS after waiting, using home location.")
                my_loc = {"lat": home_lat, "lon": home_lon}

        for event in location_events:
            self._check_event(event, my_loc, now)

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
            self._escalate(event_id, event_summary, travel_minutes, minutes_until)

    def _escalate(self, event_id: str, summary: str, travel_min: float, minutes_until: float):
        now = datetime.now()

        if event_id not in active_alerts:
            active_alerts[event_id] = {
                "level": -1,
                "first_alert_time": now,
            }

        state = active_alerts[event_id]
        minutes_since_first = (now - state["first_alert_time"]).total_seconds() / 60

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
                        summary, minutes_until, travel_min
                    )

                self.memory.log_alert(summary, action, message)
                break

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
