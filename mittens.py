"""
Mittens - Your AI Assistant That Makes Sure You Show Up
========================================================
Runs as a web server on Railway (free tier).
Receives GPS from your iPhone, monitors Google Calendar,
sends ntfy push notifications that trigger iPhone alarms.

Architecture:
  - Flask web server (receives iPhone location POSTs, health checks)
  - Background thread (monitors events, checks if you need to leave)
  - ntfy.sh (sends push notifications → iPhone Automation sets alarm)
"""

import os
import hmac
import logging
import threading
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
load_dotenv()  # loads .env for local dev; no-op on Railway

from flask import Flask, request, jsonify, abort

from travel import TravelTimeEstimator
from alerts import AlertManager
from memory import MittensMemory
from monitor import MittensMonitor
from push_notifier import ExpoPushNotifier

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
        "sleep_hours": int(os.environ.get("SLEEP_HOURS", "0")),
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

        if not api_key:
            return f(*args, **kwargs)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_key = auth_header[7:]
        else:
            provided_key = request.args.get("key", "")

        if not provided_key:
            logger.warning(f"Unauthorized request to {request.path} (no key)")
            abort(401, description="Missing API key")

        if not hmac.compare_digest(provided_key, api_key):
            logger.warning(f"Unauthorized request to {request.path} (bad key)")
            abort(403, description="Invalid API key")

        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Push notification handler (shared with AlertManager)
push_notifier = ExpoPushNotifier()

# Shared state — passed to monitor, read/written by Flask routes
current_location = {"lat": None, "lon": None, "updated": None}
active_alerts = {}  # event_id -> alert state
shared_state = {
    "current_location": current_location,
    "active_alerts": active_alerts,
    "calendar": None,  # set by MittensMonitor.__init__
    "monitor_wake": threading.Event(),
    "push_notifier": push_notifier,
}


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

    calendar = shared_state["calendar"]
    if calendar is None:
        return jsonify({"alarm": False, "message": "calendar not ready"})

    config = load_config()
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

    calendar = shared_state["calendar"]
    if calendar:
        calendar.handle_webhook(channel_id, resource_id, resource_state)

    # Wake monitor to process changes immediately
    shared_state["monitor_wake"].set()

    # Google expects 200 OK, otherwise it retries
    return "", 200


# ---------------------------------------------------------------------------
# Push Token Registration
# ---------------------------------------------------------------------------
@app.route("/push-token", methods=["POST"])
@require_api_key
def register_push_token():
    """Register an Expo push token from the mobile app."""
    data = request.get_json(silent=True)
    if data and "token" in data:
        push_notifier.register_token(data["token"])
        logger.info(f"Push token registered from {data.get('platform', 'unknown')}")
        return jsonify({"status": "registered"}), 200
    return jsonify({"error": "need token"}), 400


# ---------------------------------------------------------------------------
# Start everything
# ---------------------------------------------------------------------------
def start_monitor(config):
    """Start the background monitoring thread."""
    monitor = MittensMonitor(config, shared_state)
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
