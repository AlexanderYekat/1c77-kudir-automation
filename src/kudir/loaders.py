"""Загрузка каталога exchange/<run_id>/ по контракту csv_io."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from kudir_proto.csv_io import (
    DOCUMENTS_FIELDS,
    LEDGER_FIELDS,
    MANIFEST_KEYS,
    OPENING_FIELDS,
    PAYMENTS_FIELDS,
    SCHEMA_VERSION,
    read_kv,
    read_rows,
)


class LoadError(Exception):
    pass


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FLAG = frozenset({"0", "1"})


@dataclass
class ExchangeBundle:
    directory: Path
    manifest: dict[str, str]
    payments: list[dict[str, str]]
    ledger: list[dict[str, str]]
    documents: list[dict[str, str]]
    openings: list[dict[str, str]]


def load_exchange(directory: Path) -> ExchangeBundle:
    directory = directory.resolve()
    manifest_path = directory / "manifest.csv"
    if not manifest_path.is_file():
        raise LoadError("нет manifest.csv")
    opening_path = directory / "opening_balances.csv"
    if not opening_path.is_file():
        raise LoadError("нет opening_balances.csv")
    manifest = read_kv(manifest_path)
    schema = (manifest.get("schema_version") or "").strip()
    if schema != SCHEMA_VERSION:
        raise LoadError(f"неизвестная schema_version={schema!r}, ожидается {SCHEMA_VERSION}")
    run_id = (manifest.get("run_id") or "").strip()
    if not run_id:
        raise LoadError("пустой run_id в manifest.csv")
    missing = [k for k in MANIFEST_KEYS if not (manifest.get(k) or "").strip()]
    if missing:
        raise LoadError("в manifest.csv нет обязательных ключей: " + ", ".join(missing))
    bundle = ExchangeBundle(
        directory=directory,
        manifest=manifest,
        payments=read_rows(directory / "payments.csv", PAYMENTS_FIELDS),
        ledger=read_rows(directory / "ledger62.csv", LEDGER_FIELDS),
        documents=read_rows(directory / "documents.csv", DOCUMENTS_FIELDS),
        openings=read_rows(opening_path, OPENING_FIELDS),
    )
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: ExchangeBundle) -> None:
    """Входные инварианты до matcher. Нарушение — FAILED, не продолжать."""
    _validate_manifest_dates(bundle.manifest)
    horizon = (bundle.manifest.get("match_horizon_end") or "").strip()
    pay_ids = _unique_ids(bundle.payments, "payment_id", "payments.csv")
    _unique_ids(bundle.ledger, "ledger_event_id", "ledger62.csv")
    _unique_ids(bundle.documents, "document_id", "documents.csv")
    _unique_openings(bundle.openings)
    for i, row in enumerate(bundle.payments, start=2):
        where = f"payments.csv:{i}"
        _flag(row, "is_target", where)
        _kop(row, "СуммаКоп", where)
        _iso_required(row, "ДатаОперации", where)
        _iso_optional(row, "ДатаДокументаОплаты", where)
        _iso_optional(row, "ДатаДоговора", where)
    for i, row in enumerate(bundle.ledger, start=2):
        where = f"ledger62.csv:{i}"
        _kop(row, "СуммаКоп", where)
        _iso_required(row, "ДатаОперации", where)
        _iso_optional(row, "ДатаДокумента", where)
        _iso_optional(row, "ДатаДоговора", where)
    for i, row in enumerate(bundle.documents, start=2):
        where = f"documents.csv:{i}"
        _flag(row, "Проведен", where)
        _flag(row, "ПомеченНаУдаление", where)
        _kop(row, "СуммаКоп", where)
        _kop(row, "СуммаОблагаемаяКоп", where)
        _kop(row, "СуммаНеоблагаемаяКоп", where)
        _kop(row, "СуммаНДСКоп", where)
        doc_date = _iso_required(row, "ДатаДокумента", where)
        _iso_optional(row, "ДатаДоговора", where)
        if horizon and doc_date > horizon:
            raise LoadError(
                f"{where}: ДатаДокумента {doc_date} позже match_horizon_end {horizon}"
            )
    for i, row in enumerate(bundle.openings, start=2):
        where = f"opening_balances.csv:{i}"
        _kop(row, "ОстатокДтКоп", where)
        _kop(row, "ОстатокКтКоп", where)
        _iso_required(row, "ДатаСальдо", where)
    _check_payment_ledger_sums(bundle, pay_ids)


def _validate_manifest_dates(manifest: dict[str, str]) -> None:
    for key in ("history_start", "date_start", "date_end", "quarter_end", "match_horizon_end"):
        _parse_iso((manifest.get(key) or "").strip(), f"manifest.csv:{key}")


def _unique_ids(rows: list[dict[str, str]], field: str, fname: str) -> set[str]:
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        value = (row.get(field) or "").strip()
        if not value:
            raise LoadError(f"{fname}:{i}: пустой {field}")
        if value in seen:
            raise LoadError(f"{fname}: дубликат {field}={value}")
        seen.add(value)
    return seen


def _unique_openings(rows: list[dict[str, str]]) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for i, row in enumerate(rows, start=2):
        key = (
            (row.get("КонтрагентID") or "").strip(),
            (row.get("Счет62") or "").strip(),
            (row.get("ДоговорID") or "").strip(),
            (row.get("ВидРасчетовID") or "").strip(),
        )
        if key in seen:
            raise LoadError(f"opening_balances.csv:{i}: дубликат аналитики {key}")
        seen.add(key)


def _check_payment_ledger_sums(bundle: ExchangeBundle, pay_ids: set[str]) -> None:
    parts: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(bundle.ledger, start=2):
        pid = (row.get("payment_id") or "").strip()
        if not pid:
            continue
        if pid not in pay_ids:
            raise LoadError(f"ledger62.csv:{i}: payment_id={pid} нет в payments.csv")
        parts[pid].append(_kop(row, "СуммаКоп", f"ledger62.csv:{i}"))
    pay_amounts: dict[str, int] = {}
    for i, row in enumerate(bundle.payments, start=2):
        pid = (row.get("payment_id") or "").strip()
        amount = _kop(row, "СуммаКоп", f"payments.csv:{i}")
        pay_amounts[pid] = amount
        if (row.get("is_target") or "").strip() == "1" and pid not in parts:
            raise LoadError(f"payments.csv:{i}: целевая оплата {pid} без строк ledger62")
        got = sum(parts.get(pid, []))
        if pid in parts and got != amount:
            raise LoadError(
                f"payments.csv:{i}: СуммаКоп {amount} != сумма ledger62 {got} для {pid}"
            )
        if pid not in parts and amount != 0:
            raise LoadError(f"payments.csv:{i}: оплата {pid} без строк ledger62")


def _flag(row: dict[str, str], field: str, where: str) -> str:
    value = (row.get(field) or "").strip()
    if value not in _FLAG:
        raise LoadError(f"{where}: поле {field} должно быть 0 или 1, получено {value!r}")
    return value


def _kop(row: dict[str, str], field: str, where: str) -> int:
    raw = (row.get(field) or "").strip()
    if raw == "" or not raw.isdigit():
        raise LoadError(f"{where}: поле {field} должно быть целыми неотрицательными копейками, получено {raw!r}")
    return int(raw)


def _iso_required(row: dict[str, str], field: str, where: str) -> str:
    value = (row.get(field) or "").strip()
    return _parse_iso(value, f"{where}:{field}")


def _iso_optional(row: dict[str, str], field: str, where: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        return ""
    return _parse_iso(value, f"{where}:{field}")


def _parse_iso(value: str, where: str) -> str:
    if not _ISO.match(value):
        raise LoadError(f"{where}: не ISO-дата {value!r}")
    try:
        date(int(value[:4]), int(value[5:7]), int(value[8:10]))
    except ValueError as exc:
        raise LoadError(f"{where}: несуществующая дата {value!r}") from exc
    return value
