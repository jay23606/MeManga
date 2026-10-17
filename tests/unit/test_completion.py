"""Tests for memanga.completion shell scripts (#248).

The bash tests source the generated script and call the completion
function with a synthetic COMP_WORDS, so they check what a user sees on
TAB, not just the script text. Only bash is supported.
"""

import shutil
import subprocess
import sys

import pytest

from memanga import cli, completion

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs bash",
)


@pytest.fixture
def parser(monkeypatch):
    monkeypatch.delenv("MEMANGA_CLI_ONLY", raising=False)
    return cli.build_parser()


def _complete(script, *words):
    """Return bash's COMPREPLY for the command line ``memanga *words``.

    The last word is the one being completed ("" for a fresh word).
    """
    line = ["memanga", *words]
    quoted = " ".join("'" + w.replace("'", "'\\''") + "'" for w in line)
    program = (
        f"{script}\n"
        f"COMP_WORDS=({quoted})\n"
        f"COMP_CWORD={len(line) - 1}\n"
        "_memanga\n"
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    result = subprocess.run(["bash", "--norc", "--noprofile", "-c", program],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return [w for w in result.stdout.splitlines() if w]


class TestScript:
    def test_only_bash_is_offered(self):
        assert completion.SHELLS == ("bash",)

    def test_bash_script(self, parser):
        out = completion.script("bash", parser)
        assert out.endswith("\n")
        assert "complete -o default -F _memanga memanga" in out

    @pytest.mark.parametrize("shell", ["zsh", "fish", "powershell"])
    def test_unsupported_shell_raises(self, parser, shell):
        with pytest.raises(ValueError):
            completion.script(shell, parser)

    def test_hidden_options_are_left_out(self, monkeypatch):
        monkeypatch.setenv("MEMANGA_CLI_ONLY", "1")
        assert "gui" not in completion.script("bash", cli.build_parser())


@needs_bash
class TestBashCompletion:
    @pytest.fixture
    def script(self, parser):
        return completion.script("bash", parser)

    def test_bash_syntax(self, script):
        result = subprocess.run(["bash", "-n"], input=script,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr

    def test_commands(self, script):
        reply = _complete(script, "")
        for name in ("list", "ls", "check", "completion", "failed", "doctor"):
            assert name in reply
        assert _complete(script, "ch") == ["check"]

    def test_global_options(self, script):
        reply = _complete(script, "--")
        assert {"--version", "--config-dir", "--help"} <= set(reply)

    def test_command_after_global_option_value(self, script):
        reply = _complete(script, "--config-dir", "/tmp/x", "check", "--dry")
        assert reply == ["--dry-run"]

    def test_alias_uses_command_options(self, script):
        assert "--json" in _complete(script, "ls", "--")

    def test_option_choices(self, script):
        assert _complete(script, "check", "--format", "c") == ["cbz"]
        assert set(_complete(script, "list", "--status", "")) == {
            "reading", "on-hold", "dropped", "completed"}

    def test_positional_choices(self, script):
        assert _complete(script, "completion", "") == ["bash"]
        assert _complete(script, "set", "Some Title", "on") == ["on-hold"]

    def test_nested_actions_and_their_options(self, script):
        assert "retry" in _complete(script, "failed", "")
        assert "--json" in _complete(script, "config", "show", "--")
        assert _complete(script, "failed", "retry", "--j") == []

    # `--opt=value`: bash splits it into `--opt`, `=`, `value` by default
    # (COMP_WORDBREAKS has "="); without "=" there it stays one word.
    def test_option_equals_value_split(self, script):
        assert _complete(script, "check", "--format", "=", "c") == ["cbz"]
        assert _complete(script, "list", "--sort", "=", "") == [
            "list", "title", "status", "last-chapter", "downloaded"]

    def test_option_equals_value_right_after_equals(self, script):
        assert "cbz" in _complete(script, "check", "--format", "=")

    def test_option_equals_value_one_word(self, script):
        assert _complete(script, "check", "--format=c") == ["--format=cbz"]

    @pytest.mark.parametrize("words", [
        ("--config-dir", "=", "/tmp", "check", "--dr"),
        ("--config-dir=/tmp", "check", "--dr"),
        ("--config-dir", "/tmp", "check", "--dr"),
    ])
    def test_global_option_value_before_command(self, script, words):
        assert _complete(script, *words) == ["--dry-run"]

    def test_global_option_equals_value_then_command_value(self, script):
        assert _complete(script, "--config-dir", "=", "/tmp", "check",
                         "--format", "=", "e") == ["epub"]

    def test_non_value_option_word_is_left_alone(self, script):
        assert _complete(script, "list", "--js") == ["--json"]
        assert _complete(script, "--config-dir=/tmp", "") != []

    def test_free_value_offers_no_words(self, script):
        # A title value falls back to bash's default (file names), never
        # to option names.
        assert not any(w.startswith("-") for w in
                       _complete(script, "add", "--title", ""))
