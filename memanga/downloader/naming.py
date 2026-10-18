"""File and chapter naming helpers shared by the download engine."""


def _get_extension(url: str) -> str:
    """Get file extension from URL path (not substring match)."""
    from posixpath import splitext
    path = url.split('?')[0].split('#')[0]
    _, ext = splitext(path)
    ext = ext.lower()
    if ext in ('.webp', '.png', '.jpg', '.jpeg', '.gif'):
        return ext
    return '.jpg'


def _sanitize_filename(name: str) -> str:
    """Make a string safe for use as filename."""
    # Remove/replace invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')
    name = name.strip()[:100]  # Limit length
    # Windows trims trailing dots/spaces from path components, so remove
    # them explicitly after truncation to keep generated paths stable.
    name = name.rstrip(". ")
    # Never yield a dot-only ('.', '..') or empty result; those are
    # directory references, not usable path components.
    if not name.strip('.'):
        return 'untitled'
    return name


def _format_chapter_number(chapter_num: str) -> str:
    """
    Format chapter number for proper alphabetical sorting.
    
    - Regular chapters: stay as is (2, 3, 4...)
    - Part chapters: "2 Part 1" -> "2.01", "2 Part 2" -> "2.02"
    - Decimal chapters: stay as is (2.5 -> 2.5)
    
    Sorting result: 2 < 2.01 < 2.02 < 2.5 < 3 (correct!)
    """
    import re
    
    # Check for "Part X" pattern (case insensitive)
    part_match = re.match(r'^(\d+)\s*[Pp]art\s*(\d+)(.*)$', chapter_num)
    if part_match:
        main_num = part_match.group(1)
        part_num = part_match.group(2).zfill(2)  # Zero-pad to 2 digits
        rest = part_match.group(3)  # Any trailing text
        return f"{main_num}.{part_num}{rest}"
    
    # Return as-is for regular and decimal chapters
    return chapter_num
