"""CSV-протокол прототипа: Windows-1251, разделитель ';', даты ISO."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

ENCODING = "cp1251"
DELIMITER = ";"
SCHEMA_VERSION = "1"

MANIFEST_KEYS = (
    "run_id",
    "schema_version",
    "encoding",
    "history_start",
    "date_start",
    "date_end",
)

PAYMENTS_FIELDS = [
    "payment_id",
    "is_target",
    "PaymentKind",
    "PaymentGranularity",
    "ДатаОперации",
    "ПозицияДокумента",
    "НомерСтрокиВДокументе",
    "ВидДокументаОплаты",
    "НомерДокументаОплаты",
    "ДатаДокументаОплаты",
    "СуммаКоп",
    "СчетДебета",
    "СчетКредита",
    "КонтрагентID",
    "Контрагент",
    "ДоговорID",
    "Договор",
    "НомерДоговора",
    "ДатаДоговора",
    "ВидРасчетовID",
    "ВидРасчетовСПокупателем",
    "СодержаниеПроводки",
    "Содержание",
    "ПредставлениеПроводки",
    "НазначениеПлатежа",
    "ПервичныйДокумент",
    "КомментарийДокументаОплаты",
]

LEDGER_FIELDS = [
    "ledger_event_id",
    "source_document_id",
    "payment_id",
    "ДатаОперации",
    "ПозицияДокумента",
    "НомерСтрокиВДокументе",
    "НомерПроводкиВДокументе",
    "ВидДокумента",
    "НомерДокумента",
    "ДатаДокумента",
    "СуммаКоп",
    "СчетДебета",
    "СчетКредита",
    "Счет62",
    "Сторона62",
    "КонтрагентID",
    "Контрагент",
    "ДоговорID",
    "Договор",
    "НомерДоговора",
    "ДатаДоговора",
    "ВидРасчетовID",
    "ВидРасчетовСПокупателем",
    "СодержаниеПроводки",
    "Содержание",
    "ПредставлениеПроводки",
    "КомментарийДокумента",
    "ПервичныйДокумент",
]

DOCUMENTS_FIELDS = [
    "document_id",
    "DocumentRole",
    "DocumentType",
    "ВидДокумента",
    "НомерДокумента",
    "ДатаДокумента",
    "ПозицияДокумента",
    "КонтрагентID",
    "Контрагент",
    "ДоговорID",
    "Договор",
    "НомерДоговора",
    "ДатаДоговора",
    "ВидРасчетовID",
    "ВидРасчетовСПокупателем",
    "СуммаКоп",
    "Проведен",
    "ПомеченНаУдаление",
    "Комментарий",
    "Содержание",
]

OPENING_FIELDS = [
    "КонтрагентID",
    "Счет62",
    "ДоговорID",
    "ВидРасчетовID",
    "ВидРасчетов",
    "ДатаСальдо",
    "ОстатокДтКоп",
    "ОстатокКтКоп",
]

KUDIR_RESULT_FIELDS = [
    "run_id",
    "result_row_id",
    "payment_id",
    "row_type",
    "matched_document_id",
    "contract_id",
    "amount_kopecks",
    "advance_kopecks",
    "repayment_kopecks",
    "income_kopecks",
    "vat_base_kopecks",
    "vat_kopecks",
    "match_type",
    "confidence",
    "reason",
    "СодержаниеЗаписи",
]

MATCHES_FIELDS = [
    "payment_id",
    "document_id",
    "matched_amount_kopecks",
    "match_type",
    "score",
    "confidence",
    "reason",
]

UNRESOLVED_FIELDS = [
    "payment_id",
    "amount_kopecks",
    "reason",
]


def read_rows(path: Path, expected_fields: list[str] | None = None) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding=ENCODING, newline="") as fh:
        reader = csv.DictReader(fh, delimiter=DELIMITER)
        if reader.fieldnames is None:
            raise ValueError(f"Нет заголовка: {path.name}")
        got = list(reader.fieldnames)
        if expected_fields is not None and got != expected_fields:
            raise ValueError(
                f"{path.name}: колонки не совпали с контрактом.\n"
                f"ожидалось: {expected_fields}\nполучено: {got}"
            )
        rows = []
        for raw in reader:
            rows.append({k: ("" if v is None else str(v)) for k, v in raw.items()})
        return rows


def write_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=ENCODING, newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            delimiter=DELIMITER,
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def read_kv(path: Path) -> dict[str, str]:
    rows = read_rows(path, expected_fields=["key", "value"])
    return {r["key"]: r["value"] for r in rows if r.get("key")}


def write_kv(path: Path, data: dict[str, str]) -> None:
    write_rows(path, ["key", "value"], [{"key": k, "value": v} for k, v in data.items()])


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
