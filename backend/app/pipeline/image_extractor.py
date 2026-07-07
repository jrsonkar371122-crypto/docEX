"""Phase 3 — image extraction and generic-VLM description."""
from __future__ import annotations

import logging
import os
import re
from base64 import b64encode

import fitz  # PyMuPDF

from app.config import settings
from app.pipeline import ollama_sync
from app.pipeline.elements import Element, ElementKind

logger = logging.getLogger("documind.pipeline.image_extractor")

_MIN_DIM = 50
_FIGURE_CAPTION_RE = re.compile(r"figure\s+\d+", re.IGNORECASE)

_DESCRIBE_PROMPT = (
    "Describe this image extracted from a technical PDF. "
    "State the visual type, key data/labels/values, axes if it is a chart, "
    "components if it is a diagram, and all visible text. Be specific."
)


def _nearest_caption(page: "fitz.Page", image_bbox: fitz.Rect) -> str:
    """Find a 'Figure N' caption within ~120px below the image bounding box."""
    text_dict = page.get_text("dict")
    best = ""
    best_dist = 121.0
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = fitz.Rect(block.get("bbox", [0, 0, 0, 0]))
        line_text = " ".join(
            span.get("text", "")
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ).strip()
        if not _FIGURE_CAPTION_RE.search(line_text):
            continue
        dist = bbox.y0 - image_bbox.y1
        if 0 <= dist <= best_dist:
            best_dist = dist
            best = line_text
    return best


def extract_images(pdf_path: str, document_id: str) -> dict[int, list[Element]]:
    """Extract raster images, describe them via the generic VLM, associate captions."""
    doc = fitz.open(pdf_path)
    elements_by_page: dict[int, list[Element]] = {}
    image_out_dir = os.path.join(settings.image_dir, document_id)
    os.makedirs(image_out_dir, exist_ok=True)

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_number = page_index + 1

        for img_ix, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                base = doc.extract_image(xref)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to extract image xref=%s: %s", xref, exc)
                continue
            width = base.get("width", 0)
            height = base.get("height", 0)
            if width < _MIN_DIM or height < _MIN_DIM:
                continue  # decorative

            image_bytes = base["image"]
            ext = base.get("ext", "png")
            img_path = os.path.join(
                image_out_dir, f"p{page_number}_{img_ix}.{ext}"
            )
            with open(img_path, "wb") as fh:
                fh.write(image_bytes)

            description = ""
            if ollama_sync.vision_available():
                image_b64 = b64encode(image_bytes).decode("ascii")
                description = ollama_sync.describe_image(image_b64, _DESCRIBE_PROMPT)
            if not description:
                # Vision disabled/unavailable: keep a minimal placeholder note.
                description = "Image (no description available — VLM disabled)."

            # Locate the image bbox on the page for caption association.
            try:
                rects = page.get_image_rects(xref)
                bbox = rects[0] if rects else fitz.Rect(0, 0, 0, 0)
            except Exception:  # noqa: BLE001
                bbox = fitz.Rect(0, 0, 0, 0)
            caption = _nearest_caption(page, bbox)

            elements_by_page.setdefault(page_number, []).append(
                Element(
                    kind=ElementKind.image,
                    page=page_number,
                    text=description,
                    caption=caption,
                    y0=float(bbox.y0),
                )
            )

    doc.close()
    return elements_by_page
