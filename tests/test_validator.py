"""Validator edge case tests."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bookmark_organiser import parser as bookmark_parser
from bookmark_organiser import validator as bookmark_validator

if TYPE_CHECKING:
    from pathlib import Path


_DEF_EXPORT = (
    "<!DOCTYPE NETSCAPE-Bookmark-file-1><HTML><H3>Bookmarks</H3><DL>"
    '<DT><A HREF="https://one.example">One</A></DT>'
    '<DT><A HREF="https://two.example">Two</A></DT>'
    '</DL></HTML>'
)


def _write_export(tmp_path: Path, html: str = _DEF_EXPORT) -> Path:
    p = tmp_path / "orig.html"
    p.write_text(html, encoding="utf-8")
    return p


def _records_for_export(path: Path):
    return bookmark_parser.parse_bookmark_html(path)


def test_validator_url_mismatch(tmp_path: Path) -> None:
    orig = _write_export(tmp_path)
    original_records = _records_for_export(orig)
    # Write reorganised HTML missing one URL
    bad_html = (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1><HTML><H3>Bookmarks</H3><DL>"
        '<DT><A HREF="https://one.example">One</A></DT>'
        '</DL></HTML>'
    )
    reorganised_path = tmp_path / "reorg.html"
    reorganised_path.write_text(bad_html, encoding="utf-8")
    try:
        bookmark_validator.validate_reorganisation(original_records, reorganised_path)
    except ValueError as exc:
        # Accept either count mismatch or URL mismatch as valid failure scenarios
        if (
            "Mismatch between original and reorganised bookmark counts" not in str(exc)
            and "URL mismatch" not in str(exc)
        ):
            raise AssertionError("Unexpected error text for mismatch validation") from exc
    else:
        raise AssertionError("Expected mismatch validation to fail")


def test_validator_empty_location(tmp_path: Path) -> None:
    orig = _write_export(tmp_path)
    original_records = _records_for_export(orig)
    # Produce reorganised HTML with empty folder (simulate missing location_after root)
    bad_html = (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1><HTML><H3>Bookmarks</H3><DL>"
        '<DT><A HREF="https://one.example">One</A></DT>'
        '<DT><A HREF="https://two.example">Two</A></DT>'
        '</DL></HTML>'
    )
    reorganised_path = tmp_path / "reorg2.html"
    reorganised_path.write_text(bad_html, encoding="utf-8")
    # Manually blank out location_after by editing records JSON
    json_path = tmp_path / "bookmarks.json"
    serialised: list[dict[str, object]] = []
    for idx, r in enumerate(original_records):
        serialised.append(
            {
                "index": idx,
                "title_before": r.title_before,
                "title_after": r.title_before,
                "url": r.url,
                "location_before": r.location_before,
                "location_after": "",  # problematic
                "link_metadata": {"title": "", "description": "", "tags": []},
            },
        )
    json_path.write_text(json.dumps(serialised, indent=2), encoding="utf-8")
    try:
        bookmark_validator.validate_reorganisation(
            original_records, reorganised_path, json_records_path=json_path,
        )
    except ValueError as exc:
        if "empty location_after" not in str(exc):
            raise AssertionError("Expected empty location_after validation error") from exc
    else:
        raise AssertionError("Expected validation to fail for empty location_after")
