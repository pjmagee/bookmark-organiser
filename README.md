# Bookmark Organiser

A command-line workflow that parses a browser bookmark export, enriches it with live page metadata, uses an LLM to suggest a reorganised folder structure, and emits refreshed HTML plus validation reports.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for environment and script management
- An OpenAI API key `OPENAI_API_KEY`

## Initial Setup

1. Install the dependencies (installs into a local `.venv`):

   ```pwsh
   uv sync
   ```

2. Activate the virtual environment if you prefer running scripts directly (optional when using `uv run`):

   ```pwsh
   .\.venv\Scripts\Activate.ps1
   ```

3. Export your API key:

   ```pwsh
   $env:OPENAI_API_KEY = "sk-..."
   ```

## CLI Overview

Run the orchestrator with `uv run python main.py` and choose a mode that targets a specific pipeline phase. Add `--verbose` for detailed logging.

| Mode | Purpose | Outputs (written/updated) |
| --- | --- | --- |
| `parse` | Parse the HTML export and serialize initial bookmark records. | `bookmarks.json` (fresh write) |
| `metadata` | Enrich records with page metadata (reuse cache unless `--fresh-scrape`). Concurrency accelerates I/O-bound fetches. | `bookmarks.json` (updated), may skip fetch if fully cached |
| `llm` | Run categorisation only (assumes parsed + optionally enriched JSON). | `bookmarks.json` (updated with reorg fields), `bookmarks_reorganised.html`, validation log |
| `html` | Generate reorganised HTML from existing JSON (requires prior `llm`). | `bookmarks_reorganised.html` |
| `compare` | Validate an existing reorganised HTML against original export. | Console validation report |
| `all` | End-to-end: parse → metadata (concurrent) → LLM → HTML → validation. | All artefacts above |

### Shared Flags

- `--use-json-cache` reuses existing JSON instead of re-parsing the HTML.
- `--fresh-scrape` forces metadata re-fetches for every URL.
- `--verbose` surfaces detailed progress logs.
- `--instruction-file` points to free-form guidance that is injected into the user prompt.
- `--system-instruction-file` appends immutable guardrails to the system prompt before each LLM batch.
- `--metadata-mode` controls enrichment strategy: `all` (default) or `only-missing` to skip already enriched entries.

### Additional CLI Flags

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--input` | `bookmarks_31_10_2025.html` (or `$env:BOOKMARKS_EXPORT_FILE` if set) | Source bookmark HTML export. |
| `--json-output` | `bookmarks.json` | Path for the intermediate & evolving JSON (overwritten each stage). |
| `--html-output` | `bookmarks_reorganised.html` | Destination for the reorganised HTML. |
| `--model` | `gpt-4.1-mini` | OpenAI model for the LLM categorisation stage. |
| `--mode` | `llm` | Pipeline stage selector (now includes `all`). |
| `--metadata-mode` | `all` | Strategy for enrichment: `all` or `only-missing`. |

### Mode Interactions & Constraints

- `parse` mode cannot be combined with `--use-json-cache` or `--fresh-scrape` (they are meaningless before JSON exists).
- `html` mode cannot be combined with `--fresh-scrape` (no scraping occurs).
- `llm` regenerates HTML and runs validation after categorisation; it skips parsing if `--use-json-cache` provided.
- `all` executes every phase in order, respecting `--fresh-scrape` and metadata mode.
- Metadata reuse requires `--use-json-cache` and a pre-existing JSON file.
- If any bookmark lacks a `location_after` in JSON, `--mode html` will abort; run `llm` or `all` to regenerate.

### JSON Evolution

The file indicated by `--json-output` is overwritten at each stage:

1. `parse`: base entries (no metadata, no reorganised fields).
2. `metadata`: adds or updates `metadata` (title/description/tags). Cached entries are reused unless `--fresh-scrape`.
3. `llm`: appends `title_after`, `location_after`, refined tags; then HTML is built and validation runs.
4. `all`: performs steps 1–3 sequentially.

There are no separate `bookmarks_with_metadata.json` or `bookmarks_with_llm.json` files—only the evolving `bookmarks.json`.

## Typical End-to-End Run

1. Parse the source export:

   ```pwsh
   uv run python main.py --mode parse --verbose
   ```

2. Enrich metadata, reusing the freshly written JSON:

   ```pwsh
   uv run python main.py --mode metadata --use-json-cache --verbose
   ```

3. Ask the LLM to reorganise, using cached metadata:

   ```pwsh
   uv run python main.py --mode llm --use-json-cache --verbose
   ```

4. Rebuild the bookmark HTML:

   ```pwsh
   uv run python main.py --mode html --verbose
   ```

5. Confirm the output mirrors the original bookmark set:

   ```pwsh
   uv run python main.py --mode compare --verbose
   ```

The orchestrator writes results into the project root, allowing you to open `bookmarks_reorganised.html` in a browser and inspect the final structure. Validation logs appear in the console after `llm` and `compare`.

## Troubleshooting Tips

- Missing metadata typically means the site blocked scraping; the tool falls back to the domain root when possible (401/403/407 triggers root retry).
- If the metadata cache seems stale, rerun with `--fresh-scrape`.
- The HTML build requires successful `llm` output; rerun step 3 (or `all`) if you see a "reorganised locations missing" error.
- Use `--system-instruction-file` for hard safety/format constraints; use `--instruction-file` for softer organisational hints.
- If all metadata is already present, `metadata` mode with `--metadata-mode only-missing` will skip network calls entirely.
- Concurrency: metadata enrichment uses a thread pool (default workers=12) and falls back to root URL on permission errors.

## Validation Guarantees

The validator enforces:

- URL multiset equivalence between original and reorganised output.
- Every record gets a non-empty `location_after` after LLM step.
- Index continuity and ordering expectations inside generated HTML blocks.
- Structural depth trimming to `MAX_FOLDER_DEPTH` (currently 4) to avoid overly nested folders.

## Configuration Constants

`config.py` provides global constants (e.g. `MAX_FOLDER_DEPTH`, `DEFAULT_BATCH_SIZE`). Adjust carefully; depth >4 can inflate token usage and reduce clarity.

## Tests

Run the full test suite (pytest) via `uv`:

```pwsh
uv run pytest -q
```

Ruff lint (auto-fix where possible):

```pwsh
uv run ruff check . --fix
```

Add a dependency needed only for development (example):

```pwsh
uv add --dev httpx
```

Regenerate the lock / sync after editing `pyproject.toml` manually:

```pwsh
uv sync
```

The implemented test coverage currently targets:

- Parsing record count integrity.
- Metadata enrichment concurrency & skip logic (mocked requests).
- LLM response schema validation & retry (including malformed JSON batch scenarios).
- HTML generation depth enforcement.
- Validator edge cases (missing locations, URL mismatch, empty location_after).

## Environment Fallbacks

`--input` can be omitted if `BOOKMARKS_EXPORT_FILE` environment variable is set.

## Quality & Reliability

LLM calls use retry with exponential backoff and pydantic schema validation. Malformed items are logged and skipped, ensuring downstream HTML remains consistent.

## Performance Notes

Concurrent metadata fetching dramatically reduces total enrichment time for large bookmark sets. Small sets (≤3) run serially to avoid thread overhead.
