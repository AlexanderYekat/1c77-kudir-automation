"""Semantic reranker в версии 1 не обязателен: всегда 0, модель не вызывается."""

from __future__ import annotations


def semantic_score(_payment_text: str, _document_text: str, max_score: int) -> int:
    return 0
