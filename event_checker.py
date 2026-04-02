"""
Event checking, travel time decisions, and alarm escalation for Mittens.
Handles both physical events (location) and virtual meetings (Zoom/Meet).
"""

import logging
from datetime import datetime

from travel import TravelTimeEstimator

logger = logging.getLogger("mittens.events")

# Escalation chain: action, delay in minutes since first alert
ESCALATION = [
    ("alarm", 0),          # immediately: ALARM (triggers iPhone timer)
    ("alarm", 5),          # 5 min later: alarm again
    ("alarm", 10),         # 10 min later: one more
]


def check_event(event: dict, my_location: dict, now: datetime,
                travel: TravelTimeEstimator, alerts, memory,
                active_alerts: dict, buffer: int):
    """Check a physical event and fire alarms if it's time to leave."""
    event_id = event["id"]
    event_start = event["start_time"]
    event_location = event["location"]
    event_summary = event.get("summary", "Appointment")

    # Make both datetimes naive for comparison (or both aware)
    if event_start.tzinfo is not None:
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
    travel_minutes = travel.get_travel_time(
        origin=my_location,
        destination=event_location,
    )

    if travel_minutes is None:
        if TravelTimeEstimator.is_virtual_location(event_location) or \
           TravelTimeEstimator.is_virtual_location(event.get("description", "")):
            handle_virtual_meeting(
                event_id, event_summary, minutes_until,
                event_location, event.get("description", ""),
                alerts, active_alerts,
            )
        else:
            logger.warning(f"Could not calc travel to '{event_summary}'")
        return

    need_to_leave_in = minutes_until - travel_minutes - buffer

    logger.info(
        f"{event_summary} in {minutes_until:.0f}min | "
        f"Travel: {travel_minutes:.0f}min | "
        f"Leave in: {need_to_leave_in:.0f}min"
    )

    # Log to memory
    memory.log_check(
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
            memory.log_arrival(event_id, event_summary)
            del active_alerts[event_id]
        return

    # Should you have left already?
    if need_to_leave_in <= 0:
        escalate(event_id, event_summary, travel_minutes, minutes_until,
                 event_location, alerts, memory, active_alerts)


def check_virtual_only_event(event: dict, now: datetime,
                             alerts, active_alerts: dict):
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

    handle_virtual_meeting(
        event_id, event_summary, minutes_until,
        event.get("location", ""), event.get("description", ""),
        alerts, active_alerts,
    )


def handle_virtual_meeting(event_id: str, event_summary: str,
                           minutes_until: float, location: str,
                           description: str, alerts, active_alerts: dict):
    """Send a MITTENS_ZOOM email ~5 min before a virtual meeting (once per event)."""
    # Only send when we're in the 3-7 min window
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
    alerts.send_zoom_reminder(event_summary, minutes_until, zoom_link)

    # Mark as reminded so we don't spam
    if event_id not in active_alerts:
        active_alerts[event_id] = {"level": -1, "first_alert_time": datetime.now()}
    active_alerts[event_id]["zoom_reminded"] = True


def escalate(event_id: str, summary: str, travel_min: float,
             minutes_until: float, location: str,
             alerts, memory, active_alerts: dict):
    """Fire escalating alarms when it's time to leave."""
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
    for i, (action, delay) in enumerate(ESCALATION):
        if i > state["level"] and minutes_since_first >= delay:
            state["level"] = i
            message = (
                f"{summary} is in {minutes_until:.0f} minutes "
                f"and you're {travel_min:.0f} minutes away. "
                f"Get up and go!"
            )

            if action == "notification":
                alerts.send_notification(
                    message, summary, minutes_until, travel_min
                )
            else:
                alerts.send_alarm(
                    summary, minutes_until, travel_min,
                    location=location
                )

            memory.log_alert(summary, action, message)
            logger.info(f"Escalation fired: level={i}, action={action}")
            break
    else:
        logger.info("No escalation to fire (max level reached)")
