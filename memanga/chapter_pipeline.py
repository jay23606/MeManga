"""Process one chapter: download, recover, deliver and record it.

``check`` and ``failed retry`` both run every chapter through
:class:`ChapterPipeline` (issue #222), so backup recovery, partial-chapter
salvage (issue #86), Kindle delivery, delete-after-send, failure records
and state cleanup cannot drift between the two commands.

The pipeline makes the decisions and writes state; it prints nothing.
Progress goes to a :class:`ChapterReporter` while a chapter is processed
(downloads are slow, so the user must see each step as it happens), and
the final result comes back as a :class:`ChapterOutcome`.
"""

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .downloader import (DEFAULT_PAGE_RETRIES, ChapterWithSource,
                         DownloaderError, _find_chapter_on_backup,
                         _get_sources_from_manga, download_chapter)
from .emailer import EmailError, send_to_kindle


class ChapterStatus(Enum):
    """How a chapter ended up after every recovery step."""

    COMPLETE = "complete"
    PARTIAL = "partial"   # saved with missing pages, within the threshold
    FAILED = "failed"     # nothing saved; a failure record was written


class DeliveryStatus(Enum):
    """What Kindle delivery did with a saved chapter."""

    NOT_REQUESTED = "not_requested"    # local delivery, or no Kindle address
    SKIPPED_FOLDER = "skipped_folder"  # image folders cannot be emailed
    SENT = "sent"
    FAILED = "failed"                  # the file stays on disk


@dataclass(frozen=True)
class PipelineOptions:
    """Download and delivery settings for one run, shared by every chapter."""

    download_dir: Path
    output_format: str
    max_retries: int = DEFAULT_PAGE_RETRIES
    post_processing: Optional[Dict[str, Any]] = None
    allow_partial: bool = False
    partial_threshold: float = 0.0
    delivery_mode: str = "local"
    email: Dict[str, Any] = field(default_factory=dict)
    delete_after_send: bool = False


@dataclass(frozen=True)
class ChapterOutcome:
    """Result of processing one chapter."""

    chapter: str
    status: ChapterStatus
    path: Optional[Path] = None
    source: Optional[str] = None
    from_backup: bool = False
    # Only for PARTIAL: 1-indexed pages that are missing, and the total.
    missing_pages: List[int] = field(default_factory=list)
    total_pages: int = 0
    delivery: DeliveryStatus = DeliveryStatus.NOT_REQUESTED
    # Only for FAILED: why the primary and (when tried) backup failed.
    error: Optional[str] = None
    backup_error: Optional[str] = None

    @property
    def saved(self) -> bool:
        """True when a complete or partial chapter was saved."""
        return self.status is not ChapterStatus.FAILED


class ChapterReporter:
    """Progress hooks called while a chapter is processed.

    Every hook does nothing by default; a front end overrides the ones it
    shows.
    """

    def downloading(self, chapter):
        """About to download ``chapter`` from its own source."""

    def trying_backup(self, error, backup_chapter):
        """The primary download failed with ``error``; trying the backup."""

    def backup_failed(self, error):
        """The complete download from the backup failed too."""

    def saved(self, outcome: ChapterOutcome):
        """A chapter was saved (complete or partial), before delivery."""

    def email_skipped_folder(self, path: Path):
        """``path`` is an image folder, which cannot be emailed."""

    def email_sending(self, path: Path):
        """About to send ``path`` to Kindle."""

    def email_sent(self, path: Path):
        """``path`` reached the Kindle address."""

    def deleted_after_send(self, path: Path):
        """The local copy was deleted after sending."""

    def email_failed(self, error):
        """Sending failed; the chapter still counts as saved."""

    def failed(self, outcome: ChapterOutcome):
        """Nothing could be saved; the failure has been recorded."""


def _no_app_password() -> Optional[str]:
    return None


