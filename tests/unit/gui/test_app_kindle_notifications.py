"""Issue #340 — when Kindle delivery is on but the output format saves
an image folder (JPG/PNG/WEBP), the chapter is downloaded and delivery
is skipped. The app must tell the user why, as the CLI does.

The handler is exercised unbound on a stub app object, like
test_app_cover_fallback.py: no window or widgets are involved.
"""

from __future__ import annotations

from types import SimpleNamespace


def _handle(stub, data):
    from memanga.gui.app import MeMangaApp
    MeMangaApp._on_kindle_skipped(stub, data)


class TestKindleSkippedNotification:
    def test_adds_warning_with_title_chapter_and_reason(self, state,
                                                        event_bus):
        added = []
        event_bus.subscribe("notification_added", added.append)
        stub = SimpleNamespace(app_state=state, events=event_bus)

        _handle(stub, {
            "task_id": "Vinland Saga:71", "title": "Vinland Saga",
            "chapter": "71", "path": "/downloads/Vinland Saga/Chapter 71",
        })
        event_bus.poll()

        notes = state.get_notifications()
        assert len(notes) == 1
        assert notes[0]["type"] == "warn"
        message = notes[0]["message"]
        assert message.startswith("Not sent to Kindle: Vinland Saga Ch. 71")
        assert "image folders" in message
        assert "PDF, EPUB, CBZ or ZIP" in message
        assert added == [{}]
