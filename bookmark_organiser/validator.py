"""Bookmark reorganisation validation utilities."""

from __future__ import annotations

import collections
import logging
from typing import TYPE_CHECKING

from .models import BookmarkEntryListModel, BookmarkEntryModel
from .parser import parse_bookmark_html

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable
    from pathlib import Path

    from .models import BookmarkRecord

LOGGER = logging.getLogger(__name__)


def validate_reorganisation(
    original_records: Iterable[BookmarkRecord],
    reorganised_path: Path,
    json_records_path: Path | None = None,
) -> None:
    """Validate reorganised output against originals."""
    original_list = list(original_records)
    reorganised_records = parse_bookmark_html(reorganised_path)
    _assert_counts(original_list, reorganised_records)
    _assert_url_multiset(original_list, reorganised_records)
    _assert_locations(reorganised_records)
    if json_records_path and json_records_path.exists():
        _assert_json_consistency(json_records_path, reorganised_records)
    LOGGER.info("Validation successful: all %d bookmarks accounted for", len(original_list))


def _assert_counts(original: list[BookmarkRecord], reorganised: list[BookmarkRecord]) -> None:
    if len(original) != len(reorganised):
        msg = (
            "Mismatch between original and reorganised bookmark counts: "
            f"{len(original)} vs {len(reorganised)}"
        )
        raise ValueError(msg)


def _assert_url_multiset(original: list[BookmarkRecord], reorganised: list[BookmarkRecord]) -> None:
    original_urls = collections.Counter(r.url for r in original)
    new_urls = collections.Counter(r.url for r in reorganised)
    if original_urls != new_urls:
        missing = original_urls - new_urls
        extras = new_urls - original_urls
        msg = "URL mismatch detected after reorganisation"
        raise ValueError(msg, {"missing": dict(missing), "extra": dict(extras)})


def _assert_locations(reorganised: list[BookmarkRecord]) -> None:
    empty_locations = [
        r for r in reorganised if not r.location_after.strip() and not r.location_before.strip()
    ]
    if empty_locations:
        msg = (
            "Found "
            f"{len(empty_locations)} records with empty location_after (and no original location)."
        )
        raise ValueError(msg)


def _assert_json_consistency(json_path: Path, reorganised: list[BookmarkRecord]) -> None:
    """Validate that the JSON record file matches the reorganised HTML output.

    Strong typing: we parse the entire file through a root Pydantic list model.
    Any invalid entry aborts validation immediately with a clear error.
    """
    raw_text = json_path.read_text(encoding="utf-8")
    try:
        list_model = BookmarkEntryListModel.model_validate_json(raw_text)
    except Exception as exc:
        msg = f"Invalid JSON bookmark records file: {exc}"
        raise ValueError(msg) from exc

    parsed_models: list[BookmarkEntryModel] = list_model.__root__

    if len(parsed_models) != len(reorganised):
        msg = (
            "JSON model count does not match reorganised HTML count: "
            f"{len(parsed_models)} vs {len(reorganised)}"
        )
        raise ValueError(msg)

    json_urls = collections.Counter(m.url for m in parsed_models)
    reorganised_urls = collections.Counter(r.url for r in reorganised)
    if json_urls != reorganised_urls:
        msg = "Mismatch between JSON reorganised URLs and HTML output URLs"
        raise ValueError(msg)

    # Index continuity intentionally not enforced: the persisted JSON schema
    # doesn't currently carry original ordering indices; multiset check suffices.
