"""Data models for bookmark organising pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from attrs import Factory, define
from pydantic import BaseModel, Field, RootModel, field_validator


def _empty_str_list() -> list[str]:
    return []


@dataclass(slots=True)
class BookmarkMetadata:
    """Metadata scraped from the bookmark target."""

    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=_empty_str_list)


@dataclass(slots=True)
class BookmarkRecord:
    """Bookmark entry captured from the HTML export."""

    title_before: str
    url: str
    location_before: str
    metadata: BookmarkMetadata = field(default_factory=BookmarkMetadata)
    title_after: str = ""
    location_after: str = ""

    def to_model(self) -> BookmarkEntryModel:
        """Convert the record into a serialisable pydantic model."""
        return BookmarkEntryModel(
            title_before=self.title_before,
            title_after=self.title_after,
            url=self.url,
            location_before=self.location_before,
            location_after=self.location_after,
            link_metadata=BookmarkMetadataModel(
                title=self.metadata.title,
                description=self.metadata.description,
                tags=list(self.metadata.tags),
            ),
        )

    @classmethod
    def from_model(cls, model: BookmarkEntryModel) -> BookmarkRecord:
        """Create a record from a validated pydantic model."""
        metadata = BookmarkMetadata(
            title=model.link_metadata.title,
            description=model.link_metadata.description,
            tags=list(model.link_metadata.tags),
        )
        return cls(
            title_before=model.title_before,
            title_after=model.title_after,
            url=model.url,
            location_before=model.location_before,
            location_after=model.location_after,
            metadata=metadata,
        )


class BookmarkMetadataModel(BaseModel):
    """Pydantic model for bookmark metadata."""

    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        return [tag.strip() for tag in value] if value else []


class BookmarkEntryModel(BaseModel):
    """Pydantic model for a bookmark entry."""

    title_before: str
    title_after: str = ""
    url: str
    location_before: str
    location_after: str = ""
    link_metadata: BookmarkMetadataModel


class LLMReorgEntryModel(BaseModel):
    """Model representing a single LLM reorganisation output entry."""

    index: int
    title_after: str = ""
    location_after: str
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        return [str(tag).strip() for tag in value] if value else []


class BookmarkEntryListModel(RootModel[list[BookmarkEntryModel]]):
    """Root list model for bookmark entries (strict all-or-nothing validation)."""

    def to_records(self) -> list[BookmarkRecord]:  # type: ignore[name-defined]
        """Convert the root list of entry models into dataclass records."""
        return [BookmarkRecord.from_model(m) for m in self.root]



@define(slots=True, init=False)
class BookmarkTreeNode:
    """Tree node used when rendering the reorganised bookmark HTML."""

    name: str
    children: list[BookmarkTreeNode] = Factory(lambda: list[BookmarkTreeNode]())
    bookmarks: list[BookmarkRecord] = Factory(lambda: list[BookmarkRecord]())

    def __init__(self, name: str) -> None:
        """Initialise the tree node."""
        self.name = name
        self.children = []
        self.bookmarks = []

    def get_or_create_child(self, child_name: str) -> BookmarkTreeNode:
        """Get or create a child node with the given name."""
        for child in self.children:
            if child.name == child_name:
                return child
        new_child = BookmarkTreeNode(name=child_name)
        self.children.append(new_child)
        return new_child

    def add_bookmark(self, record: BookmarkRecord) -> None:
        """Add a bookmark to the node."""
        self.bookmarks.append(record)
