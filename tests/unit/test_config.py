"""Tests for memanga.config.Config — dotted-key get/set, manga list
mutation, app-password keyring helpers, persistence round-trips."""

from __future__ import annotations

import pytest


class TestConfigBasics:
    def test_get_returns_default_for_unknown_key(self, config):
        assert config.get("nope.nothing.here", "fallback") == "fallback"

    def test_set_then_get(self, config):
        config.set("delivery.output_format", "epub")
        assert config.get("delivery.output_format") == "epub"

    def test_mangadex_original_is_default_and_choice_persists(self, config):
        assert config.get("mangadex.data_saver") is False
        assert config.get("mangadex.removed_group_ids") == []
        config.set("mangadex.data_saver", True)
        config.set("mangadex.removed_group_ids", ["group-1"])
        config.save()
        config.reload()
        assert config.get("mangadex.data_saver") is True
        assert config.get("mangadex.removed_group_ids") == ["group-1"]

    def test_dotted_key_creates_nested(self, config):
        config.set("a.b.c", 42)
        assert config.get("a.b.c") == 42
        # Top-level nested dicts should also be readable directly.
        a = config.get("a")
        assert a == {"b": {"c": 42}}


class TestPartialChapterConfig:
    """Partial-chapter tolerance settings (issue #86)."""

    def test_disabled_by_default(self, config):
        assert config.partial_enabled is False
        # Default threshold is exposed as a float for the downloader.
        assert config.partial_threshold == 5.0

    def test_enable_and_set_threshold(self, config):
        config.set("partial_chapters.enabled", True)
        config.set("partial_chapters.threshold_percent", 10)
        assert config.partial_enabled is True
        assert config.partial_threshold == 10.0

    def test_threshold_falls_back_on_garbage(self, config):
        config.set("partial_chapters.threshold_percent", "not-a-number")
        assert config.partial_threshold == 5.0

    def test_threshold_clamps_above_100(self, config):
        config.set("partial_chapters.threshold_percent", 150)
        assert config.partial_threshold == 100.0

    def test_threshold_clamps_below_0(self, config):
        config.set("partial_chapters.threshold_percent", -20)
        assert config.partial_threshold == 0.0


class TestConfigPersistence:
    def test_save_then_reload(self, isolated_home):
        from memanga.config import Config
        c1 = Config()
        c1.set("delivery.naming_template", "{title}-{chapter}")
        c1.save()
        c2 = Config()  # fresh load from disk
        assert c2.get("delivery.naming_template") == "{title}-{chapter}"


class TestMangaList:
    def test_add_manga_via_set(self, config):
        config.set("manga", [{"title": "X", "url": "u"}])
        assert len(config.get("manga", [])) == 1

    def test_update_manga_mutates_and_persists(self, config):
        config.set("manga", [{"title": "X", "url": "u", "status": "reading"}])

        def _mutator(entry):
            entry["status"] = "completed"
            return True

        ok = config.update_manga("X", _mutator)
        assert ok
        assert config.get("manga")[0]["status"] == "completed"

    def test_update_manga_unknown_returns_false(self, config):
        config.set("manga", [])
        assert not config.update_manga("never", lambda e: True)


class TestKeyringHelpers:
    def test_set_then_get_app_password(self, config, monkeypatch):
        # Patch keyring so the test never touches the real OS keychain.
        store = {}
        import keyring
        monkeypatch.setattr(keyring, "set_password",
                            lambda s, u, p: store.__setitem__((s, u), p))
        monkeypatch.setattr(keyring, "get_password",
                            lambda s, u: store.get((s, u)))
        from memanga.config import set_app_password, get_app_password
        set_app_password(config, "secret-pw")
        assert get_app_password(config) == "secret-pw"

    def test_get_app_password_missing_returns_empty(self, config, monkeypatch):
        import keyring
        monkeypatch.setattr(keyring, "get_password", lambda s, u: None)
        from memanga.config import get_app_password
        # Contract: when nothing stored in keyring AND nothing in config,
        # return the empty string (not None — Config never stores None).
        assert get_app_password(config) == ""


