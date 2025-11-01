"""Tests for metadata enrichment concurrency and skip logic."""
from __future__ import annotations

import types

from bookmark_organiser import metadata, models


class DummyResponse:
    def __init__(self, text: str, status: int = 200, content_type: str = "text/html") -> None:
        self.text = text
        self.status_code = status
        self._headers = {"Content-Type": content_type}

    @property
    def headers(self):  # simple property shim
        return self._headers

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        if self.status_code >= 400:
            msg = f"HTTP {self.status_code}"
            raise RuntimeError(msg)


def _dummy_html(title: str, desc: str = "", keywords: str = "") -> str:
    parts = ["<html><head>"]
    if title:
        parts.append(f"<title>{title}</title>")
    if desc:
        parts.append(f'<meta name="description" content="{desc}" />')
    if keywords:
        parts.append(f'<meta name="keywords" content="{keywords}" />')
    parts.append("</head><body></body></html>")
    return "".join(parts)


def test_metadata_enrichment_only_missing() -> None:
    # Prepare two records: one with existing metadata to skip.
    r1 = models.BookmarkRecord(
        title_before="Alpha",
        url="https://alpha.example",
        location_before="Root/Alpha",
    )
    r2 = models.BookmarkRecord(
        title_before="Beta",
        url="https://beta.example",
        location_before="Root/Beta",
    )
    r1.metadata.title = "Existing"

    html_alpha = _dummy_html("Alpha Title", "Alpha Desc", "alpha,one")
    html_beta = _dummy_html("Beta Title", "Beta Desc", "beta,two")

    html_map = {
        r1.url: html_alpha,
        r2.url: html_beta,
    }

    # unused args retained to match requests.Session.get signature
    def fake_get(url: str, timeout: float | None = None, allow_redirects: bool = True):  # noqa: ARG001
        return DummyResponse(html_map[url])

    session = types.SimpleNamespace(get=fake_get, headers={})

    # Monkeypatch requests.Session() to return our dummy session
    original_session = metadata.requests.Session
    metadata.requests.Session = lambda: session  # type: ignore[assignment]
    try:
        enriched = metadata.enrich_with_metadata(
            [r1, r2], mode=metadata.MetadataEnrichMode.ONLY_MISSING,
        )
        _count = len(enriched)
    finally:
        metadata.requests.Session = original_session

    # r1 should retain existing metadata (skip), r2 should get populated.
    if r1.metadata.title != "Existing":
        raise AssertionError("Expected r1 metadata to be unchanged")
    if r2.metadata.title != "Beta Title":
        raise AssertionError("Expected r2 metadata to be populated from HTML")
    if "two" not in r2.metadata.tags:
        raise AssertionError("Expected keyword tag parsing")


def test_metadata_enrichment_all_mode() -> None:
    r = models.BookmarkRecord(
        title_before="Gamma",
        url="https://gamma.example",
        location_before="Root/Gamma",
    )
    html_gamma = _dummy_html("Gamma Title", "Gamma Desc", "gamma,three")
    # unused args retained to match requests.Session.get signature
    def fake_get(_url: str, timeout: float | None = None, allow_redirects: bool = True):  # noqa: ARG001
        return DummyResponse(html_gamma)

    session = types.SimpleNamespace(get=fake_get, headers={})
    original_session = metadata.requests.Session
    metadata.requests.Session = lambda: session  # type: ignore[assignment]
    try:
        enriched = metadata.enrich_with_metadata(
            [r], mode=metadata.MetadataEnrichMode.ALL,
        )
        _count2 = len(enriched)
    finally:
        metadata.requests.Session = original_session
    rec = enriched[0]
    if rec.metadata.description != "Gamma Desc":
        raise AssertionError("Description not extracted")
    if set(rec.metadata.tags) != {"gamma", "three"}:
        raise AssertionError("Tags not parsed correctly")
