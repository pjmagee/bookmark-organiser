"""Shared pytest fixtures for bookmark organiser tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bookmark_organiser import parser

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sample_export_html(tmp_path: Path) -> Path:
    """Create a minimal synthetic bookmark export HTML file."""
    content = (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1><HTML><H3>Bookmarks</H3><DL>"
        '<DT><A HREF="https://example.com">Example</A></DT>'
        '<DT><A HREF="https://example.org">Example Org</A></DT>'
        "</DL></HTML>"
    )
    p = tmp_path / "sample.html"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def run_parse(tmp_path: Path, sample_export_html: Path) -> Path:
    """Run the parser on the synthetic export and produce a JSON artifact."""
    records = parser.parse_bookmark_html(sample_export_html)
    out = tmp_path / "bookmarks.json"
    payload = [r.to_model().model_dump() for r in records]
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return out
