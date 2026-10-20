"""Failed and accepted-partial chapter records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ._base import StateDomain, snapshot


class FailureStore(StateDomain):
    """Chapters whose download failed, and partial chapters the user
    accepted but that stay retryable."""

    __slots__ = ()

    def add_failed(
        self,
        manga_title: str,
        chapter: str,
        source: str,
        error: str,
        failed_pages: Optional[List[int]] = None,
    ) -> None:
        """Record a chapter download failure."""
        with self._backend._locked() as data:
            self._backend._ensure_manga_entry(manga_title)
            chapter_str = str(chapter)
            entry = data["manga"][manga_title]

            if "failed_chapters" not in entry:
                entry["failed_chapters"] = {}

            existing = entry["failed_chapters"].get(chapter_str, {})
            entry["failed_chapters"][chapter_str] = {
                "failed_at": datetime.now().isoformat(),
                "source": source,
                "error": error[:300],
                "failed_pages": snapshot(failed_pages or []),
                "attempts": existing.get("attempts", 0) + 1,
            }
            self._backend.save()

    def get_failed(self, manga_title: str) -> Dict[str, Any]:
        """Get all failed chapter records for a manga."""
        return self._backend._entry_value(manga_title, "failed_chapters", {})

    def get_all_failed(self) -> Dict[str, Dict[str, Any]]:
        """Get failed chapters for all manga that have failures."""
        with self._backend._locked() as data:
            result = {}
            for title, manga_state in data.get("manga", {}).items():
                failed = manga_state.get("failed_chapters", {})
                if failed:
                    result[title] = snapshot(failed)
            return result

    def clear_failed(self, manga_title: str, chapter: str) -> None:
        """Remove a failed chapter record after successful re-download."""
        with self._backend._locked() as data:
            chapter_str = str(chapter)
            failed = data.get("manga", {}).get(manga_title, {}).get("failed_chapters", {})
            if chapter_str in failed:
                del failed[chapter_str]
                self._backend.save()

    # ── Accepted partial chapters ──────────────────────────────────────

    def add_partial(
        self,
        manga_title: str,
        chapter: str,
        source: str = "",
        failed_pages: Optional[List[int]] = None,
        total_pages: int = 0,
        path: str = "",
        from_backup: bool = False,
    ) -> None:
        """Record an accepted partial chapter so it remains retryable."""
        with self._backend._locked() as data:
            self._backend._ensure_manga_entry(manga_title)
            chapter_str = str(chapter)
            entry = data["manga"][manga_title]
            entry.setdefault("partial_chapters", {})[chapter_str] = {
                "accepted_at": datetime.now().isoformat(),
                "source": source,
                "failed_pages": snapshot(failed_pages or []),
                "total_pages": int(total_pages or 0),
                "path": path,
                "from_backup": bool(from_backup),
            }
            self._backend.save()

    def get_partials(self, manga_title: str) -> Dict[str, Any]:
        """Get accepted partial chapter records for a manga."""
        return self._backend._entry_value(manga_title, "partial_chapters", {})

    def get_partial(self, manga_title: str, chapter: str) -> Optional[Dict[str, Any]]:
        """Get one accepted partial chapter record."""
        partials = self.get_partials(manga_title)
        return partials.get(str(chapter))

    def get_all_partials(self) -> Dict[str, Dict[str, Any]]:
        """Get accepted partial chapters for all manga."""
        with self._backend._locked() as data:
            return {
                title: snapshot(partials)
                for title, manga_state in data.get("manga", {}).items()
                if (partials := manga_state.get("partial_chapters", {}))
            }

    def clear_partial(self, manga_title: str, chapter: str) -> None:
        """Remove an accepted partial record after a complete re-download."""
        with self._backend._locked() as data:
            chapter_str = str(chapter)
            partials = data.get("manga", {}).get(manga_title, {}).get("partial_chapters", {})
            if chapter_str in partials:
                del partials[chapter_str]
                self._backend.save()
