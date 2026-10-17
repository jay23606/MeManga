"""Shell completion scripts generated from the CLI parser (issue #248).

`memanga completion SHELL` prints a static completion script. Only bash
is supported; SHELL stays an argument so other shells can be added once
they can be tested. The script is built from the same argparse parser the CLI runs, so commands,
aliases, options and choices can't drift from the real interface. The
scripts only complete the command line's shape plus fixed word lists
attached with `suggest()`; nothing calls back into memanga on TAB.

Tracked manga titles are not completed (issue #260): that needs a
callback into memanga on every TAB, the --config-dir rules redone in the
shell, and quoting for free-form titles that `compgen -W` would expand.
`#N` from `memanga list` is the short way to name a manga.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

SHELLS = ("bash",)
PROG = "memanga"

# Words written unquoted into the scripts must be plain tokens.
_SAFE_WORD = re.compile(r"[A-Za-z0-9_.:+-]+")


@dataclass
class _Option:
    strings: List[str]
    takes_value: bool
    choices: List[str]
    directory: bool


@dataclass
class _Node:
    """One parser: the root, a command, or a command's action."""

    name: str
    aliases: List[str]
    options: List[_Option] = field(default_factory=list)
    choices: List[str] = field(default_factory=list)
    has_positionals: bool = False
    children: List["_Node"] = field(default_factory=list)


def suggest(action: argparse.Action,
            words: Callable[[], Iterable[str]]) -> argparse.Action:
    """Complete ``action``'s value with ``words()`` (issue #260).

    Unlike argparse choices, the words don't limit what the option
    accepts. ``words`` is only called when a script is generated, so the
    CLI's start-up cost is unchanged. Words that aren't plain tokens are
    left out of the script.
    """
    action.completion_words = words
    return action


def _words(values) -> List[str]:
    return [str(v) for v in values or () if _SAFE_WORD.fullmatch(str(v))]


def _suggestions(action: argparse.Action) -> List[str]:
    """``action``'s choices, or the words given to `suggest()`."""
    if action.choices:
        return _words(action.choices)
    words = getattr(action, "completion_words", None)
    return _words(words()) if words else []


def _node(parser: argparse.ArgumentParser, name: str = "",
          aliases: Optional[List[str]] = None) -> _Node:
    node = _Node(name=name, aliases=aliases or [])
    for action in parser._actions:
        if action.help == argparse.SUPPRESS:
            continue
        if isinstance(action, argparse._SubParsersAction):
            node.children = _children(action)
        elif action.option_strings:
            metavar = action.metavar if isinstance(action.metavar, str) else ""
            node.options.append(_Option(
                strings=_words(action.option_strings),
                takes_value=action.nargs != 0,
                choices=_suggestions(action),
                directory=metavar == "DIR",
            ))
        else:
            node.has_positionals = True
            node.choices.extend(_suggestions(action))
    return node


def _children(subparsers: argparse._SubParsersAction) -> List[_Node]:
    """Visible subcommands in definition order, with their aliases."""
    names_by_parser: Dict[int, List[str]] = {}
    for name, sub in subparsers.choices.items():
        names_by_parser.setdefault(id(sub), []).append(name)
    children = []
    for pseudo in subparsers._choices_actions:
        sub = subparsers.choices[pseudo.dest]
        names = names_by_parser[id(sub)]
        aliases = [n for n in names if n != pseudo.dest]
        children.append(_node(sub, pseudo.dest, aliases))
    return children


def _all_options(node: _Node) -> List[str]:
    return [s for opt in node.options for s in opt.strings]


def _value_options(node: _Node) -> List[str]:
    return [s for opt in node.options if opt.takes_value for s in opt.strings]


def _positional_words(node: _Node) -> List[str]:
    """Words offered when the current word is not an option."""
    words = list(node.choices)
    for child in node.children:
        words.extend([child.name, *child.aliases])
    if not words and not node.has_positionals:
        # Nothing positional to complete: offer the options instead of
        # falling back to file names.
        words = _all_options(node)
    return words


# Puts `--opt=` back in front of each value when bash kept `--opt=value`
# as one word (COMP_WORDBREAKS without "=").
_ADD_PREFIX = 'COMPREPLY=("${COMPREPLY[@]/#/$prefix}")'


