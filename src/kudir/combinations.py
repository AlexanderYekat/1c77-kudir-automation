"""Ограниченный комбинаторный поиск. Для уникальных блокировок не вызывается."""

from __future__ import annotations

from kudir.scoring import ScoringConfig


def cap_candidates(candidates: list, cfg: ScoringConfig) -> tuple[list, bool]:
    limit = cfg.s("max_candidates")
    if len(candidates) <= limit:
        return candidates, False
    return candidates[:limit], True
