"""CBZ/ZIP archive writer and ComicInfo.xml metadata."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...scrapers.base import Chapter
from ..errors import DownloaderError


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
) -> bytes:
    """Build a ComicInfo.xml document from already-known metadata.

    Only fields MeManga already has on hand (the tracked manga config and
    the ``Chapter`` object) are written; no extra network requests are
    made. Optional fields with no data are omitted entirely. Serialization
    and XML escaping are handled by ElementTree.
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
