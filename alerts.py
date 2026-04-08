"""
Alert Manager for Mittens.
Primary: Expo Push Notifications (instant, via Mittens app).
Fallback: Resend email (triggers Apple Shortcuts Automations).

Email types (fallback only):
  - Subject "MITTENS_ALARM ..."  -> automation sets iPhone alarm
  - Subject "MITTENS_ZOOM ..."   -> automation checks if Zoom is open
  - Subject "MITTENS_DOWNTIME"   -> automation activates Sleep Focus
  - Subject "MITTENS_LOCATION"   -> automation sends GPS to server
"""

import logging
import requests
from datetime import datetime

logger = logging.getLogger("mittens.alerts")

RESEND_API_URL = "https://api.resend.com/emails"


class AlertManager:
    def __init__(self, config: dict, push_notifier=None):
        """
        config should have:
          - resend_api_key: Resend API key
          - from_email: sender address (verified in Resend)
          - to_email: your email address
        push_notifier: ExpoPushNotifier instance (optional)
        """
        self.api_key = config.get("resend_api_key", "")
        self.from_email = config.get("from_email", "")
        self.to_email = config.get("to_email", "")
        self.push = push_notifier

        if self.push:
            logger.info("Push notifications enabled (primary)")
        if not self.api_key or not self.to_email:
            logger.warning("Resend not configured. Email fallback disabled.")
        else:
            logger.info(f"Email fallback: {self.from_email} -> {self.to_email}")

    def _send_email(self, subject: str, body: str) -> bool:
        """Send an email via Resend API (fallback)."""
        if not self.api_key:
            return False

        try:
            resp = requests.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self.from_email,
                    "to": [self.to_email],
                    "subject": subject,
                    "text": body,
                    "html": f"<p>{body}</p>",
                },
                timeout=10,
            )

            if resp.status_code == 200:
                logger.info(f"Email sent: {subject}")
                return True
            else:
                logger.error(f"Resend error {resp.status_code}: {resp.text}")
                return False

        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False

    def request_location(self):
        """
        Ask iPhone to send GPS.
        Push: not needed (app sends location directly).
        Email fallback: triggers iPhone automation.
        """
        # Push notification can request location from the app
        if self.push and self.push.has_tokens():
            self.push.send(
                title="Location needed",
                body="Mittens needs your current location.",
                data={"type": "location_request"},
                priority="high",
            )
        else:
            self._send_email(
                subject="MITTENS_LOCATION",
                body=(
                    "Mittens needs your current location.\n"
                    f"Time: {datetime.now().strftime('%I:%M %p')}"
                ),
            )

    def send_alarm(self, event_summary: str, minutes_until: float,
                   travel_minutes: float, location: str = ""):
        """
        Urgent alarm: you need to leave NOW.
        Push first, email fallback.
        """
        pushed = False
        if self.push and self.push.has_tokens():
            pushed = self.push.send_alarm(
                event_summary, minutes_until, travel_minutes, location
            )

        if not pushed:
            subject = (
                f"MITTENS_ALARM {event_summary} in {minutes_until:.0f} min"
                f" -- {travel_minutes:.0f} min away"
            )
            body = (
                f"GET UP! {event_summary} in {minutes_until:.0f} min. "
                f"You're {travel_minutes:.0f} min away."
            )
            if location:
                body += f" At: {location}"
            self._send_email(subject, body)

    def send_notification(self, message: str, event_summary: str = "",
                          minutes_until: float = 0, travel_minutes: float = 0):
        """Gentle heads-up (no alarm trigger)."""
        pushed = False
        if self.push and self.push.has_tokens():
            pushed = self.push.send_reminder(event_summary or "Mittens", message)

        if not pushed:
            subject = f"MITTENS_REMINDER {event_summary}"
            self._send_email(subject, message)

    def send_zoom_reminder(self, event_summary: str, minutes_until: float,
                           zoom_link: str = ""):
        """Virtual meeting reminder."""
        pushed = False
        if self.push and self.push.has_tokens():
            pushed = self.push.send_zoom_reminder(
                event_summary, minutes_until, zoom_link
            )

        if not pushed:
            subject = f"MITTENS_ZOOM {event_summary} in {minutes_until:.0f} min"
            body = f"{event_summary} starts in {minutes_until:.0f} minutes."
            if zoom_link:
                body += f"\nLink: {zoom_link}"
            self._send_email(subject, body)

    def send_downtime_alert(self, bedtime_str: str):
        """Bedtime/downtime alert."""
        pushed = False
        if self.push and self.push.has_tokens():
            pushed = self.push.send_downtime(bedtime_str)

        if not pushed:
            subject = f"MITTENS_DOWNTIME Bedtime at {bedtime_str}"
            body = (
                f"Bedtime is at {bedtime_str} tonight.\n"
                f"Wind down! Put devices away and get ready for sleep."
            )
            self._send_email(subject, body)

    def test(self):
        """Send a test alert to verify setup."""
        if self.push and self.push.has_tokens():
            success = self.push.send(
                title="Mittens Test",
                body="Push notifications work!",
                data={"type": "test"},
            )
            if success:
                print("Test push notification sent!")
                return

        if not self.api_key:
            print("No push tokens and no Resend API key configured.")
            return

        success = self._send_email(
            subject="MITTENS_TEST",
            body="Mittens email test! If you see this, alerts work.",
        )
        if success:
            print(f"Test email sent to {self.to_email}!")
        else:
            print("Test email failed. Check Resend API key.")
