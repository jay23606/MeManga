"""#213: the Playwright scrapers use the ``Stealth`` class from playwright-stealth
2.x. Dependency declarations must not allow the 1.x releases, which lack it."""
import re
from pathlib import Path

from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]
COMPATIBLE = SpecifierSet(">=2.0,<3")


def _declared_spec(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    pattern = r"""["']?playwright-stealth([<>=!~][^"',\s]*(?:\s*,\s*[<>=!~][^"',\s]*)*)"""
    match = re.search(pattern, text)
    assert match, f"playwright-stealth not declared in {name}"
    return SpecifierSet(match.group(1).strip())


def test_playwright_stealth_declarations_require_2x():
    for name in ("pyproject.toml", "requirements.txt", "requirements-docker.txt"):
        spec = _declared_spec(name)
        assert not spec.contains("1.0.6"), f"{name} allows {spec} (includes 1.0.6)"
        assert spec.contains("2.0.3"), f"{name} excludes 2.x: {spec}"


def test_playwright_stealth_lock_pins_2x():
    # Release builds install from the lockfile (see build_app.py).
    spec = _declared_spec("requirements-lock.txt")
    pins = [s.version for s in spec if s.operator == "=="]
    assert len(pins) == 1, f"requirements-lock.txt must pin exactly: {spec}"
    assert COMPATIBLE.contains(pins[0]), f"requirements-lock.txt pins {pins[0]}, need {COMPATIBLE}"
