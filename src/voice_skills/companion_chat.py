from __future__ import annotations

from collections.abc import Iterable

from src.providers.llm import stream_companion_answer_text


def stream_companion_chat(text: str, answer_mode: str | None = None) -> Iterable[str]:
    return stream_companion_answer_text(text, answer_mode=answer_mode)
