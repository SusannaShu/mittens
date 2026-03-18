"""
Location Provider for Mittens.
Gets your iPhone's current GPS coordinates.

Supports multiple methods (in priority order):
1. Apple Shortcuts webhook - iPhone POSTs location to Mittens every few minutes
2. FindMy (iCloud) - pulls location from Apple's Find My network (unofficial)
3. Manual / fallback - last known location

For v1, the Shortcuts webhook is recommended:
  - Create a Shortcut on iPhone: "Get Current Location" → "Get Contents of URL"
  - Set it to POST to http://<your-mac-ip>:5555/location every 5 minutes
  - Use a Personal Automation trigger (time-based, every 5 min)
"""

import logging
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify

logger = logging.getLogger("mittens.location")


class LocationProvider:
    def __init__(self, config: dict):
        """
        config options:
          - method: "webhook" | "findmy" | "manual"
          - webhook_port: port for the location webhook server (default: 5555)
          - manual_lat: fallback latitude
          - manual_lon: fallback longitude
        """
        self.method = config.get("method", "webhook")
        self.webhook_port = config.get("webhook_port", 5555)
        self._last_location = None
        self._last_update = None

        # If manual location provided as default
        if config.get("manual_lat") and config.get("manual_lon"):
            self._last_location = {
                "lat": config["manual_lat"],
                "lon": config["manual_lon"],
            }
            self._last_update = datetime.now()

        if self.method == "webhook":
            self._start_webhook_server()

    def _start_webhook_server(self):
        """
        Start a tiny Flask server that receives location POSTs from iPhone Shortcuts.

        Your iPhone Shortcut should POST JSON to http://<mac-local-ip>:5555/location
        Body: {"lat": 40.7128, "lon": -74.0060}

        The Shortcut steps:
        1. Get Current Location
        2. Dictionary:
             lat → Current Location.Latitude
             lon → Current Location.Longitude
        3. Get Contents of URL:
             URL: http://<your-mac-ip>:5555/location
             Method: POST
             Body: JSON (the dictionary)
        """
        app = Flask("mittens-location")
        app.logger.setLevel(logging.WARNING)  # quiet Flask

        @app.route("/location", methods=["POST"])
        def receive_location():
            data = request.get_json(silent=True)
            if data and "lat" in data and "lon" in data:
                self._last_location = {
                    "lat": float(data["lat"]),
                    "lon": float(data["lon"]),
                }
                self._last_update = datetime.now()
                logger.debug(
                    f"📍 Location updated: {self._last_location['lat']:.4f}, "
                    f"{self._last_location['lon']:.4f}"
                )
                return jsonify({"status": "ok"}), 200
            return jsonify({"error": "need lat and lon"}), 400

        @app.route("/location", methods=["GET"])
        def get_location():
            """Debug endpoint - check what Mittens thinks your location is."""
            if self._last_location:
                return jsonify({
                    **self._last_location,
                    "updated": self._last_update.isoformat() if self._last_update else None,
                    "age_seconds": (
                        (datetime.now() - self._last_update).total_seconds()
                        if self._last_update else None
                    ),
                })
            return jsonify({"error": "no location yet"}), 404

        thread = threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=self.webhook_port, debug=False),
            daemon=True,
        )
        thread.start()
        logger.info(f"📍 Location webhook listening on port {self.webhook_port}")

    def get_current_location(self) -> dict | None:
        """
        Returns {"lat": float, "lon": float} or None if unknown.
        Location is considered stale after 10 minutes.
        """
        if not self._last_location:
            logger.warning("No location data available yet.")
            return None

        if self._last_update:
            age = (datetime.now() - self._last_update).total_seconds()
            if age > 600:  # 10 minutes
                logger.warning(
                    f"Location data is {age/60:.0f} minutes old. "
                    "iPhone might not be sending updates."
                )
                # Still return it - stale location > no location
                # But this is something to improve later

        return self._last_location
