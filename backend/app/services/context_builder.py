"""Assemble RAG prompts: conversation history + retrieved context + citations."""
from __future__ import annotations

import re

from app.config import settings
from app.models.chat import ChatMessage, MessageRole
from app.services.llm_service import build_gemma_prompt
from app.services.retrieval import RetrievedChunk

# Pronouns/demonstratives that indicate a follow-up question.
_FOLLOWUP_MARKERS = {
    "it", "its", "that", "this", "those", "these", "they", "them",
    "their", "the above", "the previous", "he", "she",
}
_WORD_RE = re.compile(r"[A-Za-z']+")


def is_followup(question: str) -> bool:
    """True if the question likely depends on prior conversation context."""
    lowered = question.lower()
    if "the above" in lowered or "the previous" in lowered:
        return True
    words = set(_WORD_RE.findall(lowered))
    return bool(words & _FOLLOWUP_MARKERS)


def resolve_query(question: str, history: list[ChatMessage]) -> str:
    """Prepend the previous user turn to the query when it's a follow-up."""
    if not is_followup(question):
        return question
    for msg in reversed(history):
        if msg.role == MessageRole.user:
            return f"{msg.content}\n{question}"
    return question


def _format_context(chunks: list[RetrievedChunk]) -> str:
    lines: list[str] = []
    for i, c in enumerate(chunks, start=1):
        page = c.page_number if c.page_number is not None else "?"
        section = c.section_path or "(document body)"
        body = c.content.strip()
        if c.has_image:
            body = f"[Figure: {body}]"
        lines.append(f"[{i}] {section} (p.{page}): {body}")
    return "\n\n".join(lines)


def _format_history(history: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in history:
        speaker = "User" if msg.role == MessageRole.user else "Assistant"
        lines.append(f"{speaker}: {msg.content.strip()}")
    return "\n".join(lines)


def build_rag_prompt(
    question: str,
    chunks: list[RetrievedChunk],
    history: list[ChatMessage],
) -> str:
    """Build the final Gemma-templated prompt for RAG answering."""
    context_block = _format_context(chunks) if chunks else "(no relevant context found)"
    history_block = _format_history(history) if history else "(no prior conversation)"

    user_prompt = (
        "CONTEXT:\n"
        f"{context_block}\n\n"
        "CONVERSATION HISTORY:\n"
        f"{history_block}\n\n"
        f"QUESTION: {question}\n\n"
        "Instructions: Answer only from the context above. "
        "Cite sources as [N]. If the answer is not in the context, respond: "
        '"This information was not found in the available manuals."'
    )
    return build_gemma_prompt(user_prompt)


def build_title_prompt(first_message: str) -> str:
    """Prompt to auto-generate a short session title from the first message."""
    user_prompt = (
        "Generate a concise chat title (maximum 6 words, no quotes, no trailing "
        "punctuation) summarizing this question:\n\n"
        f"{first_message}"
    )
    return build_gemma_prompt(user_prompt)


def trim_history(history: list[ChatMessage]) -> list[ChatMessage]:
    """Keep only the most recent configured number of messages."""
    return history[-settings.history_turns :]
