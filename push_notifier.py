"""
Expo Push Notification sender for Mittens.
Sends instant push notifications to the Mittens app via Expo's Push API.
Replaces email-based alarms for time-sensitive alerts.

Expo Push API docs: https://docs.expo.dev/push-notifications/sending-notifications/
"""

import logging
import requests

logger = logging.getLogger("mittens.push")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


class ExpoPushNotifier:
    def __init__(self):
        self.push_tokens = []  # List of Expo push tokens (typically just one)

    def register_token(self, token: str):
        """Store a push token from the mobile app."""
        if token and token not in self.push_tokens:
            self.push_tokens.append(token)
            logger.info(f"Registered push token: {token[:20]}...")

    def has_tokens(self) -> bool:
        return bool(self.push_tokens)

    def send(self, title: str, body: str, data: dict = None,
             sound: str = "default", priority: str = "high",
             category: str = None) -> bool:
        """
        Send a push notification to all registered devices.
        Returns True if at least one notification was sent successfully.

        Args:
            title: Notification title (e.g. "Time to leave!")
            body: Notification body message
            data: Optional JSON data payload
            sound: Sound to play ("default" or custom)
            priority: "default", "normal", or "high"
            category: Optional category for actionable notifications
        """
        if not self.push_tokens:
            logger.warning("No push tokens registered. Can't send notification.")
            return False

        messages = []
        for token in self.push_tokens:
            msg = {
                "to": token,
                "title": title,
                "body": body,
                "sound": sound,
                "priority": priority,
            }
            if data:
                msg["data"] = data
            if category:
                msg["categoryId"] = category
            messages.append(msg)

        try:
            resp = requests.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=10,
            )

            if resp.status_code == 200:
                result = resp.json()
                errors = [
                    t for t in result.get("data", [])
                    if t.get("status") == "error"
                ]
                if errors:
                    logger.error(f"Push errors: {errors}")
                    return False
                logger.info(f"Push sent: {title}")
                return True
            else:
                logger.error(f"Expo Push API error {resp.status_code}: {resp.text}")
                return False

        except Exception as e:
            logger.error(f"Push notification failed: {e}")
            return False

    def send_alarm(self, event_summary: str, minutes_until: float,
                   travel_minutes: float, location: str = ""):
        """Send urgent alarm notification."""
        title = f"GO NOW -- {event_summary}"
        body = (
            f"{event_summary} in {minutes_until:.0f} min. "
            f"You're {travel_minutes:.0f} min away."
        )
        if location:
            body += f" At: {location}"

        return self.send(
            title=title,
            body=body,
            data={
                "type": "alarm",
                "event": event_summary,
                "minutesUntil": round(minutes_until),
                "travelMinutes": round(travel_minutes),
            },
            priority="high",
            category="alarm",
        )

    def send_reminder(self, event_summary: str, message: str):
        """Send a gentle reminder notification."""
        return self.send(
            title=event_summary,
            body=message,
            data={"type": "reminder", "event": event_summary},
            priority="default",
        )

    def send_zoom_reminder(self, event_summary: str, minutes_until: float,
                           zoom_link: str = ""):
        """Send a Zoom/virtual meeting reminder."""
        title = f"Meeting in {minutes_until:.0f} min"
        body = f"{event_summary} is starting soon. Open Zoom!"
        data = {"type": "zoom", "event": event_summary}
        if zoom_link:
            data["link"] = zoom_link

        return self.send(
            title=title,
            body=body,
            data=data,
            priority="high",
            category="meeting",
        )

    def send_downtime(self, bedtime_str: str):
        """Send bedtime reminder."""
        return self.send(
            title=f"Bedtime at {bedtime_str}",
            body="Wind down. Put devices away and get ready for sleep.",
            data={"type": "downtime", "bedtime": bedtime_str},
            priority="default",
        )
