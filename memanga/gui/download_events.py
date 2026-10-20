"""
Chapter-pipeline adapter for GUI downloads (issue #295).

GUI downloads run through the same :class:`ChapterPipeline` as
`memanga check` and `failed retry`. This module turns the pipeline's
progress hooks and final outcome into the EventBus events the pages
already listen to. It has no Qt dependency.

The worker imports it only when a download runs, like the downloader
itself, so an import problem surfaces as a download error rather than
breaking the app at start-up.
"""

import threading
from pathlib import Path
from typing import Any, Dict

from ..chapter_pipeline import ChapterOutcome, ChapterReporter, ChapterStatus
from .events import EventBus


class GuiChapterReporter(ChapterReporter):
    """Publishes one queued download's progress on the event bus and
    keeps the worker's ``[Download]`` log lines."""

    def __init__(self, events: EventBus, task_id: str, manga: Dict[str, Any],
                 chapter, cancel: threading.Event):
        self._events = events
        self._task_id = task_id
        self._title = manga["title"]
        self._chapter = chapter
        self._cancel = cancel

    def _log(self, message: str):
        print(f"[Download] {message}", flush=True)

    def _label(self) -> str:
        return f"{self._title} Ch.{self._chapter.number}"

    def downloading(self, chapter):
        self._events.publish("download_started", {
            "task_id": self._task_id,
            "title": self._title,
            "chapter": self._chapter.number,
        })

    def progress(self, current, total):
        if self._cancel.is_set():
            raise InterruptedError("Download cancelled")
        try:
            self._events.publish("download_progress", {
                "task_id": self._task_id,
                "current": current,
                "total": total,
            })
        except Exception:
            pass

    def trying_backup(self, error, backup_chapter):
        self._log(
            f"Primary failed ({error}), trying backup "
            f"({backup_chapter.source}) for {self._label()}"
        )

    def backup_failed(self, error):
        self._log(f"Backup also failed: {error}")

    def saved(self, outcome: ChapterOutcome):
        if outcome.status is ChapterStatus.PARTIAL:
            src = " from backup" if outcome.from_backup else ""
            self._log(
                f"Partial kept{src}: {self._label()} "
                f"— {len(outcome.missing_pages)}/{outcome.total_pages} "
                f"page(s) missing {outcome.missing_pages}"
            )
        elif outcome.from_backup:
            self._log(f"Completed from backup: {self._label()}")

    def email_skipped_folder(self, path: Path):
        self._log(f"Kindle skipped (image folders cannot be emailed): {path}")
        # Issue #340: tell the user, as the CLI does, rather than only
        # logging it.
        self._events.publish("kindle_skipped", {
            "task_id": self._task_id,
            "title": self._title,
            "chapter": self._chapter.number,
            "path": str(path),
        })

    def email_sent(self, path: Path):
        self._events.publish("kindle_sent", {
            "task_id": self._task_id, "path": str(path),
        })

    def deleted_after_send(self, path: Path):
        self._log(f"Deleted local copy after sending: {path}")

    def delete_failed(self, path: Path, error: OSError):
        self._log(f"Could not delete local copy after sending: {error}")

    def email_failed(self, error):
        self._events.publish("kindle_error", {
            "task_id": self._task_id, "error": str(error),
        })

    def failed(self, outcome: ChapterOutcome):
        self._log(f"ERROR: {self._label()}: {outcome.message}")


def download_complete_payload(task_id: str, manga: Dict[str, Any],
                              outcome: ChapterOutcome) -> Dict[str, Any]:
    """The ``download_complete`` event for a saved chapter. An accepted
    partial (issue #86) carries its missing pages under ``partial``."""
    payload = {
        "task_id": task_id,
        "path": str(outcome.path) if outcome.path else None,
        "title": manga["title"],
        "chapter": outcome.chapter,
        "from_backup": outcome.from_backup,
        "source": outcome.source,
    }
    if outcome.status is ChapterStatus.PARTIAL:
        payload["partial"] = {
            "failed_pages": list(outcome.missing_pages),
            "total": outcome.total_pages,
        }
    return payload


def download_error_payload(task_id: str, manga: Dict[str, Any],
                           outcome: ChapterOutcome) -> Dict[str, Any]:
    """The ``download_error`` event for a chapter nothing could save."""
    return {
        "task_id": task_id,
        "error": outcome.message,
        "title": manga["title"],
        "chapter": outcome.chapter,
    }
