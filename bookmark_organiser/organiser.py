"""Bookmark organiser using OpenAI's language models."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from typing import TYPE_CHECKING, cast

from openai import OpenAI

from .config import DEFAULT_BATCH_SIZE, MAX_FOLDER_DEPTH
from .models import BookmarkEntryModel, BookmarkRecord, LLMReorgEntryModel

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4.1-mini"

SYSTEM_PROMPT = f"""You are an assistant that restructures browser bookmarks.
You MUST respond with valid JSON only — no prose — using the following schema:
[
    {{
        "index": <int>,
        "title_after": <string>,
        "location_after": <string>,
        "tags": [<string>, ...]
    }}
]

Rules:
- Keep the output list order identical to the input ordering by index.
- Every bookmark must be assigned to a non-empty `location_after` with folders delimited by "/".
- There must be at least one folder level (no direct root bookmarks).
- Limit folder nesting depth to at most {MAX_FOLDER_DEPTH} levels.
- Location names should be concise but descriptive and stable across entries.
- Titles can be adjusted to be clearer but must remain short.
- Ensure tags are informative keywords sorted alphabetically and 3-5 per entry when possible.
"""


class LLMInvocationError(RuntimeError):
    """Raised when all LLM invocation attempts fail."""


class BookmarkOrganiser:
    """LLM-backed bookmark categoriser.

    Adds optional fallback model support: if the primary model is unavailable (e.g. user
    requests a frontier model like `gpt-5` not enabled for the API key), the first failed
    attempt inside `_invoke_with_retry` will transparently switch to a configured fallback
    (env `OPENAI_FALLBACK_MODEL`, else default) and retry remaining attempts.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        system_prompt_extension: str | None = None,
        fallback_model: str | None = None,
    ) -> None:
        """Initialise the organiser.

        Args:
            model: Primary model to use (user supplied / CLI).
            batch_size: Number of bookmarks per LLM call.
            system_prompt_extension: Extra system instructions appended to system prompt.
            fallback_model: Explicit fallback model. If None, uses env ``OPENAI_FALLBACK_MODEL``
                else defaults to ``DEFAULT_MODEL``.

        """
        self._client = OpenAI()
        self._model = model
        fb = fallback_model or os.getenv("OPENAI_FALLBACK_MODEL") or DEFAULT_MODEL
        # Ensure type is concretely str for type checkers
        self._fallback_model: str = str(fb)
        self._batch_size = max(1, batch_size)
        # Some newer frontier models (e.g. certain gpt-5* preview/versioned endpoints)
        # reject explicit temperature values other than their fixed default, returning
        # a 400 with an 'unsupported_value' error. We start optimistic (allow custom
        # temperature) and disable it if we encounter that error once.
        self._supports_temperature: bool = True
        base_prompt = SYSTEM_PROMPT.strip()
        if system_prompt_extension and system_prompt_extension.strip():
            extension = system_prompt_extension.strip()
            self._system_prompt = (
                f"{base_prompt}\n\nAdditional directives from the user:\n{extension}"
            )
        else:
            self._system_prompt = base_prompt

    # Test/extension hook -----------------------------------------------------
    def set_client(self, client: object) -> None:  # pragma: no cover - test helper
        """Inject a mock / custom OpenAI-like client (testing only)."""
        # Assign with ignore; dynamic test double accepted.
        self._client = client  # type: ignore[assignment]

    def reorganise(
        self,
        records: Iterable[BookmarkRecord],
        user_context: str | None = None,
    ) -> list[BookmarkRecord]:
        """Reorganise bookmarks using the LLM model."""
        mutable_records: list[BookmarkRecord] = list(records)
        processed: list[BookmarkRecord] = []

        for start in range(0, len(mutable_records), self._batch_size):
            chunk = mutable_records[start : start + self._batch_size]
            payload = self._build_payload(chunk, start)
            structure_context = self._summarise_structure(processed)
            messages = self._build_messages(payload, user_context, structure_context)

            LOGGER.info(
                "Requesting categorisation from OpenAI model %s for entries %d-%d",
                self._model,
                start,
                start + len(chunk) - 1,
            )
            parsed = self._invoke_with_retry(messages)
            index_map = {item.index: item for item in parsed}

            for idx, record in enumerate(chunk, start=start):
                entry = index_map.get(idx)
                if entry is None:
                    LOGGER.warning(
                        "No LLM output for bookmark index %d; leaving entry unchanged", idx,
                    )
                    continue

                record.title_after = str(entry.title_after or record.title_before)
                record.location_after = str(entry.location_after or record.location_before)
                tags_list = [str(tag).strip() for tag in entry.tags if str(tag).strip()]
                record.metadata.tags = tags_list

            processed.extend(chunk)

        return mutable_records

    # --- LLM invocation & validation helpers -------------------------------------------------

    def _invoke_with_retry(
        self,
        messages: list[ChatCompletionMessageParam],
        max_attempts: int = 3,
        backoff_seconds: float = 2.0,
    ) -> list[LLMReorgEntryModel]:
        """Invoke chat completion with retry/backoff and return validated items."""
        last_error: Exception | None = None

        def _single_attempt() -> list[LLMReorgEntryModel]:
            if self._supports_temperature:
                response: ChatCompletion = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=0.2,
                )
            else:
                response: ChatCompletion = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
            raw_items = self._extract_items(cast("ChatCompletion", response))  # type: ignore[arg-type]
            validated: list[LLMReorgEntryModel] = []
            for obj in raw_items:
                item = self._validate_item(obj)
                if item is not None:
                    validated.append(item)
            return validated

        for attempt in range(1, max_attempts + 1):
            try:
                validated = _single_attempt()
                if validated:
                    return validated
                last_error = ValueError("No valid items after validation")
            except Exception as exc:  # noqa: BLE001
                if self._process_exception(
                    exc,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                ):
                    continue
                time.sleep(backoff_seconds * attempt)
                continue

        if last_error is not None:
            raise last_error
        raise LLMInvocationError

    # Separated to reduce complexity of _invoke_with_retry
    def _process_exception(
        self,
        exc: Exception,
        *,
        attempt: int,
        max_attempts: int,
        backoff_seconds: float,
    ) -> bool:
        """Handle an exception and decide whether to immediate-continue.

        Returns True if caller should immediately continue (retry without generic
        backoff logging), else False allowing caller to apply standard backoff.
        """
        message = str(exc).lower()
        # Temperature unsupported: disable and retry immediately
        if (
            self._supports_temperature
            and "temperature" in message
            and "unsupported" in message
        ):
            LOGGER.warning(
                "Model '%s' rejects custom temperature; omitting for remaining attempts",
                self._model,
            )
            self._supports_temperature = False
            time.sleep(0.1)
            return True

        # Primary model missing -> swap to fallback after first failure
        if (
            attempt == 1
            and self._fallback_model != self._model
            and "model" in message
            and ("not found" in message or "does not exist" in message)
        ):
            LOGGER.warning(
                "Primary model '%s' unavailable; switching to fallback '%s'",
                self._model,
                self._fallback_model,
            )
            self._model = self._fallback_model
            time.sleep(backoff_seconds * attempt)
            return True

        # Generic failure -> log; caller handles timed backoff
        LOGGER.warning(
            "LLM attempt %d/%d failed: %s; retrying in %.1fs",
            attempt,
            max_attempts,
            exc,
            backoff_seconds,
        )
        return False

    def _extract_items(self, response: ChatCompletion) -> list[dict[str, object]]:
        if not response.choices:
            msg = "OpenAI response missing choices"
            raise RuntimeError(msg)
        content = response.choices[0].message.content
        if content is None:
            msg = "OpenAI response content empty"
            raise RuntimeError(msg)
        raw_obj: object = json.loads(content)
        if not isinstance(raw_obj, list):
            msg = "LLM response root is not a list"
            raise TypeError(msg)
        raw_list = cast("list[object]", raw_obj)
        cleaned: list[dict[str, object]] = [
            cast("dict[str, object]", item) for item in raw_list if isinstance(item, dict)
        ]
        return cleaned

    def _validate_item(self, obj: dict[str, object]) -> LLMReorgEntryModel | None:
        if "index" not in obj or "location_after" not in obj:
            LOGGER.warning("Skipping item missing required keys: %r", obj)
            return None
        # Normalise depth
        location = str(obj.get("location_after", "")).strip()
        if location:
            parts = [p.strip() for p in location.split("/") if p.strip()]
            if len(parts) > MAX_FOLDER_DEPTH:
                parts = parts[:MAX_FOLDER_DEPTH]
            obj["location_after"] = "/".join(parts)
        try:
            return LLMReorgEntryModel.model_validate(obj)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Validation failed for item %r: %s", obj, exc)
            return None

    @staticmethod
    def _build_payload(
        records: Sequence[BookmarkRecord], start_index: int,
    ) -> list[dict[str, object]]:
        payload: list[dict[str, object]] = []
        for offset, record in enumerate(records):
            idx = start_index + offset
            payload.append(
                {
                    "index": idx,
                    "title_before": record.title_before,
                    "url": record.url,
                    "location_before": record.location_before,
                    "metadata": {
                        "title": record.metadata.title,
                        "description": record.metadata.description,
                        "tags": record.metadata.tags,
                    },
                },
            )
        return payload

    def _build_messages(
        self,
        payload: Sequence[dict[str, object]],
        user_context: str | None,
        structure_context: dict[str, object],
    ) -> list[ChatCompletionMessageParam]:
        user_instruction = (
            user_context.strip()
            if user_context and user_context.strip()
            else (
                "Use sensible default folders with no more than "
                f"{MAX_FOLDER_DEPTH} levels and avoid root-level bookmarks."
            )
        )

        user_payload = json.dumps(
            {
                "instructions": user_instruction,
                "existing_structure": structure_context,
                "entries": payload,
            },
        )

        return [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": self._system_prompt,
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_payload,
                    },
                ],
            },
        ]

    @staticmethod
    def _summarise_structure(records: Sequence[BookmarkRecord]) -> dict[str, object]:
        folders: dict[str, list[str]] = defaultdict(list)
        for record in records:
            location = record.location_after.strip()
            if not location:
                continue
            representative_title = record.title_after.strip() or record.title_before
            if representative_title:
                folders[location].append(representative_title)

        summary: list[dict[str, object]] = []
        for path, titles in folders.items():
            summary.append(
                {
                    "path": path,
                    "count": len(titles),
                    "examples": titles[:5],
                },
            )

        ordered = sorted(summary, key=lambda item: str(item["path"]).lower())
        return {"folders": ordered}

    @staticmethod
    def write_json(records: Iterable[BookmarkRecord], path: Path) -> None:
        """Write bookmark records to a JSON file."""
        models = [record.to_model() for record in records]
        payload = [model.model_dump(mode="json") for model in models]
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        LOGGER.info("Wrote %d records to %s", len(payload), path)

    @staticmethod
    def load_json(path: Path) -> list[BookmarkRecord]:
        """Load bookmark records from a JSON file."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        models = [BookmarkEntryModel.model_validate(item) for item in raw]
        return [BookmarkRecord.from_model(model) for model in models]