class TestInvalidConfigFile:
    def _write(self, isolated_home, text):
        path = isolated_home / ".config" / "memanga" / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_strict_by_default(self, isolated_home):
        import yaml
        from memanga.config import Config
        self._write(isolated_home, "manga: [unclosed\n")
        with pytest.raises(yaml.YAMLError):
            Config()

    @pytest.mark.parametrize("text", ["manga: [unclosed\n", "- just\n- a list\n"])
    def test_tolerant_loads_defaults_and_refuses_save(self, isolated_home, text):
        from memanga.config import Config, ConfigError
        path = self._write(isolated_home, text)
        cfg = Config(tolerate_errors=True)
        assert cfg.load_error
        assert cfg.get("manga") == []
        with pytest.raises(ConfigError):
            cfg.save()
        assert path.read_text(encoding="utf-8") == text

    def test_reload_clears_error_once_fixed(self, isolated_home):
        from memanga.config import Config
        path = self._write(isolated_home, "manga: [unclosed\n")
        cfg = Config(tolerate_errors=True)
        path.write_text("manga: []\n", encoding="utf-8")
        cfg.reload()
        assert cfg.load_error is None
        cfg.save()


class TestConfigKeys:
    """Schema behind `config get/set/edit` (issue #353)."""

    @pytest.mark.parametrize("key,text,expected", [
        ("delivery.output_format", "EPUB", "epub"),
        ("delivery.mode", "email", "email"),
        ("delivery.delete_after_send", "yes", True),
        ("gui.auto_check", "off", False),
        ("email.smtp_port", "587", 587),
        ("partial_chapters.threshold_percent", "12.5", 12.5),
        ("cron.time", "6:5", "06:05"),
        ("delivery.post_processing.command", "", ""),
        ("email.kindle_email", "me@kindle.com", "me@kindle.com"),
    ])
    def test_parse_accepts(self, key, text, expected):
        from memanga.config import parse_config_value
        value = parse_config_value(key, text)
        assert value == expected
        assert type(value) is type(expected)

    @pytest.mark.parametrize("key,text", [
        ("delivery.output_format", "mobi"),
        ("delivery.delete_after_send", "maybe"),
        ("email.smtp_port", "0"),
        ("email.smtp_port", "70000"),
        ("email.smtp_port", "5.5"),
        ("partial_chapters.threshold_percent", "150"),
        ("partial_chapters.threshold_percent", "nan"),
        ("cron.time", "25:00"),
        ("cron.time", "noon"),
        ("delivery.naming_template", "  "),
        ("gui.auto_check_interval", "5"),
        ("mangadex.removed_group_ids", "x"),
    ])
    def test_parse_rejects(self, key, text):
        from memanga.config import parse_config_value
        with pytest.raises(ValueError):
            parse_config_value(key, text)

    def test_unknown_key(self):
        from memanga.config import UnknownConfigKey, parse_config_value
        with pytest.raises(UnknownConfigKey):
            parse_config_value("manga", "[]")

    def test_defaults_are_valid(self, isolated_home):
        from memanga.config import CONFIG_KEYS, Config, config_value_problem
        cfg = Config()
        for key in CONFIG_KEYS:
            assert config_value_problem(key, cfg.get(key)) is None, key

    @pytest.mark.parametrize("key,value", [
        ("email.smtp_port", "587"),
        ("email.smtp_port", True),
        ("delivery.delete_after_send", "yes"),
        ("delivery.output_format", "mobi"),
        ("cron.time", 450),  # unquoted 7:30 in YAML 1.1
        ("partial_chapters.threshold_percent", 101),
        ("mangadex.removed_group_ids", "abc"),
    ])
    def test_value_problem(self, key, value):
        from memanga.config import config_value_problem
        assert config_value_problem(key, value)

    @pytest.mark.parametrize("data,expected", [
        ({}, []),
        ({"delivery": {"mode": "local"}}, []),
        ({"delivery": {"post_processing": "oops"}},
         ["delivery.post_processing: expected a section"]),
        ({"delivery": None}, ["delivery: expected a section"]),
        ({"email": [], "gui": 3},
         ["email: expected a section", "gui: expected a section"]),
        ({"manga": "not checked here"}, []),
    ])
    def test_section_problems(self, data, expected):
        from memanga.config import config_section_problems
        assert config_section_problems(data) == expected
