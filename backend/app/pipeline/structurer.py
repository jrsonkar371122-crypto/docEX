"""Phase 4 — build a nested document tree from extracted elements."""
from __future__ import annotations

import logging

from app.pipeline.elements import Element, ElementKind, Section

logger = logging.getLogger("documind.pipeline.structurer")


def _merge_page_elements(
    text_elems: dict[int, list[Element]],
    table_elems: dict[int, list[Element]],
    image_elems: dict[int, list[Element]],
    page_count: int,
) -> list[Element]:
    """Flatten all elements into a single page-ordered stream."""
    ordered: list[Element] = []
    for page in range(1, page_count + 1):
        # Text/headings in their reading order.
        ordered.extend(text_elems.get(page, []))
        # Tables and images attach to whatever section is active on their page.
        ordered.extend(table_elems.get(page, []))
        ordered.extend(image_elems.get(page, []))
    return ordered


def build_tree(
    text_elems: dict[int, list[Element]],
    table_elems: dict[int, list[Element]],
    image_elems: dict[int, list[Element]],
    page_count: int,
) -> Section:
    """Assemble the nested section tree via a stack-based heading tracker."""
    root = Section(title="", level=-1, page=1)
    stack: list[Section] = [root]
    ordered = _merge_page_elements(text_elems, table_elems, image_elems, page_count)

    for element in ordered:
        if element.kind == ElementKind.heading:
            # Pop until the parent is shallower than this heading.
            while len(stack) > 1 and stack[-1].level >= element.level:
                stack.pop()
            section = Section(
                title=element.text,
                level=element.level,
                page=element.page,
            )
            stack[-1].children.append(section)
            stack.append(section)
        else:
            # Attach content to the innermost active section.
            stack[-1].elements.append(element)

    return root


def detect_title(text_elems: dict[int, list[Element]]) -> str:
    """Highest-confidence heading on page 0 (1)."""
    first_page = text_elems.get(1, [])
    headings = [e for e in first_page if e.kind == ElementKind.heading]
    if not headings:
        return ""
    headings.sort(key=lambda e: (e.confidence, -e.level), reverse=True)
    return headings[0].text


def detect_subtitle(text_elems: dict[int, list[Element]], title: str) -> str:
    """First meaningful text element after the title on page 0 (1)."""
    first_page = text_elems.get(1, [])
    seen_title = not title
    for e in first_page:
        if not seen_title and e.kind == ElementKind.heading and e.text == title:
            seen_title = True
            continue
        if seen_title and e.kind == ElementKind.text and len(e.text.split()) >= 3:
            return e.text
    return ""
