"""Parse a Chrome/Brave exported bookmark file into structured records."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

from bs4 import BeautifulSoup, Tag

from .models import BookmarkMetadata, BookmarkRecord

LOGGER = logging.getLogger(__name__)


def parse_bookmark_html(html_path: Path) -> list[BookmarkRecord]:
    """Parse a Chrome/Brave exported bookmark file into structured records."""
    LOGGER.debug("Parsing bookmark export from %s", html_path)
    html_text = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_text, "html.parser")

    root_dl = soup.find("dl")
    if root_dl is None:
        msg = "Bookmark export is missing <DL> root element"
        raise ValueError(msg)

    records: list[BookmarkRecord] = []
    for anchor in root_dl.find_all("a"):
        record = _record_from_anchor(anchor)
        if record is not None:
            records.append(record)

    LOGGER.info("Extracted %d bookmark entries", len(records))
    return records


def _record_from_anchor(anchor: Tag) -> BookmarkRecord | None:
    href_value = anchor.get("href")
    if not isinstance(href_value, str):
        LOGGER.debug("Skipping anchor without textual href")
        return None

    href = href_value.strip()
    if not href:
        LOGGER.debug("Skipping anchor with empty href")
        return None

    title = anchor.get_text(strip=True)
    location_segments = _compute_location_segments(anchor)
    location = "/".join(location_segments)

    metadata = BookmarkMetadata()
    return BookmarkRecord(
        title_before=title,
        url=href,
        location_before=location,
        metadata=metadata,
    )


def _compute_location_segments(anchor: Tag) -> list[str]:
    segments: list[str] = []
    current_dl = anchor.find_parent("dl")

    while current_dl is not None:
        dt_container = current_dl.find_parent("dt")
        if dt_container is None:
            break

        header = dt_container.find("h3")
        if header is not None:
            folder_name = header.get_text(strip=True)
            if folder_name:
                segments.append(folder_name)

        current_dl = dt_container.find_parent("dl")

    segments.reverse()
    return segments
