"""
journal/sessions.py - Trading session detection

Sessions (UTC):
  Asian    — 00:00 - 08:00
  London   — 08:00 - 15:00 (overlap with NY 13:00-15:00 tagged as London)
  New York — 15:00 - 21:00
  Off hours — 21:00 - 00:00
"""

from datetime import datetime


def detect_session(utc_time: datetime) -> str:
    hour = utc_time.hour

    if 0 <= hour < 8:
        return "asian"
    elif 8 <= hour < 15:
        return "london"
    elif 15 <= hour < 21:
        return "new_york"
    else:
        return "off_hours"


SESSION_INFO = {
    "asian":     {"label": "Asian",     "hours": "00:00-08:00 UTC"},
    "london":    {"label": "London",    "hours": "08:00-15:00 UTC"},
    "new_york":  {"label": "New York",  "hours": "15:00-21:00 UTC"},
    "off_hours": {"label": "Off hours", "hours": "21:00-00:00 UTC"},
}
