"""Ollama client: chat, streaming chat, embeddings and vision (VLM) calls.

The vision model (VLM) is fully generic and decoupled from the chat LLM.
It is selected via ``settings.vlm_model`` and can be any multimodal model
served by the Ollama instance (gemma3, llava, llama3.2-vision, qwen2.5vl, ...),
or disabled entirely via ``settings.vlm_enabled``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("documind.llm")

# Gemma chat-template markers. Applied only for gemma* chat models; other
# model families use Ollama's own template via the /api/chat endpoint.
GEMMA_TURN_USER = "<start_of_turn>user\n"
GEMMA_TURN_MODEL = "<start_of_turn>model\n"
GEMMA_TURN_END = "<end_of_turn>\n"

_MAX_RETRIES = 3
_BASE_BACKOFF = 1.5
_REQUEST_TIMEOUT = httpx.Timeout(600.0, connect=15.0)


class OllamaError(RuntimeError):
    """Raised when Ollama cannot fulfil a request after retries."""


def _is_gemma(model: str) -> bool:
    return model.lower().startswith("gemma")


def build_gemma_prompt(user_prompt: str) -> str:
    """Wrap a single-turn user prompt in the Gemma chat template."""
    return f"{GEMMA_TURN_USER}{user_prompt}{GEMMA_TURN_END}{GEMMA_TURN_MODEL}"


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPError, httpx.HTTPStatusError) as exc:  # noqa: PERF203
            last_exc = exc
            wait = _BASE_BACKOFF ** attempt
            logger.warning(
                "Ollama call failed (attempt %d/%d) on %s: %s — retrying in %.1fs",
                attempt, _MAX_RETRIES, url, exc, wait,
            )
            await asyncio.sleep(wait)
    raise OllamaError(f"Ollama request to {url} failed after {_MAX_RETRIES} attempts: {last_exc}")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return embeddings for a batch of texts using the embedding model.

    Retries each text up to 3 times with exponential backoff. Callers batch
    upstream; this issues one request per text against /api/embeddings.
    """
    embeddings: list[list[float]] = []
    async with httpx.AsyncClient(base_url=settings.ollama_host, timeout=_REQUEST_TIMEOUT) as client:
        for text in texts:
            payload = {"model": settings.embedding_model, "prompt": text}
            resp = await _post_with_retry(client, "/api/embeddings", payload)
            data = resp.json()
            vector = data.get("embedding")
            if not vector:
                raise OllamaError("Embedding response missing 'embedding' field.")
            embeddings.append(vector)
    return embeddings


async def embed_text(text: str) -> list[float]:
    """Embed a single text (e.g. a search query)."""
    result = await embed_texts([text])
    return result[0]


async def chat(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    images_b64: list[str] | None = None,
) -> str:
    """Non-streaming chat completion. Returns the full assistant text."""
    model = model or settings.llm_model
    max_tokens = max_tokens or settings.llm_max_new_tokens

    message: dict[str, Any] = {"role": "user", "content": prompt}
    if images_b64:
        message["images"] = images_b64

    payload = {
        "model": model,
        "messages": [message],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": settings.llm_ctx_tokens,
            "num_predict": max_tokens,
        },
    }
    async with httpx.AsyncClient(base_url=settings.ollama_host, timeout=_REQUEST_TIMEOUT) as client:
        resp = await _post_with_retry(client, "/api/chat", payload)
        data = resp.json()
    return (data.get("message") or {}).get("content", "").strip()


async def chat_stream(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> AsyncGenerator[str, None]:
    """Stream assistant tokens from Ollama's /api/chat endpoint."""
    model = model or settings.llm_model
    max_tokens = max_tokens or settings.llm_max_new_tokens

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "options": {
            "temperature": temperature,
            "num_ctx": settings.llm_ctx_tokens,
            "num_predict": max_tokens,
        },
    }

    async with httpx.AsyncClient(base_url=settings.ollama_host, timeout=_REQUEST_TIMEOUT) as client:
        async with client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                import json

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = (event.get("message") or {}).get("content", "")
                if token:
                    yield token
                if event.get("done"):
                    break


async def describe_image(image_b64: str, prompt: str) -> str:
    """Describe an image using the generic VLM.

    Returns an empty string when vision is disabled so callers can gracefully
    degrade (e.g. skip figure descriptions). Uses ``settings.vlm_model`` — any
    multimodal Ollama model — never assuming it equals the chat LLM.
    """
    if not settings.vlm_enabled:
        logger.info("VLM disabled (VLM_ENABLED=false); skipping image description.")
        return ""

    payload = {
        "model": settings.vlm_model,
        "messages": [
            {"role": "user", "content": prompt, "images": [image_b64]},
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": settings.vlm_max_new_tokens,
        },
    }
    async with httpx.AsyncClient(base_url=settings.ollama_host, timeout=_REQUEST_TIMEOUT) as client:
        resp = await _post_with_retry(client, "/api/chat", payload)
        data = resp.json()
    return (data.get("message") or {}).get("content", "").strip()


async def transcribe_page_image(image_b64: str) -> str:
    """Transcribe a rasterized scanned page using the generic VLM.

    Returns an empty string when vision is disabled so callers fall back to
    Tesseract OCR.
    """
    prompt = (
        "Extract ALL text from this scanned document page. "
        "Preserve the reading order, headings, lists and paragraph structure. "
        "Output only the extracted text with no commentary."
    )
    return await describe_image(image_b64, prompt)


def stream_chunks_to_iterator(gen: AsyncGenerator[str, None]) -> AsyncIterator[str]:
    """Adapter so a generator can be consumed as an async iterator."""
    return gen.__aiter__()
