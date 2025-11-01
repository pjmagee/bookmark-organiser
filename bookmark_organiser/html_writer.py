"""Functions for rendering bookmarks as HTML."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

from .config import MAX_FOLDER_DEPTH
from .models import BookmarkRecord, BookmarkTreeNode

HTML_HEADER = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file.
     It will be read and overwritten.
     DO NOT EDIT! -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
"""


def build_tree(records: list[BookmarkRecord]) -> BookmarkTreeNode:
    """Build a tree of bookmark folders and bookmarks from a list of records."""
    root = BookmarkTreeNode(name="Bookmarks Bar")
    for record in records:
        location = record.location_after or record.location_before
        parts = [part.strip() for part in location.split("/") if part.strip()]
        if not parts:
            parts = ["Unsorted"]
        if len(parts) > MAX_FOLDER_DEPTH:
            parts = parts[:MAX_FOLDER_DEPTH]

        node = root
        for part in parts:
            node = node.get_or_create_child(part)
        node.add_bookmark(record)
    return root


def render_html(root: BookmarkTreeNode) -> str:
    """Render the bookmark tree as HTML."""
    lines: list[str] = [HTML_HEADER.strip(), "<DL><p>"]
    _render_node(root, lines, 1)
    lines.append("</DL><p>")
    return "\n".join(lines)


def _render_node(node: BookmarkTreeNode, output: list[str], depth: int) -> None:
    indent = "    " * depth
    for folder in sorted(node.children, key=lambda child: child.name.lower()):
        output.append(f"{indent}<DT><H3>{html.escape(folder.name)}</H3>")
        output.append(f"{indent}<DL><p>")
        _render_node(folder, output, depth + 1)
        output.append(f"{indent}</DL><p>")

    for record in sorted(
        node.bookmarks,
        key=lambda item: item.title_after.lower() or item.title_before.lower(),
    ):
        title = record.title_after or record.title_before
        href = html.escape(record.url, quote=True)
        display_title = html.escape(title)
        output.append(f'{indent}<DT><A HREF="{href}" ADD_DATE="0">{display_title}</A>')


def write_bookmark_html(records: list[BookmarkRecord], output_path: Path) -> None:
    """Write the bookmarks to an HTML file."""
    tree = build_tree(records)
    html_text = render_html(tree)
    output_path.write_text(html_text + "\n", encoding="utf-8")
