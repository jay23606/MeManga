"""Managed download folders: rename, locate and delete a manga's files."""

import os
import shutil
from pathlib import Path
from typing import Optional

from .naming import _sanitize_filename


def rename_manga_downloads(download_dir, old_title: str, new_title: str) -> bool:
    """Move a manga's downloaded files when the manga is renamed.

    Downloads live under ``<download_dir>/<sanitized title>/`` and the
    default naming template embeds the title in each file
    (``{title} - Chapter N``). The reader derives both the folder and the
    file name from the manga's current title, so a rename leaves the old
    files unreachable — the chapter shows as downloaded but "Read" finds
    nothing.

    This moves the per-manga folder to the new title and rewrites the
    title prefix on any contained file or folder name so the reader's
    lookup matches again. Existing files at the destination are left
    untouched rather than overwritten.

    Returns ``True`` if anything was moved, ``False`` if there was nothing
    to migrate (no prior downloads, or the sanitized title is unchanged).
    """
    old_safe = _sanitize_filename(old_title)
    new_safe = _sanitize_filename(new_title)
    if not old_safe or not new_safe or old_safe == new_safe:
        return False

    base = Path(download_dir)
    old_dir = base / old_safe
    if not old_dir.is_dir():
        return False

    new_dir = base / new_safe
    new_dir.mkdir(parents=True, exist_ok=True)

    moved = False
    for entry in list(old_dir.iterdir()):
        name = entry.name
        # Swap the leading title prefix (e.g. "Old - Chapter 5.cbz" ->
        # "New - Chapter 5.cbz"). Names that don't start with the title
        # (e.g. image-format "Chapter 5" folders) are moved as-is.
        if name.startswith(old_safe):
            name = new_safe + name[len(old_safe):]
        target = new_dir / name
        if target.exists():
            continue
        shutil.move(str(entry), str(target))
        moved = True

    # Drop the old folder if it's now empty; leave it if collisions
    # forced some files to stay behind.
    try:
        old_dir.rmdir()
    except OSError:
        pass

    return moved


class UnsafeDownloadPathError(Exception):
    """A manga's download folder can't be deleted safely."""


def find_manga_downloads(download_dir, title: str) -> Optional[Path]:
    """Return the manga's managed download folder, or ``None`` if absent.

    Only ``<download_dir>/<sanitized title>`` is considered — the folder
    ``download_chapter`` writes to. Raises ``UnsafeDownloadPathError``
    when that entry is a symlink, not a directory, or resolves anywhere
    but directly inside the download root (issue #349).
    """
    root = Path(download_dir).expanduser()
    if not root.is_dir():
        return None
    name = _sanitize_filename(title)
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        raise UnsafeDownloadPathError(f"unusable folder name for {title!r}")
    target = root / name
    if not os.path.lexists(target):
        return None
    if target.is_symlink():
        raise UnsafeDownloadPathError(f"{target} is a symlink")
    if not target.is_dir():
        raise UnsafeDownloadPathError(f"{target} is not a directory")
    if target.resolve(strict=True).parent != root.resolve(strict=True):
        raise UnsafeDownloadPathError(f"{target} is outside {root}")
    return target


def delete_manga_downloads(download_dir, title: str) -> Optional[Path]:
    """Delete the manga's managed download folder; return what was removed.

    Returns ``None`` when there is nothing to delete. Symlinks inside the
    folder are unlinked, never followed.
    """
    target = find_manga_downloads(download_dir, title)
    if target is None:
        return None
    shutil.rmtree(target)
    return target
