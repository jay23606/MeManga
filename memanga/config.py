"""
Configuration management for MeManga
"""

import os
import tempfile
import threading
import yaml
from pathlib import Path

from .perf import timed


class ConfigError(Exception):
    """The config file exists but cannot be loaded."""


class Config:
    """Manages configuration file.

    Thread-safe: a single ``_lock`` serializes both `_data` mutations from
    background threads (cover backfill, cover fetch on add) and the YAML
    write itself. Writes are atomic via tempfile + os.replace so a crash
    mid-save can never truncate the user's config.

    With ``tolerate_errors=True`` an unreadable or invalid config file
    does not raise: defaults are loaded, the problem is kept in
    ``load_error`` and ``save()`` refuses to write, so the broken file is
    never overwritten. The CLI uses this so `memanga doctor` can still
    report the problem.
    """

    def __init__(self, config_dir=None, tolerate_errors=False):
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path.home() / ".config" / "memanga"

        self.config_path = self.config_dir / "config.yaml"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tolerate_errors = tolerate_errors
        self.load_error = None
        self._data = self._load()
    
    def _load(self):
        """Load config from file."""
        self.load_error = None
        if self.config_path.exists():
            try:
                # Explicit UTF-8: titles are arbitrary Unicode and the
                # platform default encoding may not represent them.
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if not isinstance(data, dict):
                    raise ConfigError("config file does not contain a YAML mapping")
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ConfigError) as e:
                if not self._tolerate_errors:
                    raise
                self.load_error = str(e)
                return self._default_config()
            # Merge with defaults to ensure new fields exist
            defaults = self._default_config()
            for key, value in defaults.items():
                if key not in data:
                    data[key] = value
                elif isinstance(value, dict) and isinstance(data[key], dict):
                    for k, v in value.items():
                        if k not in data[key]:
                            data[key][k] = v
                elif isinstance(value, dict) and not isinstance(data[key], dict):
                    # User config has wrong type — replace with default
                    data[key] = value
            return data
        return self._default_config()
    
    def _default_config(self):
        """Return default configuration."""
        return {
            "manga": [],
            "delivery": {
                "mode": "local",  # "local" or "email"
                "download_dir": str(Path.home() / "Downloads" / "MeManga"),
                "delete_after_send": False,  # Delete file after sending to Kindle
                "output_format": "pdf",  # "pdf", "epub", "cbz", "zip", "jpg", "png", or "webp"
                "naming_template": "{title} - Chapter {chapter}",  # File naming pattern
                "post_processing": {
                    # Optional command run after each chapter's output
                    # file/folder is created. Disabled by default. See docs
                    # for available placeholders/env vars.
                    "enabled": False,
                    "command": "",
                    "fail_on_error": False,  # True marks the chapter failed
                },
            },
            "email": {
                "kindle_email": "",
                "sender_email": "",
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
                "app_password": "",
            },
            "cron": {
                "enabled": False,
                "time": "06:00",
            },
            "gui": {
                "sort_by": "title",
                "auto_check": True,
                "auto_check_interval": 3600,
                # Reader prefetch (issue #107). Off by default: while
                # reading a manual-mode manga, the reader quietly queues
                # the immediate next available chapter in the background.
                "reader_prefetch_next": False,
            },
            # Built-in reader behaviour (issue #104). Kept separate from
            # delivery.delete_after_send, which is tied to email delivery —
            # this is reader-driven local cleanup. Off by default: a chapter
            # stays on disk after it is read unless the user opts in.
            "reader": {
                "remove_after_read": False,
            },
            "mangadex": {
                "data_saver": False,
                "removed_group_ids": [],
            },
            # Partial-chapter tolerance (issue #86). Off by default: any
            # missing page still aborts the chapter and discards output.
            # When enabled, a chapter whose failure rate is within
            # threshold_percent is kept with only the pages that succeeded.
            "partial_chapters": {
                "enabled": False,
                "threshold_percent": 5,  # max % of pages allowed to fail
            },
        }
    
    def reload(self):
        """Re-read config from disk, discarding any in-memory changes.

        Useful when another process (e.g. the CLI) has updated the file
        and this instance needs to pick up the changes.
        """
        with self._lock:
            self._data = self._load()

    def get(self, key, default=None):
        """Get a config value."""
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value
    
    def set(self, key, value):
        """Set a config value."""
        keys = key.split(".")
        data = self._data
        for k in keys[:-1]:
            if k not in data or not isinstance(data[k], dict):
                data[k] = {}
            data = data[k]
        data[keys[-1]] = value
    
    @timed("config.save")
    def save(self):
        """Save config to file atomically. Thread-safe.

        Snapshots `_data` under the lock, then writes to a temp file and
        atomically replaces the live file. Concurrent saves serialize on
        the lock instead of fighting over the same file descriptor.
        """
        if self.load_error:
            raise ConfigError(
                f"Refusing to overwrite {self.config_path}, which could not "
                f"be loaded: {self.load_error}"
            )
        with self._lock:
            payload = yaml.dump(
                self._data, default_flow_style=False, allow_unicode=True,
            )

        fd, tmp_path = tempfile.mkstemp(dir=self.config_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, self.config_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def update_manga(self, title, mutator):
        """Atomically mutate the manga entry with the given title.

        ``mutator`` is called with the entry dict while the lock is held;
        any return value is ignored. Saves automatically when the mutator
        returns truthy (or returns ``None``, the common "I mutated in
        place" case). Returns True if an entry was found.
        """
        with self._lock:
            for entry in self._data.get("manga", []):
                if entry.get("title") == title:
                    result = mutator(entry)
                    # None == "I mutated in place"; False == "skip save"
                    needs_save = result is None or bool(result)
                    break
            else:
                return False
        if needs_save:
            self.save()
        return True
    
    def reset(self):
        """Reset to default config."""
        self._data = self._default_config()
        self.save()
    
    # Convenience properties
    @property
    def delivery_mode(self):
        return self.get("delivery.mode", "local")
    
    @property
    def download_dir(self):
        return Path(self.get("delivery.download_dir", str(self.config_dir / "downloads"))).expanduser()
    
    @property
    def email_enabled(self):
        # bool() matters: `and` would otherwise leak the kindle_email
        # string, which Qt setters like setChecked() reject (issue #55).
        return self.delivery_mode == "email" and bool(self.get("email.kindle_email"))
    
    @property
    def output_format(self):
        return self.get("delivery.output_format", "pdf")

    @property
    def partial_enabled(self):
        """Whether partial-chapter tolerance is turned on (issue #86)."""
        return bool(self.get("partial_chapters.enabled", False))

    @property
    def partial_threshold(self):
        """Max share of pages allowed to fail before a partial is refused,
        as a percentage clamped to [0, 100]. Falls back to 5 for garbage or
        out-of-range values persisted by an older/hand-edited config."""
        try:
            value = float(self.get("partial_chapters.threshold_percent", 5))
        except (TypeError, ValueError):
            return 5.0
        return max(0.0, min(100.0, value))

    @property
    def reader_prefetch_enabled(self):
        """Whether the reader prefetches the next manual-mode chapter
        while reading (issue #107)."""
        return bool(self.get("gui.reader_prefetch_next", False))


_KEYRING_SERVICE = "memanga"
_KEYRING_KEY = "app_password"


def get_app_password(cfg: Config) -> str:
    """Get app password from keyring, falling back to config file."""
    try:
        import keyring
        password = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY)
        if password:
            return password
    except Exception:
        pass
    return cfg.get("email.app_password", "")


