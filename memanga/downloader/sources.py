"""Source configuration: which sites a manga is tracked on and how their
scrapers are set up."""

from typing import Any, Dict, List
from urllib.parse import urlparse

from ..scrapers import list_supported_sources
from ..scrapers.base import Chapter


class ChapterWithSource(Chapter):
    """Chapter with source info for multi-source support."""
    
    def __init__(self, chapter: Chapter, source: str, source_url: str, is_backup: bool = False):
        super().__init__(
            number=chapter.number,
            title=chapter.title,
            url=chapter.url,
            date=chapter.date,
        )
        self.source = source
        self.source_url = source_url
        self.is_backup = is_backup
        self.group_ids = getattr(chapter, "group_ids", [])
        self.group_names = getattr(chapter, "group_names", [])


def _configure_mangadex(scraper, state=None):
    """Apply the user's MangaDex choices to this scraper instance."""
    from ..config import Config
    from ..scrapers.mangadex import MangaDexScraper, removed_group_ids

    if isinstance(scraper, MangaDexScraper):
        config = Config(config_dir=state.config_dir if state else None)
        scraper.data_saver = bool(config.get("mangadex.data_saver", False))
        scraper.removed_group_ids = removed_group_ids(config)


def _extract_source(url: str) -> str:
    """Extract source domain from URL."""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _get_sources_from_manga(manga: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Get list of sources from manga config.
    Supports both old format (single source) and new format (sources array).
    
    Returns: List of dicts with 'source' and 'url' keys
    """
    # New format: sources array
    if "sources" in manga:
        sources = []
        for s in manga["sources"]:
            if isinstance(s, dict):
                url = s.get("url", "")
                if not url:
                    # Skip blank-URL entries — they'd crash downstream
                    # in get_chapters("") with no useful error.
                    continue
                source = s.get("source") or _extract_source(url)
                sources.append({"source": source, "url": url})
            elif isinstance(s, str):
                if not s:
                    continue
                # Just a URL string
                sources.append({"source": _extract_source(s), "url": s})
        return sources
    
    # Old format: single source/url
    return [{
        "source": manga.get("source", ""),
        "url": manga.get("url", ""),
    }]


def get_supported_sources() -> List[str]:
    """Get list of supported source domains."""
    return list_supported_sources()
