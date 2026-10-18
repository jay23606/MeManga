"""Shared chapter pipeline used by `check` and `failed retry` (issue #222).

Downloads and email are replaced with fakes; State is real so the
records each outcome leaves behind are checked as written.
"""

from __future__ import annotations

import pytest

from memanga.chapter_pipeline import (ChapterPipeline, ChapterReporter,
                                      ChapterStatus, DeliveryStatus,
                                      PipelineOptions)
from memanga.downloader import ChapterWithSource, DownloaderError
from memanga.emailer import EmailError
from memanga.scrapers.base import Chapter


TITLE = "Vinland Saga"
SINGLE = {"title": TITLE, "source": "primary.test", "url": "https://primary.test/vs"}
WITH_BACKUP = {
    "title": TITLE,
    "sources": [
        {"source": "primary.test", "url": "https://primary.test/vs"},
        {"source": "backup.test", "url": "https://backup.test/vs"},
    ],
}
EMAIL = {"kindle_email": "reader@example.com", "sender_email": "sender@example.com"}


def _primary(number="71"):
    return ChapterWithSource(Chapter(number=number, url=f"https://primary.test/c/{number}"),
                             "primary.test", "https://primary.test/vs")


def _backup(number="71"):
    return ChapterWithSource(Chapter(number=number, url=f"https://backup.test/c/{number}"),
                             "backup.test", "https://backup.test/vs", is_backup=True)


class _Recorder(ChapterReporter):
    def __init__(self):
        self.events = []

    def downloading(self, chapter):
        self.events.append(("downloading", chapter.source))

    def trying_backup(self, error, backup_chapter):
        self.events.append(("trying_backup", str(error), backup_chapter.source))

    def backup_failed(self, error):
        self.events.append(("backup_failed", str(error)))

    def saved(self, outcome):
        self.events.append(("saved", outcome.status, outcome.from_backup))

    def email_skipped_folder(self, path):
        self.events.append(("email_skipped_folder",))

    def email_sending(self, path):
        self.events.append(("email_sending",))

    def email_sent(self, path):
        self.events.append(("email_sent",))

    def deleted_after_send(self, path):
        self.events.append(("deleted_after_send",))

    def email_failed(self, error):
        self.events.append(("email_failed", str(error)))

    def failed(self, outcome):
        self.events.append(("failed", outcome.error))


