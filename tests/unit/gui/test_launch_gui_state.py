"""launch_gui() with a broken config or a damaged state file (issue #243).

Qt entry points are replaced with fakes so the test never opens a window
or enters the event loop. The fakes forward every other class attribute to
the real Qt class: pytest-qt calls QApplication.instance() around every
test, and runs while these patches are in place.
"""

from __future__ import annotations

import types

import pytest


@pytest.fixture
def cfg_dir(isolated_home):
    d = isolated_home / ".config" / "memanga"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _forwarding(real):
    """Metaclass that sends unknown class attributes to ``real``."""
    class Forward(type):
        def __getattr__(cls, name):
            return getattr(real, name)
    return Forward


@pytest.fixture
def fake_qt(qapp, monkeypatch):
    import PySide6.QtCore
    import PySide6.QtWidgets
    import memanga.gui as gui
    import memanga.gui.app as app_mod

    calls = {"critical": [], "warning": [], "windows": []}
    real_app = PySide6.QtWidgets.QApplication

    class FakeApplication(metaclass=_forwarding(real_app)):
        # launch_gui() builds its own QApplication; the session already
        # has one (``qapp``), so the constructor must not create another.
        def __init__(self, argv):
            pass

        def setStyle(self, style):
            pass

        def setWindowIcon(self, icon):
            pass

        def exec(self):
            return 0

    class FakeMessageBox(metaclass=_forwarding(PySide6.QtWidgets.QMessageBox)):
        @staticmethod
        def critical(parent, title, text):
            calls["critical"].append(text)

        @staticmethod
        def warning(parent, title, text):
            calls["warning"].append(text)

    class FakeTimer(metaclass=_forwarding(PySide6.QtCore.QTimer)):
        @staticmethod
        def singleShot(msec, fn):
            fn()

    class FakeWindow:
        def __init__(self, config=None, app_state=None):
            calls["windows"].append((config, app_state))

        def show(self):
            pass

    monkeypatch.setattr(PySide6.QtWidgets, "QApplication", FakeApplication)
    monkeypatch.setattr(PySide6.QtWidgets, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(PySide6.QtCore, "QTimer", FakeTimer)
    monkeypatch.setattr(app_mod, "MeMangaApp", FakeWindow)
    monkeypatch.setattr(gui, "_set_windows_app_user_model_id", lambda: None)
    monkeypatch.setattr(gui, "_ensure_browsers", lambda: None)
    monkeypatch.setattr(gui, "_load_app_icon", lambda: None)
    monkeypatch.setattr("memanga.gui.theme.apply", lambda qapp: None)
    return calls


def _launch():
    from memanga.gui import launch_gui
    with pytest.raises(SystemExit) as exc:
        launch_gui()
    return exc.value.code


def test_broken_config_shows_error_instead_of_crashing(cfg_dir, fake_qt):
    (cfg_dir / "config.yaml").write_text("manga: [unclosed\n", encoding="utf-8")
    assert _launch() == 1
    assert "config file" in fake_qt["critical"][0]
    assert fake_qt["windows"] == []
    assert (cfg_dir / "config.yaml").read_text(encoding="utf-8") == "manga: [unclosed\n"


def test_unloadable_state_shows_error_and_keeps_file(cfg_dir, fake_qt):
    (cfg_dir / "state.json").mkdir()
    assert _launch() == 1
    assert "state file" in fake_qt["critical"][0]
    assert fake_qt["windows"] == []
    assert (cfg_dir / "state.json").is_dir()


def test_damaged_state_warns_once_window_is_shown(cfg_dir, fake_qt):
    (cfg_dir / "state.json").write_text("{bad", encoding="utf-8")
    assert _launch() == 0
    assert fake_qt["critical"] == []
    assert len(fake_qt["warning"]) == 1
    assert "kept as" in fake_qt["warning"][0]
    config, state = fake_qt["windows"][0]
    assert state.recovery_notice == fake_qt["warning"][0]


def test_healthy_files_show_no_dialog(cfg_dir, fake_qt):
    assert _launch() == 0
    assert fake_qt["critical"] == [] and fake_qt["warning"] == []
    assert len(fake_qt["windows"]) == 1


def test_passed_objects_reach_the_window(cfg_dir, fake_qt):
    """The CLI hands over its loaded config/state (`memanga --gui`); the
    window must get those, not reload a repaired state file."""
    from memanga.config import Config
    from memanga.gui import launch_gui
    from memanga.state import State
    (cfg_dir / "state.json").write_text("{bad", encoding="utf-8")
    config = Config(cfg_dir, tolerate_errors=True)
    state = State(cfg_dir)
    with pytest.raises(SystemExit) as exc:
        launch_gui(config=config, state=state)
    assert exc.value.code == 0
    assert fake_qt["windows"] == [(config, state)]
    assert fake_qt["warning"] == [state.recovery_notice]


def test_window_keeps_passed_config_and_state(qapp, theme, isolated_home):
    from memanga.config import Config
    from memanga.gui.app import MeMangaApp
    from memanga.state import State
    config = Config(tolerate_errors=True)
    state = State()
    w = MeMangaApp(config=config, app_state=state)
    try:
        assert w.config is config
        assert w.app_state is state
    finally:
        try:
            w.worker.shutdown()
        except Exception:
            pass
        w.close()


def test_startup_problem_none_for_healthy_objects():
    from memanga.gui import _startup_problem
    ok = types.SimpleNamespace(load_error=None, config_path="c")
    assert _startup_problem(ok, types.SimpleNamespace(load_error=None)) is None


def test_locked_config_dir_shows_error_and_loads_nothing(cfg_dir, fake_qt,
                                                          lock_holder):
    """Issue #244: another process (a check, another window) holds the
    config directory; the GUI must not load and later overwrite it."""
    (cfg_dir / "state.json").write_text("{bad", encoding="utf-8")
    lock_holder.hold(cfg_dir)
    assert _launch() == 1
    assert "already running" in fake_qt["critical"][0]
    assert str(cfg_dir) in fake_qt["critical"][0]
    assert fake_qt["windows"] == []
    # Not even loaded: a damaged file would have been copied aside.
    assert (cfg_dir / "state.json").read_text(encoding="utf-8") == "{bad"


def test_window_holds_the_config_dir_lock(cfg_dir, fake_qt, lock_holder):
    assert _launch() == 0
    assert len(fake_qt["windows"]) == 1
    assert not lock_holder.can_lock(cfg_dir)
