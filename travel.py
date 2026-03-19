"""
Travel Time Estimator for Mittens.
Calculates travel time from current location to appointment location.
Supports: bicycling, driving, walking, transit.

Uses Google Maps Directions API (or falls back to straight-line estimate).
"""

import os
import logging
import math
import requests
from functools import lru_cache

logger = logging.getLogger("mittens.travel")

# Average speeds (mph) and detour factors by mode
# These are used for the straight-line distance fallback
MODE_DEFAULTS = {
    "bicycling": {"speed_mph": 10, "detour": 1.3, "prep_min": 3, "label": "🚲"},
    "driving":   {"speed_mph": 20, "detour": 1.4, "prep_min": 5, "label": "🚗"},
    "walking":   {"speed_mph": 3,  "detour": 1.2, "prep_min": 1, "label": "🚶"},
    "transit":   {"speed_mph": 15, "detour": 1.5, "prep_min": 5, "label": "🚇"},
}

# Keywords/patterns that indicate a virtual meeting (no travel needed)
VIRTUAL_LOCATION_KEYWORDS = [
    "zoom.us", "zoom.com", "zoom",
    "meet.google.com", "google meet",
    "teams.microsoft.com", "microsoft teams", "ms teams",
    "webex", "gotomeeting", "whereby", "discord",
    "skype", "bluejeans", "facetime",
]


class TravelTimeEstimator:
    def __init__(self, maps_api_key: str = None, travel_mode: str = None):
        """
        maps_api_key: Google Maps API key with Directions API enabled.
                      If None, falls back to straight-line distance estimate.
        travel_mode: one of 'bicycling', 'driving', 'walking', 'transit'.
                     Defaults to TRAVEL_MODE env var, or 'bicycling'.
        """
        self.api_key = maps_api_key
        self.mode = (travel_mode or os.environ.get("TRAVEL_MODE", "bicycling")).lower()
        if self.mode not in MODE_DEFAULTS:
            logger.warning(f"Unknown travel mode '{self.mode}', falling back to bicycling")
            self.mode = "bicycling"

        mode_info = MODE_DEFAULTS[self.mode]
        logger.info(f"{mode_info['label']} Travel mode: {self.mode} (~{mode_info['speed_mph']} mph)")

        if not self.api_key:
            logger.warning(
                "No Maps API key provided. Using straight-line distance estimates. "
                "Get one at https://console.cloud.google.com/apis/library/directions-backend.googleapis.com"
            )

    @staticmethod
    def is_virtual_location(location: str) -> bool:
        """Check if a location string is a virtual meeting (Zoom, Meet, etc.)."""
        loc_lower = location.strip().lower()
        return any(keyword in loc_lower for keyword in VIRTUAL_LOCATION_KEYWORDS)

    def get_travel_time(self, origin: dict, destination: str) -> float | None:
        """
        Calculate travel time in minutes.

        Args:
            origin: {"lat": float, "lon": float} - your current position
            destination: address string from calendar event

        Returns:
            Travel time in minutes, or None for virtual locations / failures.
        """
        if self.is_virtual_location(destination):
            logger.info(f"💻 Virtual meeting detected ('{destination}'), skipping travel time.")
            return None

        if self.api_key:
            return self._google_maps_travel_time(origin, destination)
        else:
            return self._estimate_travel_time(origin, destination)

    def _google_maps_travel_time(self, origin: dict, destination: str) -> float | None:
        """Use Google Maps Directions API for accurate travel time."""
        try:
            url = "https://maps.googleapis.com/maps/api/directions/json"
            params = {
                "origin": f"{origin['lat']},{origin['lon']}",
                "destination": destination,
                "mode": self.mode,
                "departure_time": "now" if self.mode == "driving" else None,
                "key": self.api_key,
            }

            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()

            if data["status"] != "OK":
                logger.error(f"Maps API error: {data['status']}")
                return None

            # Get duration_in_traffic if available, else regular duration
            leg = data["routes"][0]["legs"][0]
            duration = leg.get("duration_in_traffic", leg["duration"])
            minutes = duration["value"] / 60

            logger.info(
                f"🗺️  Travel time to '{destination}': {minutes:.0f} min "
                f"({leg['distance']['text']})"
            )
            return minutes

        except Exception as e:
            logger.error(f"Maps API request failed: {e}")
            return self._estimate_travel_time(origin, destination)

    def _estimate_travel_time(self, origin: dict, destination: str) -> float | None:
        """
        Fallback: geocode the destination and estimate based on straight-line distance.
        Uses mode-specific speed, detour factor, and prep time.
        """
        dest_coords = self._geocode(destination)
        if not dest_coords:
            logger.warning(
                f"Can't geocode '{destination}', using default 30 min travel time."
            )
            return 30.0

        distance_miles = self._haversine(
            origin["lat"], origin["lon"],
            dest_coords["lat"], dest_coords["lon"],
        )

        mode_info = MODE_DEFAULTS[self.mode]
        speed = mode_info["speed_mph"]
        detour = mode_info["detour"]
        prep = mode_info["prep_min"]
        label = mode_info["label"]

        # Estimate: straight-line * detour / speed * 60 min/hr + prep time
        estimated_minutes = (distance_miles * detour / speed) * 60 + prep

        # Minimum = prep time (still gotta get ready)
        estimated_minutes = max(estimated_minutes, prep + 1)

        logger.info(
            f"{label} Estimated {self.mode} time to '{destination}': "
            f"{estimated_minutes:.0f} min (~{distance_miles:.1f} mi straight-line)"
        )
        return estimated_minutes

    def _geocode(self, address: str) -> dict | None:
        """Convert an address string to lat/lon coordinates."""
        if self.api_key:
            return self._google_geocode(address)
        else:
            return self._nominatim_geocode(address)

    def _google_geocode(self, address: str) -> dict | None:
        """Geocode using Google Maps."""
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            resp = requests.get(
                url,
                params={"address": address, "key": self.api_key},
                timeout=10,
            )
            data = resp.json()
            if data["status"] == "OK":
                loc = data["results"][0]["geometry"]["location"]
                return {"lat": loc["lat"], "lon": loc["lng"]}
        except Exception as e:
            logger.error(f"Geocoding failed: {e}")
        return None

    def _nominatim_geocode(self, address: str) -> dict | None:
        """Free geocoding via OpenStreetMap Nominatim (no API key needed)."""
        try:
            url = "https://nominatim.openstreetmap.org/search"
            resp = requests.get(
                url,
                params={"q": address, "format": "json", "limit": 1},
                headers={"User-Agent": "Mittens/1.0 (calendar assistant)"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error(f"Nominatim HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            results = resp.json()
            if results:
                return {
                    "lat": float(results[0]["lat"]),
                    "lon": float(results[0]["lon"]),
                }
            logger.warning(f"Nominatim found no results for: {address}")
        except Exception as e:
            logger.error(f"Nominatim geocoding failed: {e}")
        return None

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in miles."""
        R = 3959  # Earth radius in miles

        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))

        return R * c
