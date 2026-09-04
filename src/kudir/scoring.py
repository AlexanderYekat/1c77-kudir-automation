"""Единственный runtime-источник весов и порогов: config/scoring.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ScoringError(Exception):
    """Нет YAML или в нём нет обязательных ключей."""


WEIGHT_KEYS = (
    "direct_exact",
    "invoice_base_exact",
    "cash_base_exact",
    "exact_remainder",
    "exact_full_amount",
    "exact_combination",
    "direct_hint",
    "same_invoice",
    "same_contract",
    "different_contract",
    "unknown_contract",
    "same_calculation_type",
    "goods_or_service_match",
    "semantic_max",
    "chronology_max",
)

CONFIDENCE_KEYS = (
    "high_min_score",
    "high_min_gap",
    "medium_min_score",
    "medium_min_gap",
    "low_min_score",
    "low_min_gap",
)

SEARCH_KEYS = (
    "max_candidates",
    "timeout_seconds_per_counterparty",
)


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "\"'" and text[-1] == text[0]:
        return text[1:-1]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def load_simple_yaml(path: Path) -> dict[str, Any]:
    """Разбор YAML с одним уровнем вложенности (секции scoring.yaml)."""
    root: dict[str, Any] = {}
    section: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.endswith(":") and not line.startswith(" ") and not line.startswith("\t"):
            section = line[:-1].strip()
            root[section] = {}
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = _parse_scalar(value)
        if section is not None and (raw_line.startswith("  ") or raw_line.startswith("\t")):
            root[section][key] = value
        else:
            section = None
            root[key] = value
    return root


@dataclass(frozen=True)
class ScoringConfig:
    weights: dict[str, int]
    confidence: dict[str, int]
    search: dict[str, int]
    schema_version: int

    def w(self, key: str) -> int:
        if key not in self.weights:
            raise ScoringError(f"в scoring.yaml нет веса {key}")
        return int(self.weights[key])

    def c(self, key: str) -> int:
        if key not in self.confidence:
            raise ScoringError(f"в scoring.yaml нет порога {key}")
        return int(self.confidence[key])

    def s(self, key: str) -> int:
        if key not in self.search:
            raise ScoringError(f"в scoring.yaml нет search.{key}")
        return int(self.search[key])


def load_scoring(path: Path) -> ScoringConfig:
    if not path.is_file():
        raise ScoringError(f"нет config/scoring.yaml: {path}")
    data = load_simple_yaml(path)
    weights = data.get("weights")
    confidence = data.get("confidence")
    search = data.get("search")
    if not isinstance(weights, dict) or not isinstance(confidence, dict) or not isinstance(search, dict):
        raise ScoringError("scoring.yaml: нужны секции weights, confidence, search")
    missing_w = [k for k in WEIGHT_KEYS if k not in weights]
    missing_c = [k for k in CONFIDENCE_KEYS if k not in confidence]
    missing_s = [k for k in SEARCH_KEYS if k not in search]
    if missing_w or missing_c or missing_s:
        raise ScoringError(
            "scoring.yaml неполный: "
            + ", ".join(missing_w + missing_c + missing_s)
        )
    schema = int(data.get("schema_version") or 1)
    return ScoringConfig(
        weights={k: int(weights[k]) for k in WEIGHT_KEYS},
        confidence={k: int(confidence[k]) for k in CONFIDENCE_KEYS},
        search={k: int(search[k]) for k in SEARCH_KEYS},
        schema_version=schema,
    )


@dataclass
class ScoreBreakdown:
    direct_reference: int = 0
    invoice_link: int = 0
    amount: int = 0
    contract: int = 0
    calculation_type: int = 0
    semantic: int = 0
    chronology: int = 0

    @property
    def selection_total(self) -> int:
        """Score для порогов HIGH/gap. Хронология сюда не входит."""
        return (
            self.direct_reference
            + self.invoice_link
            + self.amount
            + self.contract
            + self.calculation_type
            + self.semantic
        )

    @property
    def total(self) -> int:
        return self.selection_total + self.chronology

    def as_csv(self) -> dict[str, str]:
        return {
            "direct_reference_score": str(self.direct_reference),
            "invoice_link_score": str(self.invoice_link),
            "amount_score": str(self.amount),
            "contract_score": str(self.contract),
            "calculation_type_score": str(self.calculation_type),
            "semantic_score": str(self.semantic),
            "chronology_score": str(self.chronology),
            "score": str(self.total),
        }

    def amount_only(self) -> bool:
        """Почти только хронология/пусто — не HIGH. Здесь: нет ссылок, только слабые признаки."""
        return self.direct_reference == 0 and self.invoice_link == 0 and self.amount == 0


def apply_chronology(breakdowns: list[ScoreBreakdown], distances: list[int], max_score: int) -> None:
    """Записать chronology_score как объяснение tie-break. Не создаёт HIGH.

    Вызывать только внутри уже равной группы, прошедшей пороги selection_total/gap.
    """
    if max_score <= 0 or len(breakdowns) < 2 or len(breakdowns) != len(distances):
        return
    max_d = max(distances)
    if max_d <= 0:
        for bd in breakdowns:
            bd.chronology = max_score
        return
    for bd, dist in zip(breakdowns, distances):
        bd.chronology = int(round(max_score * (1.0 - dist / max_d)))


def classify_confidence(best: int, second: int | None, cfg: ScoringConfig, breakdown: ScoreBreakdown) -> str:
    """HIGH/MEDIUM/LOW по порогам YAML. best/second — без chronology."""
    gap = best - (0 if second is None else second)
    if breakdown.amount_only() and breakdown.chronology > 0 and breakdown.total == breakdown.chronology:
        # почти только хронология не может быть HIGH
        if best >= cfg.c("medium_min_score") and gap >= cfg.c("medium_min_gap"):
            return "MEDIUM"
        if best >= cfg.c("low_min_score") and gap >= cfg.c("low_min_gap"):
            return "LOW"
        return ""
    if best >= cfg.c("high_min_score") and gap >= cfg.c("high_min_gap"):
        if breakdown.amount_only() and breakdown.chronology == breakdown.total and breakdown.total > 0:
            return "MEDIUM"
        return "HIGH"
    if best >= cfg.c("medium_min_score") and gap >= cfg.c("medium_min_gap"):
        return "MEDIUM"
    if best >= cfg.c("low_min_score") and gap >= cfg.c("low_min_gap"):
        return "LOW"
    return ""


def pick_high(
    items: list[tuple[object, ScoreBreakdown]],
    distances: list[int],
    cfg: ScoringConfig,
) -> tuple[object, ScoreBreakdown] | None:
    """HIGH по selection_total/gap. Хронология — только выбор среди уже равных.

    Несколько равных без отрыва от остальных → None (не HIGH).
    """
    if not items or len(items) != len(distances):
        return None
    ranked = sorted(range(len(items)), key=lambda i: -items[i][1].selection_total)
    best_score = items[ranked[0]][1].selection_total
    tied = [i for i in ranked if items[i][1].selection_total == best_score]
    rest = [i for i in ranked if items[i][1].selection_total < best_score]
    if len(tied) == 1:
        second = items[rest[0]][1].selection_total if rest else None
    else:
        second = items[rest[0]][1].selection_total if rest else best_score
    if classify_confidence(best_score, second, cfg, items[tied[0]][1]) != "HIGH":
        return None
    if len(tied) > 1:
        apply_chronology(
            [items[i][1] for i in tied],
            [distances[i] for i in tied],
            cfg.w("chronology_max"),
        )
        chosen = min(tied, key=lambda i: distances[i])
    else:
        chosen = tied[0]
    return items[chosen]
