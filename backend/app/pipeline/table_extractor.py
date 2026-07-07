"""Phase 2 — table extraction: camelot lattice → stream → pdfplumber fallback."""
from __future__ import annotations

import hashlib
import logging
import re

import pdfplumber

from app.pipeline.elements import Element, ElementKind

logger = logging.getLogger("documind.pipeline.table_extractor")

_TABLE_CAPTION_RE = re.compile(r"table\s+\d+", re.IGNORECASE)


def _rows_to_markdown(rows: list[list[str]]) -> str:
    """Convert a list of rows to a GitHub-flavoured markdown table."""
    cleaned = [
        [(cell or "").replace("\n", " ").strip() for cell in row] for row in rows
    ]
    if not cleaned:
        return ""
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]

    header = cleaned[0]
    body = cleaned[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _table_signature(rows: list[list[str]]) -> str:
    sample = rows[:11]
    flat = "|".join("~".join((c or "").strip() for c in row) for row in sample)
    return hashlib.md5(flat.encode("utf-8")).hexdigest()


def _camelot_tables(pdf_path: str, flavor: str, **kwargs) -> list[tuple[int, list[list[str]]]]:
    """Run camelot with the given flavor; return (page_number, rows) tuples."""
    try:
        import camelot
    except Exception as exc:  # noqa: BLE001
        logger.warning("camelot unavailable (%s); skipping %s extraction.", exc, flavor)
        return []
    out: list[tuple[int, list[list[str]]]] = []
    try:
        tables = camelot.read_pdf(pdf_path, pages="all", flavor=flavor, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("camelot %s failed: %s", flavor, exc)
        return []
    for table in tables:
        rows = table.df.values.tolist()
        if len(rows) < 2:
            continue
        out.append((int(table.page), rows))
    return out


def _pdfplumber_tables(
    pdf_path: str, covered_pages: set[int]
) -> list[tuple[int, list[list[str]]]]:
    """Fallback extraction for pages not covered by camelot."""
    out: list[tuple[int, list[list[str]]]] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for idx, page in enumerate(pdf.pages):
                page_number = idx + 1
                if page_number in covered_pages:
                    continue
                for table in page.extract_tables():
                    if table and len(table) >= 2:
                        out.append((page_number, table))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdfplumber table fallback failed: %s", exc)
    return out


def _find_caption(pdf_path: str, page_number: int) -> str:
    """Scan a page for a 'Table N' caption line."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_number - 1 >= len(pdf.pages):
                return ""
            text = pdf.pages[page_number - 1].extract_text() or ""
    except Exception:  # noqa: BLE001
        return ""
    for line in text.splitlines():
        if _TABLE_CAPTION_RE.search(line):
            return line.strip()
    return ""


def extract_tables(pdf_path: str) -> dict[int, list[Element]]:
    """Extract tables from every page, deduplicated and merged across pages."""
    collected: list[tuple[int, list[list[str]]]] = []
    covered: set[int] = set()

    lattice = _camelot_tables(pdf_path, "lattice")
    for page, rows in lattice:
        collected.append((page, rows))
        covered.add(page)

    stream = _camelot_tables(pdf_path, "stream", edge_tol=500, row_tol=15)
    for page, rows in stream:
        if page in covered:
            continue
        collected.append((page, rows))
        covered.add(page)

    for page, rows in _pdfplumber_tables(pdf_path, covered):
        collected.append((page, rows))

    # Dedup by signature, then cross-page merge of identical headers.
    seen: set[str] = set()
    deduped: list[tuple[int, list[list[str]]]] = []
    for page, rows in sorted(collected, key=lambda x: x[0]):
        sig = _table_signature(rows)
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append((page, rows))

    merged = _merge_cross_page(deduped)

    elements_by_page: dict[int, list[Element]] = {}
    for page, rows in merged:
        markdown = _rows_to_markdown(rows)
        if not markdown:
            continue
        caption = _find_caption(pdf_path, page)
        elements_by_page.setdefault(page, []).append(
            Element(
                kind=ElementKind.table,
                page=page,
                text=markdown,
                caption=caption,
            )
        )
    return elements_by_page


def _merge_cross_page(
    tables: list[tuple[int, list[list[str]]]]
) -> list[tuple[int, list[list[str]]]]:
    """Merge tables on consecutive pages that share an identical header row."""
    if not tables:
        return []
    merged: list[tuple[int, list[list[str]]]] = []
    for page, rows in tables:
        if merged:
            prev_page, prev_rows = merged[-1]
            same_header = prev_rows[0] == rows[0]
            consecutive = page == prev_page + 1
            if same_header and consecutive:
                merged[-1] = (prev_page, prev_rows + rows[1:])
                continue
        merged.append((page, rows))
    return merged
