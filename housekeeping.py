"""
Daily housekeeping tasks for Mittens.
Email cleanup, webhook renewal, GPS request throttling.
"""

import os
import imaplib
import logging
from datetime import datetime

logger = logging.getLogger("mittens.housekeeping")


def cleanup_old_emails(config: dict):
    """Delete emails before today from iCloud inbox via IMAP."""
    if os.environ.get("CLEANUP_EMAILS", "").lower() != "true":
        return

    icloud_password = os.environ.get("ICLOUD_APP_PASSWORD", "")
    to_email = config.get("email", {}).get("to_email", "")

    if not icloud_password or not to_email:
        return

    try:
        mail = imaplib.IMAP4_SSL("imap.mail.me.com", 993)
        mail.login(to_email, icloud_password)
        mail.select("INBOX")

        today_str = datetime.now().date().strftime("%d-%b-%Y")
        status, messages = mail.search(None, f"BEFORE {today_str}")

        if status == "OK" and messages[0]:
            msg_ids = messages[0].split()
            for msg_id in msg_ids:
                mail.store(msg_id, "+FLAGS", "\\Deleted")
            mail.expunge()
            logger.info(f"🧹 Cleaned up {len(msg_ids)} old emails from inbox.")
        else:
            logger.info("🧹 No old emails to clean up.")

        mail.logout()
    except Exception as e:
        logger.error(f"Email cleanup failed: {e}")


def renew_watches_if_needed(calendar, last_renewal: datetime | None) -> datetime:
    """Renew webhook watch channels every 20 hours (they expire at 24h).
    Returns the updated last_renewal timestamp.
    """
    now = datetime.now()
    if last_renewal is None:
        return now

    hours_since = (now - last_renewal).total_seconds() / 3600
    if hours_since >= 20:
        calendar.renew_watches()
        return now

    return last_renewal


def request_location_if_needed(now: datetime, last_requested: datetime | None,
                               alerts) -> datetime | None:
    """Send email requesting GPS, max once per 10 minutes.
    Returns the updated last_requested timestamp.
    """
    if last_requested:
        elapsed = (now - last_requested).total_seconds()
        if elapsed < 600:
            return last_requested
    alerts.request_location()
    return now
