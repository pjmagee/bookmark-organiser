"""Enrich bookmarks with metadata from the linked page."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from html import unescape
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

if TYPE_CHECKING:  # runtime import kept minimal
    from collections.abc import Iterable

    from .models import BookmarkRecord

LOGGER = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

# Maximum number of keyword tags retained per page to avoid bloating LLM prompts.
TAG_TRIM_LIMIT = 20

_SMALL_BATCH_CUTOFF = 3


class MetadataEnrichMode(Enum):
    """Strategy for metadata enrichment.

    ALL: attempt enrichment for every record.
    ONLY_MISSING: enrich only those without existing metadata (title/description/tags).
    """

    ALL = "all"
    ONLY_MISSING = "only-missing"


def enrich_with_metadata(
    records: Iterable[BookmarkRecord],
    timeout: float = 8.0,
    workers: int = 12,
    mode: MetadataEnrichMode = MetadataEnrichMode.ALL,
) -> list[BookmarkRecord]:
    """Fetch metadata for each bookmark and update the record in-place.

    Uses a thread pool for concurrency (IO-bound requests). Falls back gracefully on errors.
    """
    target: list[BookmarkRecord] = list(records)
    session = requests.Session()
    session.headers.update(HEADERS)

    def _should_skip(r: BookmarkRecord) -> bool:
        if mode is MetadataEnrichMode.ALL:
            return False
        return any([
            r.metadata.title,
            r.metadata.description,
            r.metadata.tags,
        ])

    def _work(r: BookmarkRecord) -> BookmarkRecord:
        if _should_skip(r):
            return r
        try:
            _populate_single_record(session, r, timeout)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to fetch metadata for %s: %s", r.url, exc)
        return r

    # If very small input, avoid thread overhead.
    if len(target) <= _SMALL_BATCH_CUTOFF:
        return [_work(r) for r in target]

    enriched: list[BookmarkRecord] = [None] * len(target)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(_work, r): idx for idx, r in enumerate(target)}
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                enriched[idx] = future.result()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Worker failed for bookmark index %d: %s", idx, exc)
                enriched[idx] = target[idx]

    # Filter out potential None placeholders (defensive) and return in original order.
    return list(enriched)


def _populate_single_record(
    session: requests.Session,
    record: BookmarkRecord,
    timeout: float,
) -> None:
    response = _request_with_fallback(session, record.url, timeout)
    if response is None:
        return
    content_type = response.headers.get("Content-Type", "")
    if "text/html" not in content_type.lower():
        LOGGER.debug("Skipping non-HTML content for %s (content-type=%s)", record.url, content_type)
        return

    soup = BeautifulSoup(response.text, "html.parser")

    title = _first_non_empty(
        soup.find("meta", property="og:title"),
        soup.find("meta", attrs={"name": "twitter:title"}),
        soup.title,
    )
    if title:
        record.metadata.title = unescape(title)

    description = _first_non_empty(
        soup.find("meta", property="og:description"),
        soup.find("meta", attrs={"name": "description"}),
        soup.find("meta", attrs={"name": "twitter:description"}),
    )
    if description:
        record.metadata.description = unescape(description)

    keywords = soup.find("meta", attrs={"name": "keywords"})
    if keywords:
        content_value = keywords.get("content")
        if isinstance(content_value, str):
            raw_tags = [tag.strip() for tag in content_value.split(",") if tag.strip()]
            # Trim excessively long tag lists to reduce LLM prompt size.
            if len(raw_tags) > TAG_TRIM_LIMIT:
                LOGGER.debug(
                    "Trimming %d tags to %d for %s", len(raw_tags), TAG_TRIM_LIMIT, record.url,
                )
                raw_tags = raw_tags[:TAG_TRIM_LIMIT]
            record.metadata.tags = raw_tags


def _request_with_fallback(
    session: requests.Session,
    url: str,
    timeout: float,
) -> requests.Response | None:
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in {401, 403, 407}:
            fallback_url = _root_url(url)
            if fallback_url and fallback_url != url:
                LOGGER.debug(
                    "Permission error (%s) for %s; retrying with root %s",
                    status,
                    url,
                    fallback_url,
                )
                try:
                    fallback_response = session.get(
                        fallback_url,
                        timeout=timeout,
                        allow_redirects=True,
                    )
                    fallback_response.raise_for_status()
                except requests.RequestException as fallback_exc:  # narrow exception
                    LOGGER.debug(
                        "Fallback request to %s failed: %s",
                        fallback_url,
                        fallback_exc,
                    )
                else:
                    return fallback_response
        return None
    except requests.RequestException:
        return None
    else:
        return response


def _root_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _first_non_empty(*tags: object) -> str:
    for candidate in tags:
        if candidate is None:
            continue
        getter = getattr(candidate, "get", None)
        if callable(getter):
            content = getter("content")
            if isinstance(content, str):
                trimmed = content.strip()
                if trimmed:
                    return trimmed
        text_getter = getattr(candidate, "get_text", None)
        if callable(text_getter):
            text = text_getter(strip=True)
            if isinstance(text, str) and text:
                return text
    return ""