def _bash(root: _Node) -> str:
    commands = root.children
    global_values = "|".join(_value_options(root)) or "--"

    alias_cases = "".join(
        f'        {"|".join([c.name, *c.aliases])}) cmd="{c.name}" ;;\n'
        for c in commands if c.aliases
    )

    action_cases = ""
    for c in commands:
        if not c.children:
            continue
        names = "|".join(n for a in c.children for n in [a.name, *a.aliases])
        action_cases += (
            f'        {c.name})\n'
            f'            for ((i++; i < COMP_CWORD; i++)); do\n'
            f'                case "${{COMP_WORDS[i]}}" in\n'
            f'                    {names}) action="${{COMP_WORDS[i]}}"; break ;;\n'
            f'                esac\n'
            f'            done ;;\n'
        )

    # (key, node) for the root, every command and every action.
    scopes = [("/", root)]
    for c in commands:
        scopes.append((f"{c.name}/", c))
        for a in c.children:
            scopes.extend((f"{c.name}/{n}", a) for n in [a.name, *a.aliases])

    value_cases = ""
    word_cases = ""
    for key, node in scopes:
        for opt in node.options:
            if not opt.takes_value or not opt.strings:
                continue
            pattern = "|".join(f"{key}/{s}" for s in opt.strings)
            if opt.choices:
                reply = (f'COMPREPLY=($(compgen -W "{" ".join(opt.choices)}" -- "$cur")); '
                         + _ADD_PREFIX)
            elif opt.directory:
                reply = 'COMPREPLY=($(compgen -d -- "$cur")); ' + _ADD_PREFIX
            else:
                reply = ":"  # free value; bash falls back to file names
            value_cases += f"        {pattern}) {reply}; return ;;\n"
        word_cases += (
            f'        {key}) opts="{" ".join(_all_options(node))}"; '
            f'words="{" ".join(_positional_words(node))}" ;;\n'
        )

    return f"""# bash completion for {PROG}
# Load it with:  source <({PROG} completion bash)
_{PROG}() {{
    local cur prev cmd action i opts words prefix word_cur word_prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    cmd=""
    action=""
    opts=""
    words=""
    prefix=""
    for ((i = 1; i < COMP_CWORD; i++)); do
        case "${{COMP_WORDS[i]}}" in
            {global_values})
                # The value is the next word, or the one after a split "=".
                ((i++))
                if [[ "${{COMP_WORDS[i]}}" == "=" ]]; then ((i++)); fi ;;
            -*) ;;
            *) cmd="${{COMP_WORDS[i]}}"; break ;;
        esac
    done
    case "$cmd" in
{alias_cases}    esac
    case "$cmd" in
{action_cases}    esac
    # The value of `--opt=value`: bash splits it into `--opt`, `=` and
    # `value` by default (COMP_WORDBREAKS has "="), else keeps one word.
    word_cur="$cur"
    word_prev="$prev"
    if [[ "$cur" == "=" ]]; then
        cur=""
    elif [[ "$prev" == "=" ]] && ((COMP_CWORD >= 2)); then
        prev="${{COMP_WORDS[COMP_CWORD-2]}}"
    elif [[ "$cur" == --*=* ]]; then
        prev="${{cur%%=*}}"
        prefix="$prev="
        cur="${{cur#*=}}"
    fi
    case "$cmd/$action/$prev" in
{value_cases}    esac
    cur="$word_cur"
    prev="$word_prev"
    case "$cmd/$action" in
{word_cases}        *) return ;;
    esac
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "$opts" -- "$cur"))
    else
        COMPREPLY=($(compgen -W "$words" -- "$cur"))
    fi
}}
complete -o default -F _{PROG} {PROG}
"""


_GENERATORS = {"bash": _bash}


def script(shell: str, parser: argparse.ArgumentParser) -> str:
    """Return the completion script for ``shell`` built from ``parser``."""
    try:
        generate = _GENERATORS[shell]
    except KeyError:
        raise ValueError(f"unsupported shell: {shell}") from None
    return generate(_node(parser, parser.prog))
