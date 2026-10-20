"""Notification log shown in the GUI's notification panel."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from ._base import StateDomain, snapshot

# Chip buckets of the notification panel, by stored notification ``type``.
_BUCKETS = {
    "new":       {"check"},
    "downloads": {"download", "kindle"},
    "system":    {"error", "warn", "system"},
}


class NotificationStore(StateDomain):
    """The capped notification log (newest kept, oldest dropped)."""

    __slots__ = ()

    MAX_NOTIFICATIONS = 100

    def add(self, ntype: str, message: str) -> None:
        """Add a notification to the log."""
        with self._backend._locked() as data:
            if "notifications" not in data:
                data["notifications"] = []
            data["notifications"].append({
                "type": ntype,
                "message": message,
                "timestamp": datetime.now().isoformat(),
                "read": False,
            })
            data["notifications"] = data["notifications"][-self.MAX_NOTIFICATIONS:]
            self._backend._mark_dirty()

    def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent notifications, newest first. Items are copies —
        mark_all_read mutates the stored dicts in place, so live references
        would race."""
        with self._backend._locked() as data:
            return snapshot(
                list(reversed(data.get("notifications", [])[-limit:])))

    def get_unread_count(self) -> int:
        """Count unread notifications."""
        with self._backend._locked() as data:
            return sum(1 for n in data.get("notifications", []) if not n.get("read"))

    def mark_all_read(self) -> None:
        """Mark all notifications as read."""
        with self._backend._locked() as data:
            for n in data.get("notifications", []):
                n["read"] = True
            self._backend._mark_dirty()

    def clear(self) -> None:
        """Wipe all notifications. Used by the GUI's 'Clear all' button."""
        with self._backend._locked() as data:
            data["notifications"] = []
            self._backend._mark_dirty()

    def filter(self, kind: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Return notifications filtered by category.

        Categories collapse the granular `type` values into the four chip
        buckets used in the UI:
            all       — everything
            new       — new-chapter findings ("check")
            downloads — download-complete / kindle-sent events
            system    — errors, warnings, generic system events
        """
        all_notifs = self.get_recent(limit)
        if kind == "all":
            return all_notifs
        targets = _BUCKETS.get(kind, set())
        return [n for n in all_notifs if n.get("type") in targets]
