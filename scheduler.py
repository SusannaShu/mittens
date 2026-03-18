"""
Scheduler for Mittens.
Handles timing and could be extended with cron-like features later.
For now, the main loop in mittens.py handles scheduling directly.
This module is a placeholder for future enhancements like:
  - Quiet hours (don't call at 3am)
  - Different poll rates based on time of day
  - Smart scheduling (poll more frequently when events are close)
"""

import logging
from datetime import datetime

logger = logging.getLogger("mittens.scheduler")


class MittensScheduler:
    """Future: smarter scheduling logic."""

    def __init__(self, config: dict = None):
        self.quiet_hours = config.get("quiet_hours", {"start": 23, "end": 7}) if config else {"start": 23, "end": 7}

    def is_quiet_hours(self) -> bool:
        """Check if we're in quiet hours (no calls)."""
        hour = datetime.now().hour
        start = self.quiet_hours["start"]
        end = self.quiet_hours["end"]

        if start > end:  # e.g., 23:00 to 07:00
            return hour >= start or hour < end
        else:
            return start <= hour < end

    def get_poll_interval(self, minutes_to_next_event: float = None) -> int:
        """
        Adaptive polling: check more frequently when events are close.
        """
        if minutes_to_next_event is None:
            return 120  # 2 minutes when nothing's coming up

        if minutes_to_next_event <= 15:
            return 30   # every 30 seconds when event is imminent
        elif minutes_to_next_event <= 30:
            return 60   # every minute
        elif minutes_to_next_event <= 60:
            return 120  # every 2 minutes
        else:
            return 300  # every 5 minutes
