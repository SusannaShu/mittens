"""
MittensMonitor — the background brain of Mittens.
Runs in a background thread: adaptive scheduling, event checking,
health management, and daily housekeeping.
"""

import os
import time
import logging
from datetime import datetime

from calendar_client import GoogleCalendarClient
from travel import TravelTimeEstimator
from alerts import AlertManager
from memory import MittensMemory
from health import HealthScheduler
from event_checker import check_event, check_virtual_only_event
from housekeeping import (
    cleanup_old_emails, renew_watches_if_needed, request_location_if_needed,
)

logger = logging.getLogger("mittens")


class MittensMonitor:
    """
    Background monitor. Each loop iteration:
    1. Daily tasks (morning fetch, meal scheduling, watch renewal, email cleanup)
    2. Check cached events against live GPS → fire alarms if needed
    3. Adaptive sleep until next check (or webhook wakes us)
    """

    def __init__(self, config: dict, shared_state: dict):
        self.config = config
        self.buffer = config.get("buffer_minutes", 5)
        self.poll_interval = config.get("poll_interval", 60)
        self.calendar = None
        self.travel = TravelTimeEstimator(config.get("maps_api_key") or None)
        self.alerts = AlertManager(config["email"], shared_state.get("push_notifier"))
        self.memory = MittensMemory()

        # Shared state (from mittens.py Flask app)
        self._state = shared_state  # current_location, active_alerts, monitor_wake

        # Housekeeping tracking
        self._location_requested_at = None
        self._last_watch_renewal = None
        self._emails_cleaned_date = None
        self._morning_fetch_date = None

        # Initialize Google Calendar
        try:
            self.calendar = GoogleCalendarClient(config["google"])
            shared_state["calendar"] = self.calendar
            logger.info("Google Calendar connected (events cached, webhooks active).")
        except Exception as e:
            logger.error(f"Google Calendar init failed: {e}")
            logger.error("Calendar monitoring disabled. Fix credentials and restart.")

        # Health scheduler (sunrise, meals, bedtime)
        home_lat = float(os.environ.get("HOME_LAT", "0"))
        home_lon = float(os.environ.get("HOME_LON", "0"))
        sleep_hours = config.get("sleep_hours", 0)
        self.health = HealthScheduler(
            self.calendar, sleep_hours, home_lat, home_lon,
        )

        # Mark today as fetched (initial fetch happens in calendar __init__)
        if self.calendar:
            self._morning_fetch_date = datetime.now().date()

    def run(self):
        """
        Main monitoring loop - runs forever in background thread.
        Events are served from cache (populated at sunrise + on webhook).
        Adaptive sleep: checks more frequently as events approach.
        Webhooks wake the loop immediately for instant reaction.
        """
        logger.info(
            f"Monitor started. Adaptive intervals, "
            f"buffer: {self.buffer}min. "
            f"Calendar: sunrise fetch + webhooks."
        )

        # Run email cleanup immediately on startup/deploy
        self._cleanup_old_emails_if_needed()

        wake_event = self._state["monitor_wake"]

        while True:
            try:
                if self.calendar:
                    self._morning_fetch_if_needed()
                    self.health.schedule_meals_if_needed()
                    self._last_watch_renewal = renew_watches_if_needed(
                        self.calendar, self._last_watch_renewal
                    )
                    self._tick()
            except Exception as e:
                logger.error(f"Monitor error: {e}", exc_info=True)

            # Email cleanup runs independently of calendar
            self._cleanup_old_emails_if_needed()

            next_check = self._calculate_next_check()
            wake_event.wait(timeout=next_check)
            if wake_event.is_set():
                logger.info("Woken by webhook -- processing immediately.")
                wake_event.clear()

    def _morning_fetch_if_needed(self):
        """At sunrise each day, pull the full day's calendar from Google."""
        today = datetime.now().date()
        if self._morning_fetch_date == today:
            return

        sunrise = self.health.get_sunrise(today)
        now = datetime.now()
        if sunrise is not None and now < sunrise:
            return

        self.calendar.do_morning_fetch()
        self._morning_fetch_date = today

    def _cleanup_old_emails_if_needed(self):
        """Run email cleanup once daily."""
        today = datetime.now().date()
        if self._emails_cleaned_date == today:
            return
        cleanup_old_emails(self.config)
        self._emails_cleaned_date = today

    def _calculate_next_check(self) -> float:
        """Schedule next check based on when each event needs attention.

        Physical events (with location):
          >2h away:  sleep until 2h before
          2h-1h:     every 5 min
          1h-30min:  every 2 min
          30-15min:  every 1 min
          <15min:    every 30s
        Virtual events (Zoom/Meet): wake ~8 min before for reminder.
        Between events: sleep long, webhooks handle instant changes.
        Max sleep capped at 30 min for reliability.
        """
        if not self.calendar:
            return self.poll_interval

        events = self.calendar.get_upcoming_events(hours_ahead=18)
        if not events:
            return 3600  # nothing upcoming, check every hour

        now = datetime.now()
        soonest_check = float('inf')
        wake_summary = ""
        wake_event_min = 0

        for event in events:
            start = event["start_time"]
            if start.tzinfo is not None:
                minutes_until = (start - now.astimezone()).total_seconds() / 60
            else:
                minutes_until = (start - now).total_seconds() / 60

            if minutes_until < -15:
                continue

            summary = event.get("summary", "")
            location = event.get("location", "") or ""
            is_physical = (
                bool(location)
                and not TravelTimeEstimator.is_virtual_location(location)
            )

            if is_physical:
                if minutes_until <= 15:
                    check_in = 0.5       # every 30s
                elif minutes_until <= 30:
                    check_in = 1         # every 1 min
                elif minutes_until <= 60:
                    check_in = 2         # every 2 min
                elif minutes_until <= 120:
                    check_in = 5         # every 5 min
                else:
                    # Sleep until 2h before the event
                    check_in = minutes_until - 120
            else:
                if minutes_until <= 8:
                    check_in = 0.5
                else:
                    check_in = minutes_until - 8

            if check_in < soonest_check:
                soonest_check = check_in
                wake_summary = summary
                wake_event_min = minutes_until

        if soonest_check == float('inf'):
            return 3600  # no actionable events, check every hour

        # Convert minutes to seconds, cap at 4h, floor at 30s
        # Webhooks handle real-time changes; this is just a safety net
        interval = max(30, min(soonest_check * 60, 14400))

        logger.info(
            f"'{wake_summary}' in {wake_event_min:.0f}min "
            f"-- next check in {interval:.0f}s"
        )
        return interval

    def _tick(self):
        """Single check cycle: read cache, calc travel, fire alarms."""
        now = datetime.now()
        loc = self._state["current_location"]
        active_alerts = self._state["active_alerts"]

        events = self.calendar.get_upcoming_events(hours_ahead=2)
        location_events = [e for e in events if e.get("location")]
        virtual_only_events = [
            e for e in events
            if not e.get("location")
            and TravelTimeEstimator.is_virtual_location(e.get("description", ""))
        ]

        # Virtual events don't need GPS
        for event in virtual_only_events:
            check_virtual_only_event(event, now, self.alerts, active_alerts)

        # Check if we need GPS (physical events or bedtime)
        needs_gps = bool(location_events) or self.health.bedtime_needs_check(now)
        if not needs_gps:
            return

        # Determine location: fresh GPS > request GPS > home fallback
        my_loc = None
        if loc["lat"] is not None:
            my_loc = {"lat": loc["lat"], "lon": loc["lon"]}
            if loc["updated"]:
                age = (now - loc["updated"]).total_seconds()
                if age > 1800:
                    logger.info(f"GPS is {age/60:.0f}min old, requesting fresh location.")
                    self._location_requested_at = request_location_if_needed(
                        now, self._location_requested_at, self.alerts
                    )
        else:
            self._location_requested_at = request_location_if_needed(
                now, self._location_requested_at, self.alerts
            )

            # Wait up to 45s for iPhone to send GPS back
            for _ in range(9):
                time.sleep(5)
                if loc["lat"] is not None:
                    logger.info("GPS received from iPhone!")
                    my_loc = {"lat": loc["lat"], "lon": loc["lon"]}
                    break
            else:
                if self.health.home_lat == 0 and self.health.home_lon == 0:
                    logger.warning("No GPS and no HOME_LAT/HOME_LON set. Skipping.")
                    return
                logger.info("No GPS after waiting, using home location.")
                my_loc = {"lat": self.health.home_lat, "lon": self.health.home_lon}

        if my_loc is None:
            return

        for event in location_events:
            check_event(
                event, my_loc, now,
                self.travel, self.alerts, self.memory,
                active_alerts, self.buffer,
            )

        # Bedtime: do you need to head home?
        self.health.check_bedtime(
            my_loc, now, self.travel, self.alerts, active_alerts
        )
