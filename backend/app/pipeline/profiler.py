"""Phase 0 — PDF profiling: scanned vs digital, font-size heading hierarchy."""
from __future__ import annotations

import logging
import subprocess
from collections import Counter

import fitz  # PyMuPDF

from app.pipeline.elements import DocumentProfile

logger = logging.getLogger("documind.pipeline.profiler")

# Heading level thresholds relative to body font size (points).
_TITLE_DELTA = 6.0
_H_MAJOR_DELTA = 2.5
_H_MINOR_DELTA = 1.0


def _detect_scanned_via_pdffonts(pdf_path: str) -> bool | None:
    """Return True if pdffonts reports no embedded fonts (likely scanned)."""
    try:
        result = subprocess.run(
            ["pdffonts", pdf_path],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    # Header is two lines; any additional line means fonts exist.
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) <= 2:
        return True
    return False


def profile_pdf(pdf_path: str) -> DocumentProfile:
    """Analyse the PDF and produce a DocumentProfile."""
    doc = fitz.open(pdf_path)
    page_count = doc.page_count

    font_sizes: Counter[float] = Counter()
    per_page_chars: list[int] = []
    scanned_pages: set[int] = set()
    pages_with_image: set[int] = set()

    for page_index in range(page_count):
        page = doc[page_index]
        page_number = page_index + 1

        text_dict = page.get_text("dict")
        char_count = 0
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    char_count += len(text.strip())
                    size = round(float(span.get("size", 0.0)), 1)
                    if text.strip() and size > 0:
                        font_sizes[size] += len(text.strip())
        per_page_chars.append(char_count)

        # Very little text on a page → treat as scanned/needs-OCR.
        if char_count < 30:
            scanned_pages.add(page_number)

        if page.get_images(full=True):
            pages_with_image.add(page_number)

    doc.close()

    avg_chars = sum(per_page_chars) / page_count if page_count else 0.0
    is_slide_deck = avg_chars < 200

    pdffonts_scanned = _detect_scanned_via_pdffonts(pdf_path)
    if pdffonts_scanned is True:
        scanned_pages = set(range(1, page_count + 1))

    # Body font size = the size covering the most characters.
    if font_sizes:
        body_font_size = font_sizes.most_common(1)[0][0]
    else:
        body_font_size = 12.0

    heading_map = _build_heading_map(font_sizes, body_font_size)

    scanned_ratio = len(scanned_pages) / page_count if page_count else 0
    if scanned_ratio == 0:
        doc_type = "digital"
    elif scanned_ratio >= 0.9:
        doc_type = "scanned"
    else:
        doc_type = "mixed"

    logger.info(
        "Profiled %s: type=%s pages=%d body=%.1fpt headings=%s scanned=%d",
        pdf_path, doc_type, page_count, body_font_size, heading_map, len(scanned_pages),
    )

    return DocumentProfile(
        doc_type=doc_type,
        page_count=page_count,
        body_font_size=body_font_size,
        heading_map=heading_map,
        scanned_pages=scanned_pages,
        pages_with_image=pages_with_image,
        avg_chars_per_page=avg_chars,
        is_slide_deck=is_slide_deck,
    )


def _build_heading_map(
    font_sizes: Counter[float], body: float
) -> dict[float, int]:
    """Map each distinct font size larger than body to a heading level.

    Level 0 = TITLE, then H1..H6. Levels are assigned by descending size so the
    largest heading size becomes the shallowest level.
    """
    candidate_sizes = sorted(
        {s for s in font_sizes if s - body >= _H_MINOR_DELTA}, reverse=True
    )
    heading_map: dict[float, int] = {}
    for rank, size in enumerate(candidate_sizes):
        delta = size - body
        if delta >= _TITLE_DELTA:
            level = 0
        elif delta >= _H_MAJOR_DELTA:
            # Larger major headings get shallower levels (min level 1).
            level = 1 + min(rank, 2)
        else:
            level = 4 + min(rank, 2)
        heading_map[size] = min(level, 6)
    return heading_map
