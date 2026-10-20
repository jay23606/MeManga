"""CBZ/ZIP archive writer and ComicInfo.xml metadata."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...scrapers.base import Chapter
from ..errors import DownloaderError


# BCP 47 "undetermined" language. EPUB requires a language, so it is
# written there when neither the manga entry nor its source names one;
# ComicInfo then omits LanguageISO instead.
UNDETERMINED_LANGUAGE = "und"
# BCP 47 shape: a 2-3 letter language plus optional subtags ("pt-BR").
_LANGUAGE_TAG_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*", re.ASCII)
_READING_DIRECTIONS = ("rtl", "ltr", "vertical")


def _language_tag(value: Any) -> Optional[str]:
    if isinstance(value, str) and _LANGUAGE_TAG_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def _reading_direction_value(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip().lower() in _READING_DIRECTIONS:
        return value.strip().lower()
    return None


def _manga_language(manga: Dict[str, Any], scraper=None) -> Optional[str]:
    """Language tag of a manga's pages, or None when unknown.

    The manga entry's ``language`` wins; otherwise the source's
    ``content_language`` is used, which only sources serving a single
    language set. Nothing is guessed.
    """
    return (_language_tag(manga.get("language"))
            or _language_tag(getattr(scraper, "content_language", None)))


def _reading_direction(manga: Dict[str, Any], scraper=None) -> Optional[str]:
    """``"rtl"``, ``"ltr"`` or ``"vertical"`` for a manga, or None when
    unknown: the manga entry's ``reading_direction`` wins over the
    source's."""
    return (_reading_direction_value(manga.get("reading_direction"))
            or _reading_direction_value(getattr(scraper, "reading_direction", None)))


def _parse_comicinfo_date(date_str: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """Best-effort parse of a chapter/source date into (year, month, day).

    Chapter dates arrive in many shapes across scrapers (ISO 8601 from API
    sources like ``publishAt``, free-form text elsewhere). Returns ``None``
    when nothing usable can be extracted so the caller simply omits the
    Year/Month/Day fields.
    """
    if not date_str:
        return None
    text = str(date_str).strip()
    if not text:
        return None

    # ISO 8601 first (covers the trailing "...Z" suffix common to API sources).
    try:
        iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(iso_text)
        return dt.year, dt.month, dt.day
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y",
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.year, dt.month, dt.day
        except ValueError:
            continue
    return None


def _build_comicinfo_xml(
    manga: Dict[str, Any],
    chapter: Chapter,
    page_count: int,
    scraper=None,
) -> bytes:
    """Build a ComicInfo.xml document from already-known metadata.

    Only fields MeManga already has on hand (the tracked manga config and
    the ``Chapter`` object) are written; no extra network requests are
    made. Optional fields with no data are omitted entirely. Serialization
    and XML escaping are handled by ElementTree.

    ``scraper`` is the chapter's source; its declared language and
    reading direction are used when the manga entry names none.
    ``LanguageISO`` is written only for a known language, and
    ``Manga=YesAndRightToLeft`` only for a known right-to-left title:
    manhwa and webtoons read left to right or top to bottom, and ComicInfo
    has no value for those, so an unknown or non-RTL title gets no
    ``Manga`` field. ``Count`` (the series' total number of chapters) is
    not written: MeManga does not know it, and readers such as Kavita and
    Komga take it as final and mark the series complete.
    """
    from xml.etree.ElementTree import Element, SubElement, tostring

    root = Element("ComicInfo", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
    })

    def add(tag: str, value: Any):
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        SubElement(root, tag).text = text

    chapter_title = getattr(chapter, "title", None)
    add("Title", chapter_title or f"Chapter {chapter.number}")
    add("Series", manga.get("title"))
    add("Number", chapter.number)

    add("Summary", manga.get("description"))

    parsed = _parse_comicinfo_date(getattr(chapter, "date", None))
    if parsed:
        year, month, day = parsed
        add("Year", year)
        add("Month", month)
        add("Day", day)

    add("Writer", manga.get("author"))
    # MangaDex credit: the scanlation group(s) that translated this chapter.
    add("Translator", ", ".join(getattr(chapter, "group_names", [])))

    # Web: prefer the specific chapter URL, fall back to the manga URL.
    add("Web", getattr(chapter, "url", "") or manga.get("url", ""))
    add("PageCount", page_count)
    add("LanguageISO", _manga_language(manga, scraper))
    if _reading_direction(manga, scraper) == "rtl":
        add("Manga", "YesAndRightToLeft")

    return tostring(root, encoding="utf-8", xml_declaration=True)


def _images_to_cbz(
    image_paths: List[Path],
    output_path: Path,
    comicinfo_xml: Optional[bytes] = None,
    credit: Optional[str] = None,
):
    """Convert a list of images to a CBZ (Comic Book ZIP) archive.

    Images are stored as-is (no re-encoding) with sequential names
    for correct reading order.

    When ``comicinfo_xml`` is provided it is written to the archive root as
    ``ComicInfo.xml`` so offline libraries and e-readers can read chapter
    metadata. Page names and order are unaffected.
    """
    import zipfile

    if not image_paths:
        raise DownloaderError("No images to archive")

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_STORED) as cbz:
        for i, img_path in enumerate(image_paths):
            ext = img_path.suffix.lower() or '.jpg'
            cbz.write(img_path, f"page_{i:03d}{ext}")
        if comicinfo_xml is not None:
            cbz.writestr("ComicInfo.xml", comicinfo_xml)
        if credit:
            cbz.writestr("Source.txt", credit + "\n")
