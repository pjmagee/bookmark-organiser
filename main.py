"""CLI entry point for bookmark organiser tool.

Provides parsing, metadata enrichment (concurrent), LLM-based reorganisation,
HTML rendering, and validation modes. Designed for low cyclomatic complexity
by delegating distinct workflow segments to focused helper functions.
"""

from __future__ import annotations

# Standard library imports (alphabetical within groups)
import argparse
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

# Third-party imports
from dotenv import load_dotenv

# Internal imports
from bookmark_organiser.html_writer import write_bookmark_html
from bookmark_organiser.metadata import MetadataEnrichMode, enrich_with_metadata
from bookmark_organiser.organiser import BookmarkOrganiser
from bookmark_organiser.parser import parse_bookmark_html
from bookmark_organiser.validator import validate_reorganisation

if TYPE_CHECKING:  # pragma: no cover
    from bookmark_organiser.models import BookmarkRecord

STAGES: dict[int, str] = {
    1: "Parse bookmark export",
    2: "Persist intermediate JSON",
    3: "Enrich with page metadata",
    4: "LLM reorganisation & HTML rebuild",
    5: "Validation",
}


def configure_logging(*, verbose: bool) -> None:
    """Configure root logging (debug when verbose).

    verbose: when True, sets DEBUG level; otherwise INFO.
    (Keyword-only to improve call-site clarity.)
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_user_instructions(config_path: Path | None) -> str | None:
    """Load free-form instruction text from a file path if provided.

    Returns None when the path is absent.
    Raises FileNotFoundError when a non-existent path is supplied.
    """
    if config_path is None:
        return None
    if not config_path.exists():
        msg = f"User instruction file not found: {config_path}"
        raise FileNotFoundError(msg)
    return config_path.read_text(encoding="utf-8")


def log_stage(stage_number: int, message: str, *args: object) -> None:
    """Log a message prefixed with a stage label."""
    stage_label = STAGES.get(stage_number, f"Stage {stage_number}")
    logger = logging.getLogger("bookmark_organiser")
    logger.info("[%s] %s", stage_label, message % args if args else message)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reorganise Chrome/Brave bookmark exports")
    parser.add_argument(
        "--input",
        help=(
            "Path to the exported bookmarks HTML file. If omitted, the environment variable"
            " BOOKMARKS_EXPORT_FILE is used. (No built-in default to avoid stale hardcoding.)"
        ),
    )
    parser.add_argument(
        "--json-output",
        default="bookmarks.json",
        help="Path to emit the intermediate JSON",
    )
    parser.add_argument(
        "--html-output",
        default="bookmarks_reorganised.html",
        help="Path to emit the reorganised bookmarks HTML",
    )
    parser.add_argument(
        "--instruction-file",
        type=Path,
        help="Path to a text file with user-specific reorganisation hints",
    )
    parser.add_argument(
        "--system-instruction-file",
        type=Path,
        help=(
            "Path to a text file whose contents append to the system prompt before each LLM call"
        ),
    )
    parser.add_argument(
        "--model",
        default="gpt-4.1-mini",
        help="OpenAI model to use for categorisation",
    )
    parser.add_argument(
        "--mode",
        choices=("llm", "metadata", "parse", "html", "compare", "all"),
        default="llm",
        help=(
            "Workflow: 'parse'→JSON; 'metadata'→add page data; 'llm'→full pipeline;"
            " 'html'→rebuild HTML from JSON; 'compare'→validate; 'all'→full strict sequence."
        ),
    )
    parser.add_argument(
        "--use-json-cache",
        action="store_true",
        help="Reuse metadata from existing bookmarks.json if present",
    )
    parser.add_argument(
        "--fresh-scrape",
        action="store_true",
        help="Force refetch of metadata for every bookmark",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    if args.mode == "parse" and (args.use_json_cache or args.fresh_scrape):
        parser.error("--use-json-cache/--fresh-scrape cannot be combined with mode=parse")
    if args.mode == "html" and args.fresh_scrape:
        parser.error("--fresh-scrape cannot be combined with mode=html")
    return args


def _resolve_input(path_arg: str | None) -> Path:
    resolved = path_arg or os.getenv("BOOKMARKS_EXPORT_FILE")
    if not resolved:
        msg = (
            "No input file provided. Supply --input or set BOOKMARKS_EXPORT_FILE in env."
        )
        raise SystemExit(msg)
    return Path(resolved)



def _load_cache(json_path: Path, logger: logging.Logger) -> dict[str, BookmarkRecord]:
    if not json_path.exists():
        return {}
    try:
        cached_records = BookmarkOrganiser.load_json(json_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to load existing JSON at %s (%s); continuing without cache",
            json_path,
            exc,
        )
        return {}
    return {entry.url: entry for entry in cached_records}


def _reuse_metadata(
    records: list[BookmarkRecord], cache_map: dict[str, BookmarkRecord], logger: logging.Logger,
) -> int:
    reused = 0
    for record in records:
        cached = cache_map.get(record.url)
        if not cached:
            continue
        if cached.metadata.title or cached.metadata.description or cached.metadata.tags:
            record.metadata.title = cached.metadata.title
            record.metadata.description = cached.metadata.description
            record.metadata.tags = list(cached.metadata.tags)
            reused += 1
    if reused:
        logger.info("Reused cached metadata for %d bookmarks", reused)
    return reused


def _run_compare(input_path: Path, html_path: Path) -> None:
    log_stage(5, "Running comparison between %s and %s", input_path, html_path)
    if not html_path.exists():
        msg = f"Reorganised bookmark file not found: {html_path}"
        raise FileNotFoundError(msg)
    original_records = parse_bookmark_html(input_path)
    validate_reorganisation(original_records, html_path)
    log_stage(5, "Comparison completed successfully")


def _run_html_only(json_path: Path, html_path: Path, input_path: Path) -> None:
    log_stage(4, "Loading reorganised data from %s", json_path)
    if not json_path.exists():
        msg = f"JSON cache not found at {json_path}; run in 'llm' mode first"
        raise FileNotFoundError(msg)
    records = BookmarkOrganiser.load_json(json_path)
    missing_locations = [r.url for r in records if not r.location_after.strip()]
    if missing_locations:
        msg = (
            "JSON cache does not contain reorganised locations for all bookmarks; "
            "run in 'llm' mode to regenerate them"
        )
        raise ValueError(msg)
    write_bookmark_html(records, html_path)
    log_stage(5, "Running validation checks")
    original_records = parse_bookmark_html(input_path)
    validate_reorganisation(original_records, html_path)
    log_stage(5, "Workflow completed successfully")


class MetadataStrategy(str, Enum):
    """How to obtain metadata for bookmarks."""

    REUSE_MISSING = "reuse-missing"
    REFRESH_ALL = "refresh-all"


def _enrich_metadata(
    records: list[BookmarkRecord], strategy: MetadataStrategy,
) -> list[BookmarkRecord]:
    fresh_scrape = strategy == MetadataStrategy.REFRESH_ALL
    to_fetch = (
        len(records)
        if fresh_scrape
        else sum(
            1
            for r in records
            if not (r.metadata.title or r.metadata.description or r.metadata.tags)
        )
    )
    if to_fetch == 0 and not fresh_scrape:
        log_stage(
            3,
            "Using cached metadata for all %d bookmarks; skipping scraping",
            len(records),
        )
        return records
    log_stage(3, "Fetching metadata for %d bookmarks", to_fetch)
    mode = MetadataEnrichMode.ALL if fresh_scrape else MetadataEnrichMode.ONLY_MISSING
    return enrich_with_metadata(records, mode=mode)
def _determine_effective_mode(requested: str) -> str:
    """Resolve composite modes to concrete implementation paths."""
    if requested == "all":
        return "llm"
    return requested


@dataclass(slots=True)
class PipelinePaths:
    """Bundle of core paths used by the pipeline."""

    json_path: Path
    html_path: Path
    input_path: Path


def _run_llm_pipeline(
    records: list[BookmarkRecord],
    organiser: BookmarkOrganiser,
    paths: PipelinePaths,
    user_instructions: str | None,
) -> None:
    log_stage(4, "Invoking LLM for reorganisation")
    records = organiser.reorganise(records, user_instructions)
    organiser.write_json(records, paths.json_path)
    log_stage(4, "Building reorganised bookmark HTML at %s", paths.html_path)
    write_bookmark_html(records, paths.html_path)
    log_stage(5, "Running validation checks")
    original_records = parse_bookmark_html(paths.input_path)
    validate_reorganisation(original_records, paths.html_path)
    log_stage(5, "Workflow completed successfully")


def _handle_compare(args: argparse.Namespace, input_path: Path) -> None:
    _run_compare(input_path, Path(args.html_output))


def _handle_html(args: argparse.Namespace, input_path: Path) -> None:
    _run_html_only(Path(args.json_output), Path(args.html_output), input_path)


def _prepare_organiser(args: argparse.Namespace) -> tuple[BookmarkOrganiser, str | None]:
    user_instructions = load_user_instructions(args.instruction_file)
    system_instructions = load_user_instructions(args.system_instruction_file)
    organiser = BookmarkOrganiser(model=args.model, system_prompt_extension=system_instructions)
    return organiser, user_instructions


def _handle_parse(
    args: argparse.Namespace,
    input_path: Path,
    organiser: BookmarkOrganiser,
) -> list[BookmarkRecord]:
    log_stage(1, "Starting stage 1: Parse bookmark export")
    records = parse_bookmark_html(input_path)
    log_stage(2, "Writing intermediate JSON to %s", Path(args.json_output))
    organiser.write_json(records, Path(args.json_output))
    return records


def _maybe_reuse_cache(
    args: argparse.Namespace,
    json_path: Path,
    records: list[BookmarkRecord],
    logger: logging.Logger,
    effective_mode: str,
) -> dict[str, BookmarkRecord]:
    cache_map: dict[str, BookmarkRecord] = {}
    if args.use_json_cache:
        cache_map = _load_cache(json_path, logger)
        if not cache_map:
            logger.info("No existing JSON cache at %s; proceeding without reuse", json_path)
    if cache_map and effective_mode in {"metadata", "llm"} and not args.fresh_scrape:
        _reuse_metadata(records, cache_map, logger)
    return cache_map


def _handle_metadata(
    args: argparse.Namespace,
    records: list[BookmarkRecord],
    organiser: BookmarkOrganiser,
) -> list[BookmarkRecord]:
    strategy = (
        MetadataStrategy.REFRESH_ALL if args.fresh_scrape else MetadataStrategy.REUSE_MISSING
    )
    records = _enrich_metadata(records, strategy)
    organiser.write_json(records, Path(args.json_output))
    return records


def _handle_llm(
    args: argparse.Namespace,
    records: list[BookmarkRecord],
    organiser: BookmarkOrganiser,
    user_instructions: str | None,
    input_path: Path,
) -> None:
    paths = PipelinePaths(
        json_path=Path(args.json_output), html_path=Path(args.html_output), input_path=input_path,
    )
    _run_llm_pipeline(records, organiser, paths, user_instructions)


def main() -> None:
    """Entry point for the bookmark organiser CLI."""
    load_dotenv()
    args = _parse_args()
    input_path = _resolve_input(args.input)
    configure_logging(verbose=args.verbose)
    logger = logging.getLogger("bookmark_organiser")
    effective_mode = _determine_effective_mode(args.mode)

    if effective_mode == "compare":  # Early dispatch
        _handle_compare(args, input_path)
        return
    if effective_mode == "html":  # Early dispatch
        _handle_html(args, input_path)
        return

    organiser, user_instructions = _prepare_organiser(args)
    records = _handle_parse(args, input_path, organiser)
    json_path = Path(args.json_output)
    _maybe_reuse_cache(args, json_path, records, logger, effective_mode)

    if effective_mode == "parse":
        log_stage(2, "JSON export complete; parse-only mode finished")
        return

    if effective_mode in {"metadata", "llm"}:
        records = _handle_metadata(args, records, organiser)
        if effective_mode == "metadata":
            log_stage(3, "Metadata enrichment complete; metadata-only mode finished")
            return

    if effective_mode == "llm":
        _handle_llm(args, records, organiser, user_instructions, input_path)


if __name__ == "__main__":
    main()
