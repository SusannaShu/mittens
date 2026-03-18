"""
Alert Manager for Mittens.
Uses ntfy.sh (free, open source) to send push notifications to your iPhone.
Your iPhone Automation then sets an alarm when it receives the notification.

No Twilio, no monthly cost. Just a free push service + Apple Shortcuts.
"""

import logging
import requests

logger = logging.getLogger("mittens.alerts")

PRIORITY_LOW = 2
PRIORITY_DEFAULT = 3
PRIORITY_HIGH = 4
PRIORITY_URGENT = 5


class AlertManager:
    def __init__(self, config: dict):
        """
        config should have:
          - ntfy_topic: your unique ntfy topic name (e.g., "mittens-yourname-abc123")
                        Pick something unique and unguessable.
          - ntfy_server: optional, defaults to "https://ntfy.sh"
        """
        self.ntfy_topic = config.get("ntfy_topic")
        self.ntfy_server = config.get("ntfy_server", "https://ntfy.sh")
        self.alert_url = f"{self.ntfy_server}/{self.ntfy_topic}" if self.ntfy_topic else None

        if not self.ntfy_topic:
            logger.warning(
                "No ntfy_topic configured. Mittens can't send alerts. "
                "Pick a unique topic name and add it to config."
            )
        else:
            logger.info(f"ntfy configured: {self.alert_url}")

    def send_alert(self, message: str, event_summary: str, minutes_until: float,
                   travel_minutes: float, level: str = "notification"):
        """
        Send an alert via ntfy.

        Tags in the body help iPhone Automation distinguish alert types:
          - MITTENS_ALARM  -> triggers the "set alarm" Shortcut
          - MITTENS_REMINDER -> gentler, just a notification
        """
        if not self.alert_url:
            logger.warning(f"Can't alert (no ntfy topic): {message}")
            return

        if level in ("alarm", "urgent"):
            tag = "MITTENS_ALARM"
            priority = PRIORITY_URGENT
            title = f"GET UP - {event_summary}"
        else:
            tag = "MITTENS_REMINDER"
            priority = PRIORITY_HIGH
            title = f"Heads up - {event_summary}"

        body = (
            f"{message}\n\n"
            f"[{tag}]\n"
            f"Event: {event_summary}\n"
            f"In: {minutes_until:.0f} min\n"
            f"Travel: {travel_minutes:.0f} min"
        )

        try:
            resp = requests.post(
                self.alert_url,
                data=body.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": str(priority),
                    "Tags": "cat,alarm_clock" if level != "notification" else "cat,calendar",
                },
                timeout=10,
            )

            if resp.status_code == 200:
                logger.info(f"ntfy alert sent [{level}]: {title}")
            else:
                logger.error(f"ntfy returned {resp.status_code}: {resp.text}")

        except Exception as e:
            logger.error(f"ntfy request failed: {e}")

    def send_notification(self, message: str, event_summary: str = "Appointment",
                          minutes_until: float = 0, travel_minutes: float = 0):
        """Gentle reminder notification."""
        self.send_alert(message, event_summary, minutes_until, travel_minutes, level="notification")

    def send_alarm(self, message: str, event_summary: str = "Appointment",
                   minutes_until: float = 0, travel_minutes: float = 0):
        """Triggers iPhone Automation to set an actual alarm."""
        self.send_alert(message, event_summary, minutes_until, travel_minutes, level="alarm")

    def test(self):
        """Send a test notification to verify setup."""
        if not self.alert_url:
            print("No ntfy topic configured.")
            return

        try:
            resp = requests.post(
                self.alert_url,
                data="Mittens test! If you see this, notifications work. [MITTENS_ALARM]".encode("utf-8"),
                headers={
                    "Title": "Mittens Test",
                    "Priority": str(PRIORITY_HIGH),
                    "Tags": "cat,white_check_mark",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                print("Test notification sent! Check your phone.")
            else:
                print(f"ntfy returned {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Failed: {e}")
