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
        logger.debug("Email cleanup disabled (CLEANUP_EMAILS != true).")
        return

    icloud_password = os.environ.get("ICLOUD_APP_PASSWORD", "")
    to_email = config.get("email", {}).get("to_email", "")

    if not icloud_password:
        logger.warning("Email cleanup skipped: ICLOUD_APP_PASSWORD not set.")
        return
    if not to_email:
        logger.warning("Email cleanup skipped: to_email not found in config.")
        return

    mail = None
    try:
        logger.info(f"Connecting to iCloud IMAP as {to_email}...")
        mail = imaplib.IMAP4_SSL("imap.mail.me.com", 993)
        mail.login(to_email, icloud_password)
        logger.info("IMAP login successful.")

        mail.select("INBOX")

        today_str = datetime.now().date().strftime("%d-%b-%Y")
        logger.info(f"Searching for emails BEFORE {today_str}...")
        status, messages = mail.search(None, f"BEFORE {today_str}")

        if status == "OK" and messages[0]:
            msg_ids = messages[0].split()
            logger.info(f"Found {len(msg_ids)} old emails to delete.")
            for msg_id in msg_ids:
                mail.store(msg_id, "+FLAGS", "\\Deleted")
            mail.expunge()
            logger.info(f"Cleaned up {len(msg_ids)} old emails from inbox.")
        else:
            logger.info("No old emails to clean up.")

        mail.logout()
    except imaplib.IMAP4.error as e:
        logger.error(
            f"IMAP auth/command failed: {e} "
            f"-- check if app-specific password is still valid at "
            f"appleid.apple.com > Sign-In and Security > App-Specific Passwords"
        )
    except Exception as e:
        logger.error(f"Email cleanup failed: {type(e).__name__}: {e}")
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


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