@dataclass
class ChapterPipeline:
    """Runs chapters the same way for every command.

    The collaborators default to the real downloader and emailer; a caller
    may substitute its own (the CLI passes the names it imported, so they
    can be replaced in one place).
    """

    options: PipelineOptions
    state: Any
    reporter: ChapterReporter = field(default_factory=ChapterReporter)
    download: Callable[..., Path] = download_chapter
    find_backup: Callable[..., Optional[ChapterWithSource]] = _find_chapter_on_backup
    send: Callable[..., Any] = send_to_kindle
    app_password: Callable[[], Optional[str]] = _no_app_password

    def process(self, manga: Dict[str, Any], chapter) -> ChapterOutcome:
        """Download ``chapter`` of ``manga`` and record the result.

        Tries, in order: the chapter's own source; a complete copy from a
        backup source; then, with partial tolerance on, the best partial
        (backup first, then primary) within the threshold. A saved
        chapter is delivered and marked downloaded; a chapter nothing
        could save is recorded as failed. Only :class:`DownloaderError`
        is handled here; anything else propagates to the caller.
        """
        opts = self.options
        is_from_backup = isinstance(chapter, ChapterWithSource) and chapter.is_backup
        # Only accept a partial straight away when there's no backup to
        # try first — otherwise prefer a complete download from the
        # backup (issue #86 direction).
        has_backup = (not is_from_backup) and len(_get_sources_from_manga(manga)) > 1
        primary_allow_partial = opts.allow_partial and not has_backup

        self.reporter.downloading(chapter)
        partial = {}
        try:
            path = self._download(manga, chapter, partial,
                                  allow_partial=primary_allow_partial)
        except DownloaderError as error:
            return self._recover(manga, chapter, error,
                                 is_from_backup, primary_allow_partial)
        return self._save(
            manga, chapter, path, partial,
            from_backup=False,
            source=getattr(chapter, "source", None) or "unknown",
        )

    def _download(self, manga, chapter, partial, allow_partial=None):
        """One download attempt. ``partial`` collects the missing pages
        when a partial is kept; ``allow_partial=None`` asks for a
        complete chapter only."""
        opts = self.options
        kwargs = {}
        if allow_partial is not None:
            partial.clear()

            def _capture_partial(failed_pages, total):
                partial["failed_pages"] = failed_pages
                partial["total"] = total

            kwargs = dict(
                allow_partial=allow_partial,
                partial_threshold=opts.partial_threshold,
                on_partial=_capture_partial,
            )
        return self.download(
            manga, chapter, opts.download_dir, opts.output_format,
            state=self.state,
            max_retries=opts.max_retries,
            post_processing=opts.post_processing,
            **kwargs,
        )

    def _recover(self, manga, chapter, error, is_from_backup,
                 primary_allow_partial) -> ChapterOutcome:
        """The primary download failed: try the backup, then a partial,
        else record the failure."""
        title = manga["title"]
        backup_ch = None
        if not is_from_backup:
            backup_ch = self.find_backup(manga, chapter.number, self.state)

        # 1) A complete download from the backup source.
        backup_error = None
        if backup_ch:
            self.reporter.trying_backup(error, backup_ch)
            try:
                path = self._download(manga, backup_ch, {})
            except DownloaderError as be:
                backup_error = be
                self.reporter.backup_failed(be)
            else:
                return self._save(
                    manga, chapter, path, None,
                    from_backup=True,
                    source=getattr(backup_ch, "source", None),
                )

        # 2) No complete chapter anywhere. With partial tolerance on,
        #    salvage the best partial within the threshold.
        if self.options.allow_partial:
            candidates = []
            if backup_ch:
                candidates.append((backup_ch, True))
            # The primary was already tried with partial allowed above
            # when there is no backup.
            if not primary_allow_partial:
                candidates.append((chapter, False))
            for candidate, from_backup in candidates:
                partial = {}
                try:
                    path = self._download(manga, candidate, partial,
                                          allow_partial=True)
                except DownloaderError:
                    continue
                return self._save(
                    manga, chapter, path, partial,
                    from_backup=from_backup,
                    source=getattr(candidate, "source", None),
                )

        # 3) Nothing worked — record the failure.
        ch_source = chapter.source if isinstance(chapter, ChapterWithSource) else "unknown"
        if backup_ch and backup_error is not None:
            source = f"{ch_source}+{backup_ch.source}"
            message = f"Primary: {error}; Backup: {backup_error}"
        else:
            source = ch_source
            message = str(error)
        outcome = ChapterOutcome(
            chapter=chapter.number,
            status=ChapterStatus.FAILED,
            source=source,
            error=str(error),
            backup_error=None if backup_error is None else str(backup_error),
        )
        self.reporter.failed(outcome)
        self.state.add_failed_chapter(
            title, chapter.number, source, message,
            failed_pages=getattr(error, "failed_pages", None),
        )
        return outcome

    def _save(self, manga, chapter, path, partial, from_backup,
              source) -> ChapterOutcome:
        """Record a saved chapter, deliver it, then mark it downloaded."""
        title = manga["title"]
        if partial:
            missing = partial.get("failed_pages", [])
            total = partial.get("total", 0)
            outcome = ChapterOutcome(
                chapter=chapter.number,
                status=ChapterStatus.PARTIAL,
                path=path,
                source=source,
                from_backup=from_backup,
                missing_pages=missing,
                total_pages=total,
            )
            # Headless runs must leave a trail of which pages are missing.
            self.state.add_notification(
                "warn",
                f"'{title}' Chapter {chapter.number} saved as partial: "
                f"{len(missing)}/{total} page(s) missing (pages {missing}).",
            )
            self.state.add_partial_chapter(
                title,
                chapter.number,
                source=source or "",
                failed_pages=missing,
                total_pages=total,
                path=str(path),
                from_backup=from_backup,
            )
        else:
            outcome = ChapterOutcome(
                chapter=chapter.number,
                status=ChapterStatus.COMPLETE,
                path=path,
                source=source,
                from_backup=from_backup,
            )
            self.state.clear_partial_chapter(title, chapter.number)
        self.reporter.saved(outcome)

        # Delivery runs before the chapter is marked downloaded (#86).
        outcome = replace(outcome, delivery=self._deliver(path))
        self.state.add_downloaded_chapter(title, chapter.number)
        self.state.clear_pending_backup(title, chapter.number)
        self.state.clear_failed_chapter(title, chapter.number)
        return outcome

    def _deliver(self, path: Path) -> DeliveryStatus:
        """Send to Kindle when email delivery is configured."""
        opts = self.options
        email = opts.email
        if not (opts.delivery_mode == "email" and email.get("kindle_email")):
            return DeliveryStatus.NOT_REQUESTED
        if path.is_dir():
            self.reporter.email_skipped_folder(path)
            return DeliveryStatus.SKIPPED_FOLDER
        self.reporter.email_sending(path)
        try:
            self.send(
                pdf_path=path,
                kindle_email=email["kindle_email"],
                sender_email=email["sender_email"],
                smtp_server=email.get("smtp_server", "smtp.gmail.com"),
                smtp_port=email.get("smtp_port", 587),
                app_password=self.app_password(),
            )
            self.reporter.email_sent(path)
            if opts.delete_after_send:
                path.unlink()
                self.reporter.deleted_after_send(path)
        except EmailError as error:
            self.reporter.email_failed(error)
            return DeliveryStatus.FAILED
        return DeliveryStatus.SENT