def set_app_password(cfg: Config, password: str):
    """Store app password in keyring, falling back to config file. Saves config."""
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY, password)
        # Clear plaintext from config if keyring succeeded
        if cfg.get("email.app_password"):
            cfg.set("email.app_password", "")
            cfg.save()
        return
    except Exception:
        pass
    # Fallback: store in config
    cfg.set("email.app_password", password)
    cfg.save()


# ============================================================================
# Keys the `config get/set` commands accept (issue #353)
# ============================================================================

OUTPUT_FORMATS = ("pdf", "epub", "cbz", "zip", "jpg", "png", "webp")

_TRUE_WORDS = {"true", "yes", "on", "1"}
_FALSE_WORDS = {"false", "no", "off", "0"}


class UnknownConfigKey(KeyError):
    """The dotted key is not one `config get/set` knows."""


# kind: bool | int | float | choice | str | time | list
# secret: never printed; read_only: owned by another command.
CONFIG_KEYS = {
    "delivery.mode": {"kind": "choice", "choices": ("local", "email")},
    "delivery.download_dir": {"kind": "str", "required": True},
    "delivery.delete_after_send": {"kind": "bool"},
    "delivery.output_format": {"kind": "choice", "choices": OUTPUT_FORMATS},
    "delivery.naming_template": {"kind": "str", "required": True},
    "delivery.post_processing.enabled": {"kind": "bool"},
    "delivery.post_processing.command": {"kind": "str"},
    "delivery.post_processing.fail_on_error": {"kind": "bool"},
    "email.kindle_email": {"kind": "str"},
    "email.sender_email": {"kind": "str"},
    "email.smtp_server": {"kind": "str", "required": True},
    "email.smtp_port": {"kind": "int", "min": 1, "max": 65535},
    "email.app_password": {"kind": "str", "secret": True},
    "cron.enabled": {"kind": "bool", "read_only": "memanga cron install/remove"},
    "cron.time": {"kind": "time", "read_only": "memanga cron install --time HH:MM"},
    "gui.sort_by": {"kind": "choice", "choices": (
        "title", "last_updated", "recently_added", "chapter_count", "status")},
    "gui.auto_check": {"kind": "bool"},
    "gui.auto_check_interval": {"kind": "int", "min": 60},
    "gui.reader_prefetch_next": {"kind": "bool"},
    "reader.remove_after_read": {"kind": "bool"},
    "mangadex.data_saver": {"kind": "bool"},
    "mangadex.removed_group_ids": {
        "kind": "list", "read_only": "memanga config --mangadex-remove-group"},
    "partial_chapters.enabled": {"kind": "bool"},
    "partial_chapters.threshold_percent": {"kind": "float", "min": 0, "max": 100},
}


