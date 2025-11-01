"""Global configuration constants for bookmark organiser."""

from __future__ import annotations

# Maximum allowed folder nesting depth for reorganised bookmarks.
# This value is enforced both in LLM instructions and HTML generation.
MAX_FOLDER_DEPTH: int = 4

# Default batch size for LLM processing (can be overridden per instance).
DEFAULT_BATCH_SIZE: int = 25
