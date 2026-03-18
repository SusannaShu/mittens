"""
Alert Manager for Mittens.
Uses Resend (free tier) to send emails that trigger iPhone Automations.

Two email types:
  - Subject "MITTENS_LOCATION" → automation sends GPS to server
  - Subject "MITTENS_ALARM ..." → automation sets iPhone alarm

No ntfy, no Twilio. Just email + Apple Shortcuts automations.
"""

import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger("mittens.alerts")

RESEND_API_URL = "https://api.resend.com/emails"


class AlertManager:
    def __init__(self, config: dict):
        """
        config should have:
          - resend_api_key: Resend API key
          - from_email: sender address (verified in Resend)
          - to_email: your email address
        """
        self.api_key = config.get("resend_api_key", "")
        self.from_email = config.get("from_email", "")
        self.to_email = config.get("to_email", "")

        if not self.api_key or not self.to_email:
            logger.warning("Resend not configured. Mittens can't send alerts.")
        else:
            logger.info(f"Email alerts: {self.from_email} → {self.to_email}")

    def _send_email(self, subject: str, body: str) -> bool:
        """Send an email via Resend API."""
        if not self.api_key:
            logger.warning(f"Can't send email (no API key): {subject}")
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
        iPhone Email Automation triggers on subject containing MITTENS_LOCATION.
        """
        self._send_email(
            subject="MITTENS_LOCATION",
            body=(
                "Mittens needs your current location.\n"
                "This email triggers your iPhone automation to send GPS.\n"
                f"Time: {datetime.now().strftime('%I:%M %p')}"
            ),
        )

    def send_alarm(self, event_summary: str, minutes_until: float,
                   travel_minutes: float, location: str = ""):
        """
        Tell iPhone to set an alarm.
        Subject: MITTENS_ALARM + readable message (for notification)
        Body: plain text message with event details
        """
        subject = (
            f"MITTENS_ALARM {event_summary} in {minutes_until:.0f} min"
            f" — {travel_minutes:.0f} min away"
        )
        body = (
            f"GET UP! {event_summary} in {minutes_until:.0f} min. "
            f"You're {travel_minutes:.0f} min away. "
            f"At: {location}" if location else
            f"GET UP! {event_summary} in {minutes_until:.0f} min. "
            f"You're {travel_minutes:.0f} min away."
        )
        self._send_email(subject, body)

    def send_notification(self, message: str, event_summary: str = "",
                          minutes_until: float = 0, travel_minutes: float = 0):
        """Gentle heads-up (no alarm trigger)."""
        subject = f"MITTENS_REMINDER {event_summary}"
        self._send_email(subject, message)

    def test(self):
        """Send a test email to verify setup."""
        if not self.api_key:
            print("No Resend API key configured.")
            return

        success = self._send_email(
            subject="MITTENS_TEST",
            body="Mittens email test! If you see this, alerts work.",
        )
        if success:
            print(f"Test email sent to {self.to_email}!")
        else:
            print("Test email failed. Check Resend API key.")
