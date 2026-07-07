"""Phase 1 — text extraction and heading classification.

Digital pages use PyMuPDF block extraction with a font-size-driven heading
classifier. Scanned pages are rasterized and sent to the generic VLM, falling
back to Tesseract OCR when vision is unavailable.
"""
from __future__ import annotations

import io
import logging
import re
from base64 import b64encode
from collections import Counter

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from app.pipeline import ollama_sync
from app.pipeline.elements import DocumentProfile, Element, ElementKind

logger = logging.getLogger("documind.pipeline.extractor")

_NUMBERED_RE = re.compile(r"^\s*(\d+(\.\d+)*)([.)]|\s)")
_RASTER_DPI = 300


def _rasterize_page(page: "fitz.Page", dpi: int = _RASTER_DPI) -> bytes:
    """Render a page to PNG bytes at the given DPI."""
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def _classify_block(
    text: str,
    avg_size: float,
    bold_ratio: float,
    profile: DocumentProfile,
) -> tuple[bool, int, float]:
    """Score a block as heading vs body. Returns (is_heading, level, score)."""
    rounded = round(avg_size, 1)
    level = None
    # Nearest known heading size within 0.6pt tolerance.
    for size, lvl in profile.heading_map.items():
        if abs(size - rounded) <= 0.6:
            level = lvl
            break

    word_count = len(text.split())
    score = 0.0
    if level is not None:
        score += 0.5
    if bold_ratio >= 0.6:
        score += 0.2
    if word_count < 15:
        score += 0.1
    if _NUMBERED_RE.match(text):
        score += 0.15
    if text.isupper() and word_count <= 8:
        score += 0.1

    if level is None:
        # Even without a font match, strong signals can promote to a low heading.
        if score >= 0.45 and word_count <= 12:
            level = 3
        else:
            return (False, 0, score)

    # Demote weak headings back to body text.
    if score < 0.45:
        return (False, 0, score)
    return (True, level, score)


def _extract_digital_page(
    page: "fitz.Page", page_number: int, profile: DocumentProfile
) -> tuple[list[Element], str]:
    """Extract classified elements from a digital page in reading order."""
    text_dict = page.get_text("dict")
    page_width = page.rect.width
    mid_x = page_width / 2.0

    raw_blocks = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        spans = [
            span
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if span.get("text", "").strip()
        ]
        if not spans:
            continue
        text = " ".join(s["text"].strip() for s in spans).strip()
        if not text:
            continue
        sizes = [float(s.get("size", 0.0)) for s in spans]
        avg_size = sum(sizes) / len(sizes)
        bold_count = sum(
            1 for s in spans if "bold" in str(s.get("font", "")).lower()
            or (int(s.get("flags", 0)) & 2 ** 4)
        )
        bold_ratio = bold_count / len(spans)
        bbox = block.get("bbox", [0, 0, 0, 0])
        raw_blocks.append(
            {
                "text": text,
                "avg_size": avg_size,
                "bold_ratio": bold_ratio,
                "x0": bbox[0],
                "y0": bbox[1],
            }
        )

    # Reading order: left column first (by y), then right column (by y).
    left = [b for b in raw_blocks if b["x0"] < mid_x]
    right = [b for b in raw_blocks if b["x0"] >= mid_x]
    left.sort(key=lambda b: b["y0"])
    right.sort(key=lambda b: b["y0"])
    ordered = left + right

    elements: list[Element] = []
    page_text_parts: list[str] = []
    for b in ordered:
        is_heading, level, score = _classify_block(
            b["text"], b["avg_size"], b["bold_ratio"], profile
        )
        page_text_parts.append(b["text"])
        if is_heading:
            elements.append(
                Element(
                    kind=ElementKind.heading,
                    page=page_number,
                    text=b["text"],
                    level=level,
                    confidence=score,
                    y0=b["y0"],
                )
            )
        else:
            elements.append(
                Element(
                    kind=ElementKind.text,
                    page=page_number,
                    text=b["text"],
                    y0=b["y0"],
                )
            )

    return elements, "\n".join(page_text_parts)


def _remove_headers_footers(
    page_texts: dict[int, list[str]], page_count: int
) -> None:
    """Detect and strip cross-page repeated header/footer lines in place."""
    threshold = max(2, page_count // 3)
    line_counts: Counter[str] = Counter()
    for lines in page_texts.values():
        # Consider only the first and last two lines as header/footer candidates.
        candidates = lines[:2] + lines[-2:]
        for ln in candidates:
            norm = ln.strip()
            if 0 < len(norm) <= 120:
                line_counts[norm] += 1

    repeated = {ln for ln, cnt in line_counts.items() if cnt >= threshold}
    if not repeated:
        return
    for page in page_texts:
        page_texts[page] = [ln for ln in page_texts[page] if ln.strip() not in repeated]


def _ocr_page_image(png_bytes: bytes) -> str:
    """Tesseract OCR fallback for scanned pages."""
    try:
        image = Image.open(io.BytesIO(png_bytes))
        return pytesseract.image_to_string(image).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tesseract OCR failed: %s", exc)
        return ""


def extract_text(
    pdf_path: str, profile: DocumentProfile
) -> tuple[dict[int, list[Element]], dict[int, str], set[int]]:
    """Extract text elements per page.

    Returns (elements_by_page, raw_text_by_page, ocr_pages).
    """
    doc = fitz.open(pdf_path)
    elements_by_page: dict[int, list[Element]] = {}
    raw_text_by_page: dict[int, str] = {}
    header_footer_source: dict[int, list[str]] = {}
    ocr_pages: set[int] = set()

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_number = page_index + 1

        if page_number in profile.scanned_pages:
            png = _rasterize_page(page)
            image_b64 = b64encode(png).decode("ascii")
            text = ""
            if ollama_sync.vision_available():
                text = ollama_sync.transcribe_page(image_b64)
            if not text:
                text = _ocr_page_image(png)
            ocr_pages.add(page_number)
            raw_text_by_page[page_number] = text
            header_footer_source[page_number] = text.splitlines()
            elements = _scanned_text_to_elements(text, page_number)
            elements_by_page[page_number] = elements
        else:
            elements, page_text = _extract_digital_page(page, page_number, profile)
            elements_by_page[page_number] = elements
            raw_text_by_page[page_number] = page_text
            header_footer_source[page_number] = page_text.splitlines()

    doc.close()

    _remove_headers_footers(header_footer_source, profile.page_count)
    for page_number, lines in header_footer_source.items():
        raw_text_by_page[page_number] = "\n".join(lines)

    return elements_by_page, raw_text_by_page, ocr_pages


def _scanned_text_to_elements(text: str, page_number: int) -> list[Element]:
    """Turn transcribed/OCR text into heading + text elements heuristically."""
    elements: list[Element] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        word_count = len(line.split())
        is_heading = (
            (line.isupper() and word_count <= 10)
            or bool(_NUMBERED_RE.match(line)) and word_count <= 12
        )
        if is_heading:
            elements.append(
                Element(
                    kind=ElementKind.heading,
                    page=page_number,
                    text=line,
                    level=2,
                    confidence=0.5,
                )
            )
        else:
            elements.append(
                Element(kind=ElementKind.text, page=page_number, text=line)
            )
    return elements
