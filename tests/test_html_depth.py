"""Test that folder depth is truncated to MAX_FOLDER_DEPTH in HTML output."""
from __future__ import annotations

from bookmark_organiser import html_writer
from bookmark_organiser.config import MAX_FOLDER_DEPTH
from bookmark_organiser.models import BookmarkRecord, BookmarkTreeNode


def _make_record(url: str, loc: str, title: str) -> BookmarkRecord:
    return BookmarkRecord(title_before=title, url=url, location_before=loc)


def test_html_depth_truncation() -> None:
    # Construct a record exceeding depth by +2
    deep_path_segments = [f"L{i}" for i in range(1, MAX_FOLDER_DEPTH + 3)]
    deep_path = "/".join(deep_path_segments)
    r = _make_record("https://example.com/deep", deep_path, "Deep Item")
    # Use location_after to simulate post-LLM state
    r.location_after = deep_path
    tree = html_writer.build_tree([r])

    # Traverse tree depth; should not exceed MAX_FOLDER_DEPTH nodes below root
    def _depth(node: BookmarkTreeNode, level: int = 0) -> int:
        if not node.children:
            return level
        return max(_depth(child, level + 1) for child in node.children)

    observed_depth = _depth(tree)
    if observed_depth > MAX_FOLDER_DEPTH:
        msg = (
            f"Observed depth {observed_depth} exceeds MAX_FOLDER_DEPTH {MAX_FOLDER_DEPTH}"
        )
        raise AssertionError(msg)
