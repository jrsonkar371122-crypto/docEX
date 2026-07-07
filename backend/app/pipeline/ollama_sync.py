"""Synchronous Ollama helpers used by the Celery ingestion pipeline.

Mirrors app.services.llm_service but uses a blocking httpx client so it can be
called from synchronous Celery tasks. The VLM is generic: vision requests use
``settings.vlm_model`` and honour ``settings.vlm_enabled``.
"""
from __future__ import annotations

import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger("documind.pipeline.ollama")

_MAX_RETRIES = 3
_BASE_BACKOFF = 1.5
_TIMEOUT = httpx.Timeout(600.0, connect=15.0)


class OllamaError(RuntimeError):
    pass


def _post_with_retry(url: str, payload: dict) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.Client(base_url=settings.ollama_host, timeout=_TIMEOUT) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            wait = _BASE_BACKOFF ** attempt
            logger.warning(
                "Ollama sync call failed (%d/%d) %s: %s — retry in %.1fs",
                attempt, _MAX_RETRIES, url, exc, wait,
            )
            time.sleep(wait)
    raise OllamaError(f"Ollama request to {url} failed after {_MAX_RETRIES} attempts: {last_exc}")


def embed(text: str) -> list[float]:
    payload = {"model": settings.embedding_model, "prompt": text}
    resp = _post_with_retry("/api/embeddings", payload)
    vector = resp.json().get("embedding")
    if not vector:
        raise OllamaError("Embedding response missing 'embedding'.")
    return vector


def vision_available() -> bool:
    return settings.vlm_enabled


def describe_image(image_b64: str, prompt: str) -> str:
    """Describe an image with the generic VLM. Empty string if disabled."""
    if not settings.vlm_enabled:
        return ""
    payload = {
        "model": settings.vlm_model,
        "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": settings.vlm_max_new_tokens},
    }
    resp = _post_with_retry("/api/chat", payload)
    return (resp.json().get("message") or {}).get("content", "").strip()


def transcribe_page(image_b64: str) -> str:
    """Transcribe a scanned page image with the generic VLM. Empty if disabled."""
    prompt = (
        "Extract ALL text from this scanned document page. "
        "Preserve reading order, headings, lists and paragraph structure. "
        "Output only the extracted text with no commentary."
    )
    return describe_image(image_b64, prompt)
