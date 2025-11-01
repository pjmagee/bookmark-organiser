"""Tests for parsing bookmark exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bookmark_organiser import parser

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_COUNT = 2
EXPECTED_URLS = {"https://example.com", "https://example.org"}


def test_parse_basic(sample_export_html: Path) -> None:
    """Ensure parser extracts expected bookmarks with titles and URLs."""
    records = parser.parse_bookmark_html(sample_export_html)
    if len(records) != EXPECTED_COUNT:
        msg = f"Expected {EXPECTED_COUNT} records, got {len(records)}"
        raise AssertionError(msg)
    urls = {r.url for r in records}
    missing = EXPECTED_URLS - urls
    if missing:
        msg = f"Missing expected URLs: {missing}"
        raise AssertionError(msg)
    if not all(r.title_before for r in records):
        msg = "One or more records missing title_before"
        raise AssertionError(msg)
