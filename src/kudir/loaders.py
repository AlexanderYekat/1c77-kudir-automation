"""Загрузка каталога exchange/<run_id>/ по контракту csv_io."""

from __future__ import annotations

from dataclasses import dataclass
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
    return ExchangeBundle(
        directory=directory,
        manifest=manifest,
        payments=read_rows(directory / "payments.csv", PAYMENTS_FIELDS),
        ledger=read_rows(directory / "ledger62.csv", LEDGER_FIELDS),
        documents=read_rows(directory / "documents.csv", DOCUMENTS_FIELDS),
        openings=read_rows(opening_path, OPENING_FIELDS),
    )