class _Downloads:
    """Fake `download_chapter`. `results` maps (source, partial_allowed)
    to a path, a DownloaderError, or ("partial", path, missing, total)."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, manga, chapter, output_dir, output_format, **kwargs):
        allow_partial = kwargs.get("allow_partial")
        self.calls.append((chapter.source, allow_partial))
        result = self.results.get((chapter.source, bool(allow_partial)))
        if result is None:
            result = self.results[(chapter.source, False)]
        if isinstance(result, Exception):
            raise result
        if isinstance(result, tuple):
            _, path, missing, total = result
            kwargs["on_partial"](missing, total)
            return path
        return result


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "chapter.pdf"
    path.write_bytes(b"%PDF")
    return path


def _pipeline(state, tmp_path, downloads, backup=None, sent=None, **opts):
    def _send(**kwargs):
        if isinstance(sent, Exception):
            raise sent
        if sent is not None:
            sent.append(kwargs["pdf_path"])
            # Delivery happens before the chapter is marked downloaded.
            assert not state.is_chapter_downloaded(TITLE, "71")

    return ChapterPipeline(
        options=PipelineOptions(download_dir=tmp_path, output_format="pdf", **opts),
        state=state,
        reporter=_Recorder(),
        download=downloads,
        find_backup=lambda manga, number, state: backup,
        send=_send,
        app_password=lambda: "pw",
    )


class TestPrimarySuccess:
    def test_complete_save_marks_downloaded_and_clears_records(
            self, state, tmp_path, pdf):
        state.add_failed_chapter(TITLE, "71", "primary.test", "timeout")
        state.add_partial_chapter(TITLE, "71", source="primary.test",
                                  failed_pages=[3], total_pages=20)
        downloads = _Downloads({("primary.test", False): pdf})
        pipeline = _pipeline(state, tmp_path, downloads)

        outcome = pipeline.process(SINGLE, _primary())

        assert outcome.status is ChapterStatus.COMPLETE
        assert outcome.saved
        assert outcome.path == pdf
        assert outcome.source == "primary.test"
        assert not outcome.from_backup
        assert outcome.delivery is DeliveryStatus.NOT_REQUESTED
        assert downloads.calls == [("primary.test", False)]
        assert state.is_chapter_downloaded(TITLE, "71")
        assert state.get_failed_chapters(TITLE) == {}
        assert state.get_partial_chapter(TITLE, "71") is None
        assert pipeline.reporter.events == [
            ("downloading", "primary.test"),
            ("saved", ChapterStatus.COMPLETE, False),
        ]

    def test_single_source_accepts_partial_on_first_attempt(
            self, state, tmp_path, pdf):
        downloads = _Downloads({("primary.test", True): ("partial", pdf, [7], 40)})
        pipeline = _pipeline(state, tmp_path, downloads,
                             allow_partial=True, partial_threshold=10)

        outcome = pipeline.process(SINGLE, _primary())

        assert outcome.status is ChapterStatus.PARTIAL
        assert outcome.saved
        assert (outcome.missing_pages, outcome.total_pages) == ([7], 40)
        # No backup to prefer, so no second attempt.
        assert downloads.calls == [("primary.test", True)]
        assert state.get_partial_chapter(TITLE, "71")["failed_pages"] == [7]
        assert state.is_chapter_downloaded(TITLE, "71")
        assert any("saved as partial" in n["message"]
                   for n in state.get("notifications", []))

    def test_with_backup_primary_does_not_accept_partial_first(
            self, state, tmp_path, pdf):
        downloads = _Downloads({("primary.test", False): pdf})
        pipeline = _pipeline(state, tmp_path, downloads, backup=_backup(),
                             allow_partial=True, partial_threshold=10)

        pipeline.process(WITH_BACKUP, _primary())

        assert downloads.calls == [("primary.test", False)]


class TestBackupRecovery:
    def test_backup_complete_is_emailed_then_marked_downloaded(
            self, state, tmp_path, pdf):
        state.add_failed_chapter(TITLE, "71", "primary.test", "timeout")
        downloads = _Downloads({
            ("primary.test", False): DownloaderError("primary broke"),
            ("backup.test", False): pdf,
        })
        sent = []
        pipeline = _pipeline(state, tmp_path, downloads, backup=_backup(),
                             sent=sent, delivery_mode="email", email=EMAIL)

        outcome = pipeline.process(WITH_BACKUP, _primary())

        assert outcome.status is ChapterStatus.COMPLETE
        assert outcome.from_backup
        assert outcome.source == "backup.test"
        assert outcome.delivery is DeliveryStatus.SENT
        assert sent == [pdf]
        # The complete backup attempt does not ask for a partial.
        assert downloads.calls == [("primary.test", False), ("backup.test", None)]
        assert state.is_chapter_downloaded(TITLE, "71")
        assert state.get_failed_chapters(TITLE) == {}
        assert pipeline.reporter.events == [
            ("downloading", "primary.test"),
            ("trying_backup", "primary broke", "backup.test"),
            ("saved", ChapterStatus.COMPLETE, True),
            ("email_sending",),
            ("email_sent",),
        ]

    def test_partial_salvage_prefers_backup(self, state, tmp_path, pdf):
        downloads = _Downloads({
            ("primary.test", False): DownloaderError("primary incomplete"),
            ("backup.test", False): DownloaderError("backup incomplete"),
            ("backup.test", True): ("partial", pdf, [7], 40),
        })
        pipeline = _pipeline(state, tmp_path, downloads, backup=_backup(),
                             allow_partial=True, partial_threshold=10)

        outcome = pipeline.process(WITH_BACKUP, _primary())

        assert outcome.status is ChapterStatus.PARTIAL
        assert outcome.from_backup
        assert downloads.calls == [
            ("primary.test", False), ("backup.test", None), ("backup.test", True),
        ]
        record = state.get_partial_chapter(TITLE, "71")
        assert record["failed_pages"] == [7]
        assert record["source"] == "backup.test"
        assert state.is_chapter_downloaded(TITLE, "71")

    def test_partial_salvage_falls_back_to_primary(self, state, tmp_path, pdf):
        downloads = _Downloads({
            ("primary.test", False): DownloaderError("primary incomplete"),
            ("primary.test", True): ("partial", pdf, [2], 20),
            ("backup.test", False): DownloaderError("backup incomplete"),
        })
        pipeline = _pipeline(state, tmp_path, downloads, backup=_backup(),
                             allow_partial=True, partial_threshold=10)

        outcome = pipeline.process(WITH_BACKUP, _primary())

        assert outcome.status is ChapterStatus.PARTIAL
        assert not outcome.from_backup
        assert outcome.source == "primary.test"
        assert downloads.calls[-1] == ("primary.test", True)

    def test_chapter_already_from_backup_skips_backup_lookup(
            self, state, tmp_path):
        downloads = _Downloads({("backup.test", False): DownloaderError("gone")})
        looked_up = []
        pipeline = _pipeline(state, tmp_path, downloads)
        pipeline.find_backup = lambda *a: looked_up.append(a)

        outcome = pipeline.process(WITH_BACKUP, _backup())

        assert outcome.status is ChapterStatus.FAILED
        assert looked_up == []
        assert state.get_failed_chapters(TITLE)["71"]["source"] == "backup.test"


class TestFailure:
    def test_both_sources_fail_records_combined_failure(self, state, tmp_path):
        downloads = _Downloads({
            ("primary.test", False): DownloaderError("primary broke",
                                                     failed_pages=[4], total_pages=9),
            ("backup.test", False): DownloaderError("backup broke"),
        })
        pipeline = _pipeline(state, tmp_path, downloads, backup=_backup())

        outcome = pipeline.process(WITH_BACKUP, _primary())

        assert outcome.status is ChapterStatus.FAILED
        assert not outcome.saved
        assert outcome.path is None
        assert (outcome.error, outcome.backup_error) == ("primary broke", "backup broke")
        record = state.get_failed_chapters(TITLE)["71"]
        assert record["source"] == "primary.test+backup.test"
        assert record["error"] == "Primary: primary broke; Backup: backup broke"
        assert record["failed_pages"] == [4]
        assert not state.is_chapter_downloaded(TITLE, "71")
        assert pipeline.reporter.events[-2:] == [
            ("backup_failed", "backup broke"),
            ("failed", "primary broke"),
        ]

    def test_no_backup_records_primary_failure(self, state, tmp_path):
        downloads = _Downloads({("primary.test", False): DownloaderError("HTTP 503")})
        pipeline = _pipeline(state, tmp_path, downloads)

        outcome = pipeline.process(SINGLE, _primary())

        assert outcome.status is ChapterStatus.FAILED
        assert outcome.backup_error is None
        record = state.get_failed_chapters(TITLE)["71"]
        assert (record["source"], record["error"]) == ("primary.test", "HTTP 503")

    def test_other_errors_propagate_without_records(self, state, tmp_path):
        downloads = _Downloads({("primary.test", False): RuntimeError("bug")})
        pipeline = _pipeline(state, tmp_path, downloads)

        with pytest.raises(RuntimeError):
            pipeline.process(SINGLE, _primary())
        assert state.get_failed_chapters(TITLE) == {}
        assert not state.is_chapter_downloaded(TITLE, "71")


class TestDelivery:
    def test_delete_after_send_removes_local_copy(self, state, tmp_path, pdf):
        downloads = _Downloads({("primary.test", False): pdf})
        pipeline = _pipeline(state, tmp_path, downloads, sent=[],
                             delivery_mode="email", email=EMAIL,
                             delete_after_send=True)

        outcome = pipeline.process(SINGLE, _primary())

        assert outcome.delivery is DeliveryStatus.SENT
        assert not pdf.exists()
        assert pipeline.reporter.events[-1] == ("deleted_after_send",)
        assert state.is_chapter_downloaded(TITLE, "71")

    def test_email_failure_keeps_file_and_still_counts_as_saved(
            self, state, tmp_path, pdf):
        downloads = _Downloads({("primary.test", False): pdf})
        pipeline = _pipeline(state, tmp_path, downloads,
                             sent=EmailError("SMTP down"),
                             delivery_mode="email", email=EMAIL,
                             delete_after_send=True)

        outcome = pipeline.process(SINGLE, _primary())

        assert outcome.status is ChapterStatus.COMPLETE
        assert outcome.delivery is DeliveryStatus.FAILED
        assert pdf.exists()
        assert pipeline.reporter.events[-1] == ("email_failed", "SMTP down")
        assert state.is_chapter_downloaded(TITLE, "71")

    def test_image_folder_is_not_emailed(self, state, tmp_path):
        folder = tmp_path / "Chapter 71"
        folder.mkdir()
        sent = []
        downloads = _Downloads({("primary.test", False): folder})
        pipeline = _pipeline(state, tmp_path, downloads, sent=sent,
                             delivery_mode="email", email=EMAIL)

        outcome = pipeline.process(SINGLE, _primary())

        assert outcome.delivery is DeliveryStatus.SKIPPED_FOLDER
        assert sent == []

    def test_local_delivery_never_emails(self, state, tmp_path, pdf):
        sent = []
        downloads = _Downloads({("primary.test", False): pdf})
        pipeline = _pipeline(state, tmp_path, downloads, sent=sent,
                             delivery_mode="local", email=EMAIL)

        assert pipeline.process(SINGLE, _primary()).delivery is DeliveryStatus.NOT_REQUESTED
        assert sent == []
