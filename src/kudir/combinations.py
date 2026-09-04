"""Ограниченный комбинаторный поиск внутри контрагента.

Порядок: cheap/pre-score → сортировка → top N → поиск комбинаций сумм.
timeout_seconds_per_counterparty прерывает перебор.
"""

from __future__ import annotations

import time
from typing import Any

from kudir.scoring import ScoringConfig


def _score_of(item: Any) -> int:
    if isinstance(item, tuple) and len(item) > 1:
        bd = item[1]
        total = getattr(bd, "total", None)
        if total is not None:
            return int(total)
        if isinstance(bd, (int, float)):
            return int(bd)
    return 0


def cap_candidates(candidates: list, cfg: ScoringConfig) -> tuple[list, bool]:
    """Сначала сортировка по score, потом обрезка. Не наоборот."""
    limit = cfg.s("max_candidates")
    ordered = sorted(candidates, key=_score_of, reverse=True)
    if len(ordered) <= limit:
        return ordered, False
    return ordered[:limit], True


def search_combination(
    items: list[tuple[Any, int]],
    target: int,
    *,
    deadline: float,
    exact: bool,
    min_parts: int = 2,
) -> tuple[list | None, bool]:
    """Уникальное лучшее покрытие target суммами документов.

    items: (payload, amount). Подмножества длины >= min_parts.
    exact=True — сумма должна равняться target (долг).
    exact=False — максимум суммы <= target (частичное закрытие аванса).
    Если несколько подмножеств с одинаковой лучшей суммой — None (неоднозначно).
    Второй элемент: истек ли timeout.
    """
    n = len(items)
    if n < min_parts or target <= 0:
        return None, False
    best_sum = -1
    best_mask = 0
    ties = 0
    timed_out = False
    for mask in range(1, 1 << n):
        if time.monotonic() >= deadline:
            timed_out = True
            break
        if mask.bit_count() < min_parts:
            continue
        total = 0
        over = False
        bit = 1
        for i in range(n):
            if mask & bit:
                total += items[i][1]
                if total > target:
                    over = True
                    break
            bit <<= 1
        if over:
            continue
        if exact and total != target:
            continue
        if total > best_sum:
            best_sum = total
            best_mask = mask
            ties = 1
        elif total == best_sum:
            ties += 1
    if timed_out:
        return None, True
    if best_sum <= 0 or ties != 1:
        return None, False
    chosen = [items[i][0] for i in range(n) if best_mask & (1 << i)]
    return chosen, False
