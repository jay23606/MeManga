"""MangaDex scanlation-group credit and removal in the desktop app (#400):
the check result cache, the Detail chapter list and the Reader."""

from __future__ import annotations

from pathlib import Path

import pytest


GROUP_A = "0e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b"
GROUP_B = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
UNAVAILABLE = "unavailable at the scanlation group's request"


def _entry(number, group_id, group_name):
    return {
        "number": number,
        "title": "",
        "source": "mangadex.org",
        "source_url": "https://mangadex.org/title/abc/test-manga",
        "url": f"https://mangadex.org/chapter/ch-{number}",
        "is_backup": False,
        "group_ids": [group_id],
        "group_names": [group_name],
    }


def _label_texts(widget):
    from PySide6.QtWidgets import QLabel
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


class TestCheckResultCache:
    def test_group_credit_is_cached_and_survives_reload(self, app_window, qapp,
                                                        sample_manga):
        from memanga.downloader import ChapterWithSource
        from memanga.scrapers.base import Chapter
        from memanga.state import State

        base = Chapter(number="1", title="", url="https://mangadex.org/chapter/ch-1")
        base.group_ids = [GROUP_A]
        base.group_names = ["Group A"]
        chapter = ChapterWithSource(base, "mangadex.org", sample_manga["url"])
        plain = Chapter(number="2", title="", url="https://mangadex.org/chapter/ch-2")

        app_window._on_check_complete({"results": [{
            "manga": sample_manga, "chapters": [], "all_chapters": [chapter, plain],
        }]})

        title = sample_manga["title"]
        cached = app_window.app_state.get_available_chapters(title)
        assert cached[0]["group_ids"] == [GROUP_A]
        assert cached[0]["group_names"] == ["Group A"]
        # Chapters from sources without group data still cache cleanly.
        assert cached[1]["group_ids"] == []
        assert cached[1]["group_names"] == []

        app_window.app_state.save()
        reloaded = State(config_dir=app_window.app_state.config_dir)
        assert reloaded.get_available_chapters(title)[0]["group_names"] == ["Group A"]


class TestDetailPage:
    @staticmethod
    def _total(page):
        return next(t for t in _label_texts(page._chapters_container)
                    if "total" in t)

    @pytest.mark.parametrize("removed,total", [([], "2 total"), ([GROUP_A], "1 total")])
    def test_removed_group_entries_are_hidden(self, app_window, qapp, sample_manga,
                                              removed, total):
        title = sample_manga["title"]
        app_window.config.set("manga", [sample_manga])
        app_window.config.set("mangadex.removed_group_ids", removed)
        app_window.app_state.set_available_chapters(title, [
            _entry("1", GROUP_A, "Group A"), _entry("2", GROUP_B, "Group B")])

        app_window.show_page("detail", manga=sample_manga); qapp.processEvents()
        assert total in self._total(app_window._pages["detail"])

    def test_downloaded_chapters_stay_listed(self, app_window, qapp, sample_manga):
        """A removal only hides cached MangaDex entries from that group;
        files the user already saved remain listed."""
        title = sample_manga["title"]
        app_window.config.set("manga", [sample_manga])
        app_window.config.set("mangadex.removed_group_ids", [GROUP_A])
        app_window.app_state.set_available_chapters(title, [
            _entry("1", GROUP_A, "Group A"), _entry("2", GROUP_B, "Group B")])
        app_window.app_state.add_downloaded_chapter(title, "1")
        app_window.app_state.add_downloaded_chapter(title, "3")

        app_window.show_page("detail", manga=sample_manga); qapp.processEvents()
        page = app_window._pages["detail"]
        assert "3 total" in self._total(page)

    def test_download_carries_group_credit(self, app_window, qapp, sample_manga):
        title = sample_manga["title"]
        app_window.config.set("manga", [sample_manga])
        app_window.app_state.set_available_chapters(
            title, [_entry("2", GROUP_B, "Group B")])
        app_window.show_page("detail", manga=sample_manga); qapp.processEvents()
        page = app_window._pages["detail"]

        queued = []

        def accept(**kw):
            queued.append(kw["chapter"])
            return f"{title}:{kw['chapter'].number}"

        app_window.worker.download_chapter = accept
        page._download_chapter(app_window.app_state.get_available_chapters(title)[0])

        assert len(queued) == 1
        assert queued[0].group_ids == [GROUP_B]
        assert queued[0].group_names == ["Group B"]


