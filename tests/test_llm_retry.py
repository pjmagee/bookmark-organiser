"""Tests LLM retry/backoff and validation filtering in organiser._invoke_with_retry.

We mock the OpenAI client to first return malformed JSON, then valid structured JSON.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from bookmark_organiser.models import BookmarkRecord
from bookmark_organiser.organiser import BookmarkOrganiser, LLMReorgEntryModel


class DummyChoice:
    def __init__(self, content: str) -> None:
        self.message = SimpleNamespace(content=content)


class DummyResponse:
    def __init__(self, content: str) -> None:
        self.choices = [DummyChoice(content)]


class DummyClient:
    def __init__(self, payloads: list[str]) -> None:
        self._payloads = payloads
        self._calls = 0

    class Chat:  # mimic structure expected by organiser
        class Completions:
            @staticmethod
            def create() -> Any:  # replaced dynamically
                raise RuntimeError("Stub replaced dynamically per instance")

        completions = Completions()

    chat = Chat()

    def set_create(self) -> None:
        def _create(*_args: Any, **_kwargs: Any) -> DummyResponse:  # flexible signature
            if self._calls >= len(self._payloads):
                raise RuntimeError("No more dummy payloads")
            payload = self._payloads[self._calls]
            self._calls += 1
            return DummyResponse(payload)
        self.chat.completions.create = _create  # type: ignore[assignment]


def test_llm_retry_and_validation() -> None:
    malformed = json.dumps({"oops": 1})  # triggers TypeError (not list)
    valid_list = json.dumps(
        [
            {"index": 0, "title_after": "Title A", "location_after": "FolderA", "tags": ["a"]},
            {"index": 1, "title_after": "Title B"},  # invalid, missing location_after
        ],
    )

    dummy_client = DummyClient([malformed, valid_list])
    dummy_client.set_create()

    organiser = BookmarkOrganiser()
    organiser.set_client(dummy_client)  # inject dummy client

    r1 = BookmarkRecord(
        title_before="Before A", url="https://a.example", location_before="Root/A",
    )
    r2 = BookmarkRecord(
        title_before="Before B", url="https://b.example", location_before="Root/B",
    )

    out = organiser.reorganise([r1, r2])
    rec0, rec1 = out[0], out[1]
    if rec0.location_after != "FolderA":
        raise AssertionError("Expected valid location_after assignment from second payload")
    if rec1.location_after == "FolderB":  # would indicate unintended modification
        raise AssertionError("Second record should remain unchanged due to invalid item")
    if rec0.metadata.tags != ["a"]:
        raise AssertionError("Tags not applied correctly for valid item")

    LLMReorgEntryModel.model_validate(
        {
            "index": 0,
            "title_after": rec0.title_after,
            "location_after": rec0.location_after,
            "tags": rec0.metadata.tags,
        },
    )
