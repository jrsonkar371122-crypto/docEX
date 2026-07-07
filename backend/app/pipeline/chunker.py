"""Phase 5 — semantic chunker: walk the tree, produce breadcrumb-rich chunks."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.pipeline.elements import Element, ElementKind, Section

logger = logging.getLogger("documind.pipeline.chunker")

_TARGET_WORDS = 400
_HARD_CAP_WORDS = 800
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_XREF_RE = re.compile(r"(figure|table)\s+\d+", re.IGNORECASE)


@dataclass
class BuiltChunk:
    content: str
    section_path: str
    page_numbers: list[int]
    has_table: bool = False
    has_image: bool = False
    token_count: int = 0


@dataclass
class _Accumulator:
    breadcrumb: str
    heading: str
    body_words: list[str] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)
    tables: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    xrefs: set[str] = field(default_factory=set)


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text.strip()) if s]


def _estimate_tokens(text: str) -> int:
    # Rough heuristic ~1.3 tokens per word.
    return int(len(text.split()) * 1.3) + 1


def chunk_document(root: Section) -> list[BuiltChunk]:
    """Depth-first walk producing chunks; tables/images appended to last sub-chunk."""
    chunks: list[BuiltChunk] = []
    _walk(root, [], chunks)
    for i, ch in enumerate(chunks):
        ch.token_count = _estimate_tokens(ch.content)
    return chunks


def _walk(section: Section, ancestors: list[str], out: list[BuiltChunk]) -> None:
    breadcrumb = section.breadcrumb(ancestors)

    body_text_parts: list[str] = []
    pages: set[int] = set()
    tables: list[Element] = []
    images: list[Element] = []
    for el in section.elements:
        pages.add(el.page)
        if el.kind == ElementKind.text:
            body_text_parts.append(el.text)
        elif el.kind == ElementKind.table:
            tables.append(el)
        elif el.kind == ElementKind.image:
            images.append(el)

    if body_text_parts or tables or images:
        section_chunks = _build_section_chunks(
            breadcrumb, section.title, body_text_parts, pages, tables, images
        )
        out.extend(section_chunks)

    child_ancestors = ancestors + ([section.title] if section.title else [])
    for child in section.children:
        _walk(child, child_ancestors, out)


def _build_section_chunks(
    breadcrumb: str,
    heading: str,
    body_parts: list[str],
    pages: set[int],
    tables: list[Element],
    images: list[Element],
) -> list[BuiltChunk]:
    """Split body text at sentence boundaries into ~400-word sub-chunks."""
    body = " ".join(p.strip() for p in body_parts if p.strip()).strip()
    sub_bodies: list[str] = []

    if body:
        sentences = _split_sentences(body)
        current: list[str] = []
        current_words = 0
        for sentence in sentences:
            w = len(sentence.split())
            if current and current_words + w > _TARGET_WORDS:
                sub_bodies.append(" ".join(current))
                current, current_words = [], 0
            current.append(sentence)
            current_words += w
            # Hard cap enforcement mid-sentence-group.
            if current_words >= _HARD_CAP_WORDS:
                sub_bodies.append(" ".join(current))
                current, current_words = [], 0
        if current:
            sub_bodies.append(" ".join(current))

    if not sub_bodies:
        # Section with only tables/images and no body text.
        sub_bodies = [""]

    # Enforce hard cap by slicing any over-long sub-body.
    capped: list[str] = []
    for sb in sub_bodies:
        words = sb.split()
        if len(words) > _HARD_CAP_WORDS:
            for i in range(0, len(words), _HARD_CAP_WORDS):
                capped.append(" ".join(words[i : i + _HARD_CAP_WORDS]))
        else:
            capped.append(sb)
    sub_bodies = capped

    page_list = sorted(pages)
    xrefs: set[str] = set()
    for el in tables + images:
        for m in _XREF_RE.findall(el.text + " " + el.caption):
            xrefs.add(" ".join(m).title() if isinstance(m, tuple) else m)

    chunks: list[BuiltChunk] = []
    for idx, sub_body in enumerate(sub_bodies):
        is_last = idx == len(sub_bodies) - 1
        parts: list[str] = []
        if breadcrumb:
            parts.append(f"Section: {breadcrumb}")
        elif heading:
            parts.append(f"Section: {heading}")
        if sub_body:
            parts.append(sub_body)

        has_table = False
        has_image = False
        if is_last:
            for t in tables:
                cap = f"{t.caption}\n" if t.caption else ""
                parts.append(f"{cap}{t.text}")
                has_table = True
            for im in images:
                cap = f"{im.caption}: " if im.caption else ""
                parts.append(f"[Figure: {cap}{im.text}]")
                has_image = True
            if xrefs:
                parts.append("References: " + ", ".join(sorted(xrefs)))

        content = "\n\n".join(p for p in parts if p).strip()
        if not content:
            continue
        chunks.append(
            BuiltChunk(
                content=content,
                section_path=breadcrumb or heading,
                page_numbers=page_list,
                has_table=has_table,
                has_image=has_image,
            )
        )
    return chunks
