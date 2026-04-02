"""
Health rhythm management for Mittens.
Handles sunrise-based scheduling: meals, bedtime, and sleep tracking.
"""

import os
import logging
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("mittens.health")


class HealthScheduler:
    """Manages sunrise-based health events: meals, bedtime, sleep."""

    def __init__(self, calendar, sleep_hours: int, home_lat: float, home_lon: float):
        self.calendar = calendar
        self.sleep_hours = sleep_hours
        self.home_lat = home_lat
        self.home_lon = home_lon
        self._cached_sunrise = {}  # {date: sunrise_datetime}
        self._meals_scheduled_date = None

        if self.sleep_hours > 0:
            logger.info(
                f"🛏️ Sleep target: {self.sleep_hours}h. "
                f"Bedtime = tomorrow's sunrise - {self.sleep_hours}h."
            )
        else:
            logger.info("SLEEP_HOURS=0. Bedtime alerts disabled.")

    def schedule_meals_if_needed(self):
        """Create meal, bedtime, and sunrise events in Health calendar for 3 days."""
        if self.sleep_hours <= 0:
            return

        today = datetime.now().date()
        if self._meals_scheduled_date == today:
            return

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
            if cal_id != "primary":
                self.calendar.delete_events_by_prefix(
                    "[Mittens]", target_dt, calendar_id="primary"
                )

            # Get sunrise for this date
            sunrise = self.get_sunrise(target_date)
            if sunrise is None:
                continue

            # Calculate bedtime (tomorrow's sunrise - sleep_hours)
            next_sunrise = self.get_sunrise(target_date + timedelta(days=1))
            bedtime = None
            if next_sunrise:
                bedtime = next_sunrise - timedelta(hours=self.sleep_hours)

            # Build events: 12-hour eating window
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

    def get_sunrise(self, for_date) -> datetime | None:
        """Fetch sunrise time from sunrise-sunset.org API. Caches per day."""
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
            user_tz = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
            sunrise_local = sunrise_utc.astimezone(user_tz)
            sunrise_local = sunrise_local.replace(tzinfo=None)

            self._cached_sunrise[for_date] = sunrise_local
            logger.info(
                f"\U0001f305 Sunrise on {for_date}: {sunrise_local.strftime('%I:%M %p')}"
            )
            return sunrise_local

        except Exception as e:
            logger.error(f"Sunrise API failed: {e}")
            return None

    def get_bedtime(self, now: datetime) -> datetime | None:
        """Calculate tonight's bedtime = tomorrow's sunrise - SLEEP_HOURS."""
        tomorrow = (now + timedelta(days=1)).date()
        sunrise = self.get_sunrise(tomorrow)
        if sunrise is None:
            return None
        return sunrise - timedelta(hours=self.sleep_hours)

    def bedtime_needs_check(self, now: datetime) -> bool:
        """Check if we're within 2 hours of bedtime (worth checking travel)."""
        if self.sleep_hours <= 0:
            return False
        bedtime = self.get_bedtime(now)
        if bedtime is None:
            return False
        minutes_until = (bedtime - now).total_seconds() / 60
        return -15 < minutes_until <= 120

    def check_bedtime(self, my_location: dict, now: datetime,
                      travel, alerts, active_alerts: dict):
        """Check if you need to head home for bedtime."""
        if self.sleep_hours <= 0:
            return
        if self.home_lat == 0 and self.home_lon == 0:
            return

        bedtime = self.get_bedtime(now)
        if bedtime is None:
            return
        minutes_until = (bedtime - now).total_seconds() / 60

        if minutes_until < -15 or minutes_until > 120:
            return

        # Send downtime alert 30 min before bedtime (once per night)
        downtime_key = f"downtime_{now.date()}"
        if 25 <= minutes_until <= 35 and downtime_key not in active_alerts:
            bedtime_str = bedtime.strftime("%I:%M %p")
            logger.info(f"🌙 Sending downtime alert — bedtime at {bedtime_str}")
            alerts.send_downtime_alert(bedtime_str)
            active_alerts[downtime_key] = {"sent": True}

        # Already home?
        travel_minutes = travel.get_travel_time(
            origin=my_location,
            destination=f"{self.home_lat},{self.home_lon}",
        )

        if travel_minutes is None or travel_minutes <= 5:
            if travel_minutes is not None and travel_minutes <= 5:
                logger.debug("🛏️ Already home, no bedtime alert needed.")
                if "bedtime" in active_alerts:
                    del active_alerts["bedtime"]
            return

        bedtime_prep = 30
        need_to_leave_in = minutes_until - travel_minutes - bedtime_prep

        logger.info(
            f"🛏️ Bedtime at {bedtime.strftime('%I:%M %p')} ({minutes_until:.0f}min) | "
            f"Home: {travel_minutes:.0f}min away | "
            f"Leave in: {need_to_leave_in:.0f}min"
        )

        if need_to_leave_in <= 0:
            from event_checker import escalate
            escalate(
                "bedtime", "Bedtime (head home!)",
                travel_minutes, minutes_until, "Home",
                alerts, None, active_alerts,
            )
