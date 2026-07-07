"""Shared data structures passed between pipeline phases."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ElementKind(str, Enum):
    heading = "heading"
    text = "text"
    table = "table"
    image = "image"


@dataclass
class Element:
    """A single extracted content element, tagged with its source page."""

    kind: ElementKind
    page: int
    text: str = ""
    # Heading level 0=TITLE, 1=H1 ... 6=H6 (only for headings).
    level: int = 0
    confidence: float = 0.0
    caption: str = ""
    # y0 position on the page (used for reading-order / association).
    y0: float = 0.0


@dataclass
class DocumentProfile:
    """Output of Phase 0 profiling."""

    doc_type: str  # digital | scanned | mixed
    page_count: int
    body_font_size: float
    # Maps a font size (rounded to 1 decimal) to a heading level, or None for body.
    heading_map: dict[float, int]
    scanned_pages: set[int] = field(default_factory=set)
    pages_with_table: set[int] = field(default_factory=set)
    pages_with_image: set[int] = field(default_factory=set)
    avg_chars_per_page: float = 0.0
    is_slide_deck: bool = False


@dataclass
class Section:
    """A node in the nested document tree built in Phase 4."""

    title: str
    level: int
    page: int
    elements: list[Element] = field(default_factory=list)
    children: list["Section"] = field(default_factory=list)

    def breadcrumb(self, ancestors: list[str]) -> str:
        parts = [a for a in ancestors if a]
        if self.title:
            parts.append(self.title)
        return " > ".join(parts)