def config_key_spec(key):
    """Return the schema entry for ``key``; raise UnknownConfigKey."""
    try:
        return CONFIG_KEYS[key]
    except KeyError:
        raise UnknownConfigKey(key) from None


def _is_hhmm(value) -> bool:
    parts = str(value).split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    return 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59


def _range_problem(spec, value):
    low, high = spec.get("min"), spec.get("max")
    if low is not None and value < low:
        return f"must be at least {low:g}"
    if high is not None and value > high:
        return f"must be at most {high:g}"
    return None


def parse_config_value(key, text):
    """Turn the command-line ``text`` for ``key`` into the stored value.

    Raises UnknownConfigKey for keys outside CONFIG_KEYS and ValueError
    with a short reason for values the key does not accept.
    """
    spec = config_key_spec(key)
    kind = spec["kind"]
    text = str(text)
    if kind == "bool":
        word = text.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
        raise ValueError("expected true or false")
    if kind in ("int", "float"):
        try:
            value = int(text.strip()) if kind == "int" else float(text.strip())
        except ValueError:
            raise ValueError("expected a whole number" if kind == "int"
                             else "expected a number") from None
        if kind == "float" and value != value:  # NaN
            raise ValueError("expected a number")
        problem = _range_problem(spec, value)
        if problem:
            raise ValueError(problem)
        return value
    if kind == "choice":
        word = text.strip().lower()
        if word not in spec["choices"]:
            raise ValueError("expected one of: " + ", ".join(spec["choices"]))
        return word
    if kind == "time":
        if not _is_hhmm(text.strip()):
            raise ValueError("expected HH:MM")
        hour, minute = text.strip().split(":")
        return f"{int(hour):02d}:{int(minute):02d}"
    if kind == "str":
        if spec.get("required") and not text.strip():
            raise ValueError("must not be empty")
        return text
    raise ValueError("cannot be set from the command line")


def config_section_problems(data):
    """Sections on the way to a known key that are not mappings.

    ``data`` is the raw YAML mapping, before defaults are merged in:
    loading quietly replaces or skips a section that is not a mapping,
    so its values would never be checked.
    """
    problems = []
    for key in CONFIG_KEYS:
        parts = key.split(".")
        node = data
        for depth, part in enumerate(parts[:-1], start=1):
            if part not in node:
                break
            node = node[part]
            if not isinstance(node, dict):
                problem = f"{'.'.join(parts[:depth])}: expected a section"
                if problem not in problems:
                    problems.append(problem)
                break
    return problems


def config_value_problem(key, value):
    """Why the stored ``value`` is not valid for ``key``, or None.

    Used after a hand edit, where values arrive already typed by YAML.
    """
    spec = config_key_spec(key)
    kind = spec["kind"]
    if kind == "bool":
        return None if isinstance(value, bool) else "expected true or false"
    if kind in ("int", "float"):
        types = (int,) if kind == "int" else (int, float)
        if isinstance(value, bool) or not isinstance(value, types):
            return "expected a whole number" if kind == "int" else "expected a number"
        return _range_problem(spec, value)
    if kind == "choice":
        if value not in spec["choices"]:
            return "expected one of: " + ", ".join(spec["choices"])
        return None
    if kind == "time":
        return None if isinstance(value, str) and _is_hhmm(value) else "expected HH:MM"
    if kind == "list":
        return None if isinstance(value, list) else "expected a list"
    if not isinstance(value, str):
        return "expected text"
    if spec.get("required") and not value.strip():
        return "must not be empty"
    return None
