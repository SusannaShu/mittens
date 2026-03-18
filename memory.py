"""
Memory for Mittens.
Tracks your appointment history, alert patterns, and whether you actually showed up.

This is your "second brain" for accountability - over time, Mittens learns
which appointments you tend to miss and can be more aggressive about those.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("mittens.memory")

DB_PATH = Path.home() / ".mittens" / "memory.db"


class MittensMemory:
    def __init__(self):
        self.db_path = DB_PATH
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_id TEXT,
                    event_summary TEXT,
                    minutes_until REAL,
                    travel_minutes REAL,
                    lat REAL,
                    lon REAL
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_summary TEXT,
                    alert_type TEXT,
                    message TEXT
                );

                CREATE TABLE IF NOT EXISTS arrivals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_id TEXT,
                    event_summary TEXT
                );

                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_keyword TEXT UNIQUE,
                    total_scheduled INTEGER DEFAULT 0,
                    total_attended INTEGER DEFAULT 0,
                    total_missed INTEGER DEFAULT 0,
                    avg_alerts_needed REAL DEFAULT 0
                );
            """)
        logger.info(f"Memory initialized at {self.db_path}")

    def log_check(self, event_id: str, event_summary: str,
                  minutes_until: float, travel_minutes: float,
                  location: dict):
        """Log a location check for an upcoming event."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO checks
                   (timestamp, event_id, event_summary, minutes_until, travel_minutes, lat, lon)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(),
                    event_id,
                    event_summary,
                    minutes_until,
                    travel_minutes,
                    location.get("lat"),
                    location.get("lon"),
                ),
            )

    def log_alert(self, event_summary: str, alert_type: str, message: str):
        """Log an alert that was sent."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO alerts (timestamp, event_summary, alert_type, message)
                   VALUES (?, ?, ?, ?)""",
                (datetime.now().isoformat(), event_summary, alert_type, message),
            )

    def log_arrival(self, event_id: str, event_summary: str):
        """Log that you actually arrived at the appointment."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO arrivals (timestamp, event_id, event_summary)
                   VALUES (?, ?, ?)""",
                (datetime.now().isoformat(), event_id, event_summary),
            )
        logger.info(f"✅ Logged arrival for: {event_summary}")

    def get_attendance_stats(self, keyword: str = None) -> dict:
        """
        Get attendance statistics.
        If keyword provided, filter events containing that keyword.
        """
        with sqlite3.connect(self.db_path) as conn:
            if keyword:
                alerts = conn.execute(
                    "SELECT COUNT(DISTINCT event_summary) FROM alerts WHERE event_summary LIKE ?",
                    (f"%{keyword}%",),
                ).fetchone()[0]

                arrivals = conn.execute(
                    "SELECT COUNT(*) FROM arrivals WHERE event_summary LIKE ?",
                    (f"%{keyword}%",),
                ).fetchone()[0]
            else:
                alerts = conn.execute(
                    "SELECT COUNT(DISTINCT event_summary) FROM alerts"
                ).fetchone()[0]

                arrivals = conn.execute(
                    "SELECT COUNT(*) FROM arrivals"
                ).fetchone()[0]

        return {
            "events_alerted": alerts,
            "events_attended": arrivals,
            "attendance_rate": (
                f"{(arrivals / alerts * 100):.0f}%"
                if alerts > 0 else "no data yet"
            ),
        }

    def get_recent_alerts(self, limit: int = 10) -> list[dict]:
        """Get recent alerts for debugging/review."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