class TestReaderPage:
    @staticmethod
    def _open(app_window, qapp, manga, make_cbz):
        title = manga["title"]
        app_window.config.set("manga", [manga])
        dl = Path(app_window.config.download_dir) / title
        dl.mkdir(parents=True, exist_ok=True)
        cbz = make_cbz(pages=2)
        (dl / f"{title} - Chapter 1.cbz").write_bytes(cbz.read_bytes())
        app_window.app_state.add_downloaded_chapter(title, "1")
        app_window.app_state.set_available_chapters(
            title, [_entry("1", GROUP_A, "Group A")])
        app_window.show_page("reader", manga=manga, chapter="1")
        qapp.processEvents()
        return app_window._pages["reader"]

    def test_shows_mangadex_and_group_credit(self, app_window, qapp, sample_manga,
                                             make_cbz):
        reader = self._open(app_window, qapp, sample_manga, make_cbz)
        assert len(reader._images) == 2
        texts = _label_texts(reader)
        assert "MangaDex · Group A" in texts
        assert not any(UNAVAILABLE in t for t in texts)

    def test_removed_group_chapter_is_not_shown(self, app_window, qapp,
                                                sample_manga, make_cbz):
        app_window.config.set("mangadex.removed_group_ids", [GROUP_A])
        reader = self._open(app_window, qapp, sample_manga, make_cbz)
        assert reader._images == []
        assert any(UNAVAILABLE in t for t in _label_texts(reader))

    @staticmethod
    def _refreshed(app_window, qapp, manga, make_cbz):
        """Chapter 1 saved from Group A, then a later check whose feed
        carries only Group B's upload of it."""
        title = manga["title"]
        app_window.config.set("manga", [manga])
        dl = Path(app_window.config.download_dir) / title
        dl.mkdir(parents=True, exist_ok=True)
        (dl / f"{title} - Chapter 1.cbz").write_bytes(make_cbz(pages=2).read_bytes())
        app_window.app_state.add_downloaded_chapter(title, "1")
        app_window.app_state.set_available_chapters(
            title, [_entry("1", GROUP_A, "Group A")])
        app_window.app_state.set_available_chapters(
            title, [_entry("1", GROUP_B, "Group B")])
        app_window.show_page("reader", manga=manga, chapter="1")
        qapp.processEvents()
        return app_window._pages["reader"]

    def test_saved_chapter_keeps_its_credit_after_refresh(
            self, app_window, qapp, sample_manga, make_cbz):
        reader = self._refreshed(app_window, qapp, sample_manga, make_cbz)
        texts = _label_texts(reader)
        assert "MangaDex · Group A" in texts
        assert "MangaDex · Group B" not in texts

    def test_saved_chapter_stays_removed_after_refresh(
            self, app_window, qapp, sample_manga, make_cbz):
        app_window.config.set("mangadex.removed_group_ids", [GROUP_A])
        reader = self._refreshed(app_window, qapp, sample_manga, make_cbz)
        assert reader._images == []
        assert any(UNAVAILABLE in t for t in _label_texts(reader))

    def test_saved_record_from_download_blocks_after_feed_drops_group(
            self, app_window, qapp, sample_manga, make_cbz):
        """Record written at download time; the next feed omits the
        chapter entirely (the group's upload was filtered out)."""
        title = sample_manga["title"]
        app_window.config.set("manga", [sample_manga])
        app_window.config.set("mangadex.removed_group_ids", [GROUP_A])
        dl = Path(app_window.config.download_dir) / title
        dl.mkdir(parents=True, exist_ok=True)
        (dl / f"{title} - Chapter 1.cbz").write_bytes(make_cbz(pages=2).read_bytes())
        app_window.app_state.add_downloaded_chapter(title, "1")
        app_window.app_state.set_chapter_groups(
            title, "1", "mangadex.org", [GROUP_A], ["Group A"])
        app_window.app_state.set_available_chapters(title, [])

        app_window.show_page("reader", manga=sample_manga, chapter="1")
        qapp.processEvents()
        reader = app_window._pages["reader"]
        assert reader._images == []
        assert any(UNAVAILABLE in t for t in _label_texts(reader))

        app_window.show_page("detail", manga=sample_manga); qapp.processEvents()
        assert "Unavailable at the group's request" in _label_texts(
            app_window._pages["detail"]._chapters_container)

    def test_other_sources_show_no_mangadex_credit(self, app_window, qapp,
                                                   make_cbz):
        manga = {"title": "Other Manga", "url": "https://mock.test/m/1",
                 "source": "mock.test", "status": "reading", "mode": "manual"}
        title = manga["title"]
        app_window.config.set("manga", [manga])
        dl = Path(app_window.config.download_dir) / title
        dl.mkdir(parents=True, exist_ok=True)
        (dl / f"{title} - Chapter 1.cbz").write_bytes(make_cbz(pages=2).read_bytes())
        app_window.app_state.add_downloaded_chapter(title, "1")
        app_window.show_page("reader", manga=manga, chapter="1")
        qapp.processEvents()
        reader = app_window._pages["reader"]
        assert len(reader._images) == 2
        assert not any(t.startswith("MangaDex") for t in _label_texts(reader))
