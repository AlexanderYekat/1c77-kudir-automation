"""Read-only checks for E0.2 of plan No. 13 (identifiers, CSV, unfinished run directory).

    python reports/check_e02.py static
    python reports/check_e02.py package exchange/KUDIR4_20261010_001 [--json out.json]
    python reports/check_e02.py compare exchange/KUDIR4_20261010_001 exchange/KUDIR4_20261010_002

static  - narrow text checks of 1cv77/kudir_export.txt and its ERT: declaration order, forward signatures,
          built-in names, global calls against the installed 1C 7.7 syntax helper, new mixed-script
          identifiers, ERT module parity and the form button. It is not a 1C compiler.
package - checks one directory written by the "Набор Э0.2" button: encoding, CSV records, headers,
          manifest/export_status, ID formats, K decoding, id_map collisions and references, positions,
          CSV self-test. Files prove what 1C wrote, not completeness against the database.
compare - two runs over the same window must give the same IDs, positions and references.

Exit codes: 0 - PASS; 1 - ERROR findings (FAIL); 2 - package without errors but without checked objects
(INCOMPLETE: no posting rows or no candidate documents, so ID stability and reversibility are not shown).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
MODULE = ROOT / "1cv77" / "kudir_export.txt"
ERT = ROOT / "1cv77-extforms" / "kudir_export.ert"
# E0.1 was accepted on this commit; identifiers mixing Latin and Cyrillic that are new since then are flagged.
BASELINE_COMMIT = "ae02186"
FORM_ACTION = "ВыгрузкаЭ02()"
# Project guard, not a claim about the exact platform boundary: a 590-character
# line failed in the owner's 1C 7.7 configurator; the accepted E0.2 module had at most 479.
MAX_SOURCE_LINE_LENGTH = 500

MANIFEST_KEYS = [
    "run_id", "schema_version", "identity_scheme", "encoding", "source_id", "account_scope", "currency",
    "history_start", "match_horizon_end", "source_read_mode", "export_mode", "coverage_status",
]
MANIFEST_FIXED = {
    "schema_version": "4",
    "identity_scheme": "STABLE_DOCUMENT_V1",
    "encoding": "windows-1251",
    "account_scope": "62.1+62.2:ALL_CUSTOMERS",
    "currency": "RUB",
    "history_start": "2026-01-01",
    "source_read_mode": "QUIET_WINDOW",
    "export_mode": "FULL",
    # A diagnostic set never covers the declared area.
    "coverage_status": "INCOMPLETE",
}
STATUS_KEYS = ["run_id", "schema_version", "status"]
ID_MAP_HEADER = ["entity_type", "id", "вид", "номер", "дата", "позиция", "номер_проводки", "ссылка_1С"]
DIAG_HEADER = ["severity", "file", "record_key", "field", "code", "message"]
E02_IDS_HEADER = [
    "ledger_event_id", "source_document_id", "payment_id", "PaymentKind", "ДатаОперации", "ПозицияДокумента",
    "НомерПроводкиВДокументе", "N", "Сторона62", "Счет62", "СчетДебета", "СчетКредита", "СуммаКоп",
    "ВидДокумента", "НомерДокумента", "ДатаДокумента", "КонтрагентID", "ДоговорID", "ВидРасчетовID",
    "СодержаниеПроводки", "Содержание", "КомментарийДокумента",
]
E02_DOCS_HEADER = ["document_id", "ВидДокумента", "НомерДокумента", "ДатаДокумента", "ПозицияДокумента"]
SELFTEST_HEADER = ["case", "value", "length"]
DIAG_CODES = {
    "PRIMARY_NOT_CONNECTED", "PRIMARY_PARTIAL", "PRIMARY_NOT_FOUND", "PRIMARY_AMBIGUOUS", "PRIMARY_ERROR",
    "SOURCE_FIELD_UNAVAILABLE", "PAYMENT_MEMBERSHIP_UNKNOWN", "MOVEMENT_ROLE_UNKNOWN", "DOCUMENT_LINK_UNKNOWN",
    "ANALYTICS_UNKNOWN", "ORDER_UNKNOWN", "SNAPSHOT_UNKNOWN", "SNAPSHOT_MISMATCH", "VAT_COMPOSITION_UNKNOWN",
    "COVERAGE_INCOMPLETE", "ID_COLLISION", "READ_ERROR", "CSV_INVALID",
}
PAYMENT_KINDS = {"CASH", "BANK", "MANUAL"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
POSITIVE = re.compile(r"[1-9]\d*")
KOPECKS = re.compile(r"-?(0|[1-9]\d*)")

# Same cases, same order as КУДиР4_СлучаиСамопроверки in the 1C module.
SELFTEST_CASES = [
    ("plain", "abc"),
    ("semicolon", "a;b"),
    ("quote", 'a"b'),
    ("two_quotes", '""'),
    ("crlf", "строка1\r\nстрока2"),
    ("lf", "строка1\nстрока2"),
    ("cr", "строка1\rстрока2"),
    ("trailing_spaces", "abc   "),
    ("leading_spaces", "   abc"),
    ("spaces_only", "   "),
    ("empty", ""),
    ("tab", "a\tb"),
    ("percent_pipe", "%|%7C%25|"),
    ("leading_zeros", "000123"),
    ("cyrillic", "Счёт № 15 от 01.02.2026 «Ромашка»"),
    ("cp1251_high", bytes(b for b in range(128, 256) if b != 0x98).decode("cp1251")),
    ("position_like", "#20260601 679630000    464838   "),
    ("ref_like", '{"O","0","0","0","0","0","       238    464838   "}'),
    ("mixed", '; "x"\r\n;"'),
    ("long", "0123456789АБВГДЕЁЖЗИ" * 100),
]


class Findings:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, severity: str, code: str, file: str, key: str, message: str) -> None:
        self.items.append({"severity": severity, "code": code, "file": file, "key": key, "message": message})

    def error(self, code: str, file: str, key: str, message: str) -> None:
        self.add("ERROR", code, file, key, message)

    def info(self, code: str, file: str, key: str, message: str) -> None:
        self.add("INFO", code, file, key, message)

    @property
    def errors(self) -> int:
        return sum(1 for item in self.items if item["severity"] == "ERROR")


# --- CSV ---------------------------------------------------------------------------------------------

def parse_csv(data: bytes, name: str, findings: Findings) -> list[list[str]] | None:
    """Strict reader of the v4 transport: cp1251 without BOM, ';', '"' doubling, records end with CR LF.

    Line breaks inside quotes are values and are kept as they are.
    """
    if data.startswith(b"\xef\xbb\xbf") or data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        findings.error("CSV_INVALID", name, "PACKAGE", "BOM в начале файла")
        return None
    try:
        text = data.decode("cp1251")
    except UnicodeDecodeError as exc:
        findings.error("CSV_INVALID", name, "PACKAGE", f"байт 0x{data[exc.start]:02X} на смещении {exc.start} не Windows-1251")
        return None
    records: list[list[str]] = []
    fields: list[str] = []
    field: list[str] = []
    quoted = after_quote = False
    started = False  # something was read in the current record
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        started = True
        if quoted:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field.append('"')
                    i += 2
                    continue
                quoted, after_quote = False, True
            else:
                field.append(ch)
            i += 1
            continue
        if ch == ";":
            fields.append("".join(field))
            field, after_quote = [], False
        elif ch in "\r\n":
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            else:
                findings.error("CSV_INVALID", name, f"record {len(records) + 1}", "конец записи не CR LF")
            fields.append("".join(field))
            records.append(fields)
            fields, field, after_quote, started = [], [], False, False
        elif after_quote:
            findings.error("CSV_INVALID", name, f"record {len(records) + 1}", "символ после закрывающей кавычки")
            field.append(ch)
        elif ch == '"':
            if field:
                findings.error("CSV_INVALID", name, f"record {len(records) + 1}", "кавычка внутри поля без кавычек")
                field.append(ch)
            else:
                quoted = True
        else:
            field.append(ch)
        i += 1
    if quoted:
        findings.error("CSV_INVALID", name, f"record {len(records) + 1}", "конец файла внутри кавычек")
    if started:
        findings.error("CSV_INVALID", name, f"record {len(records) + 1}", "последняя запись не завершена CR LF")
        fields.append("".join(field))
        records.append(fields)
    return records


def read_table(directory: Path, name: str, header: list[str], findings: Findings) -> list[dict] | None:
    path = directory / name
    if not path.is_file():
        findings.error("CSV_INVALID", name, "PACKAGE", "файл отсутствует")
        return None
    records = parse_csv(path.read_bytes(), name, findings)
    if records is None:
        return None
    if not records:
        findings.error("CSV_INVALID", name, "PACKAGE", "нет заголовка")
        return None
    if records[0] != header:
        findings.error("CSV_INVALID", name, "PACKAGE", f"заголовок {records[0]!r}, ожидался {header!r}")
        return None
    rows = []
    for number, record in enumerate(records[1:], 2):
        if len(record) != len(header):
            findings.error("CSV_INVALID", name, f"record {number}", f"полей {len(record)}, ожидалось {len(header)}")
            continue
        rows.append(dict(zip(header, record)))
    return rows


def read_key_value(directory: Path, name: str, keys: list[str], findings: Findings) -> dict | None:
    rows = read_table(directory, name, ["key", "value"], findings)
    if rows is None:
        return None
    found = [row["key"] for row in rows]
    if found != keys:
        findings.error("CSV_INVALID", name, "PACKAGE", f"ключи {found!r}, ожидались {keys!r}")
    return {row["key"]: row["value"] for row in rows}


# --- IDs ---------------------------------------------------------------------------------------------

def k_problem(k: str) -> str | None:
    if not k:
        return "пустой K"
    if "|" in k:
        return "сырой | в K"
    if re.search(r"%(?!25|7C)", k):
        return "% в K не является %25/%7C"
    return None


def decode_k(k: str) -> str:
    return k.replace("%7C", "|").replace("%25", "%")


def parse_id(value: str) -> dict | None:
    """DOC|K, CASH|K, BANK|K|n, MANUAL|K|n, LED|K|n|N; K must already be encoded (no raw |)."""
    parts = value.split("|")
    kind = parts[0]
    shape = {"DOC": 2, "CASH": 2, "BANK": 3, "MANUAL": 3, "LED": 4}.get(kind)
    if shape is None or len(parts) != shape or k_problem(parts[1]):
        return None
    if any(not POSITIVE.fullmatch(part) for part in parts[2:]):
        return None
    return {"kind": kind, "k": parts[1], "posting": parts[2] if shape >= 3 else "", "n": parts[3] if shape == 4 else ""}


# --- package -----------------------------------------------------------------------------------------

def check_run_meta(directory: Path, findings: Findings, summary: dict, label: str) -> tuple[str | None, str | None, list[str], bool]:
    """manifest.csv, export_status.csv and the run log of a diagnostic set of stage `label` (e.g. "Э0.2")."""
    manifest = read_key_value(directory, "manifest.csv", MANIFEST_KEYS, findings)
    status = read_key_value(directory, "export_status.csv", STATUS_KEYS, findings)
    run_id = directory.name
    if manifest is not None:
        summary["source_id"] = manifest.get("source_id")
        summary["match_horizon_end"] = manifest.get("match_horizon_end")
        if manifest.get("run_id") != run_id:
            findings.error("CSV_INVALID", "manifest.csv", "run_id", f"run_id {manifest.get('run_id')!r} не равен имени каталога {run_id!r}")
        for key, expected in MANIFEST_FIXED.items():
            if manifest.get(key) != expected:
                findings.error("CSV_INVALID", "manifest.csv", key, f"{manifest.get(key)!r}, ожидалось {expected!r}")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", manifest.get("source_id", "")):
            findings.error("CSV_INVALID", "manifest.csv", "source_id", f"недопустимый source_id {manifest.get('source_id')!r}")
        if not DATE.fullmatch(manifest.get("match_horizon_end", "")):
            findings.error("CSV_INVALID", "manifest.csv", "match_horizon_end", "не дата YYYY-MM-DD")
    state = status.get("status") if status else None
    summary["status"] = state
    if status is not None:
        if status.get("run_id") != run_id or status.get("schema_version") != "4":
            findings.error("CSV_INVALID", "export_status.csv", "PACKAGE", "run_id/schema_version не совпадают с каталогом и v4")
        if state == "COMPLETE":
            findings.error("CSV_INVALID", "export_status.csv", "status", f"COMPLETE на {label} запрещён: набор диагностический")
        elif state not in {"WRITING", "FAILED"}:
            findings.error("CSV_INVALID", "export_status.csv", "status", f"недопустимый статус {state!r}")
    log = directory / "1c_export.log"
    log_text = log.read_bytes().decode("cp1251", "replace") if log.is_file() else None
    end = re.findall(rf"END {re.escape(label)} итог=(\w+)", log_text or "")
    # A run that failed or never reached END is FAIL whatever its files hold. Files it did not create and id_map
    # rows of the record in progress are consequences of the stop, not separate defects; written files are checked.
    interrupted = state == "FAILED" or not end
    return state, log_text, end, interrupted


def check_diagnostics(directory: Path, findings: Findings, summary: dict, state: str | None) -> list[dict]:
    """diagnostics.csv by №1 §27; exactly one designed COVERAGE_INCOMPLETE of the set; ERROR requires FAILED."""
    diagnostics = read_table(directory, "diagnostics.csv", DIAG_HEADER, findings) or []
    designed = [row for row in diagnostics if row["code"] == "COVERAGE_INCOMPLETE" and row["record_key"] == "PACKAGE"]
    reported_errors = [row for row in diagnostics if row["severity"] == "ERROR" and row not in designed]
    for number, row in enumerate(diagnostics, 2):
        if row["severity"] not in {"ERROR", "WARNING"}:
            findings.error("CSV_INVALID", "diagnostics.csv", f"record {number}", f"severity {row['severity']!r}")
        if row["code"] not in DIAG_CODES:
            findings.error("CSV_INVALID", "diagnostics.csv", f"record {number}", f"код {row['code']!r} вне перечня №1 §27")
        if not row["file"] or not row["record_key"] or not row["message"]:
            findings.error("CSV_INVALID", "diagnostics.csv", f"record {number}", "пустые file/record_key/message")
    if len(designed) != 1:
        findings.error("CSV_INVALID", "diagnostics.csv", "PACKAGE", f"ожидалась одна запись COVERAGE_INCOMPLETE набора, найдено {len(designed)}")
    summary["diagnostics"] = dict(Counter(f"{row['severity']}:{row['code']}" for row in diagnostics))
    if reported_errors and state != "FAILED":
        findings.error("CSV_INVALID", "export_status.csv", "status", f"в diagnostics {len(reported_errors)} ERROR, а статус {state!r}, не FAILED")
    if state == "FAILED" and not reported_errors:
        findings.error("CSV_INVALID", "export_status.csv", "status", "FAILED без ERROR в diagnostics.csv")
    for row in reported_errors[:50]:
        findings.add("REPORTED", row["code"], row["file"], row["record_key"], f"{row['field']}: {row['message']}")
    return diagnostics


def check_id_map(id_map: list[dict], findings: Findings, summary: dict) -> dict[tuple[str, str], dict]:
    """id_map.csv by №1 §23.3: one row per (entity_type, id), ID forms, K decodes to ссылка_1С, one position per K."""
    by_key: dict[tuple[str, str], dict] = {}
    position_of_k: dict[str, str] = {}
    for row in id_map:
        key = (row["entity_type"], row["id"])
        if key in by_key:
            findings.error("ID_COLLISION", "id_map.csv", row["id"], f"повтор ключа {key}")
            continue
        by_key[key] = row
        if not row["ссылка_1С"]:
            findings.error("CSV_INVALID", "id_map.csv", row["id"], "пустая ссылка_1С")
        if row["entity_type"] == "CONTRACT":
            if not re.fullmatch(r"D\|C\|[^|]+\|[^|]+", row["id"]):
                findings.error("CSV_INVALID", "id_map.csv", row["id"], "CONTRACT id не D|C|<код>|<код договора>")
            if row["позиция"] or row["номер_проводки"]:
                findings.error("CSV_INVALID", "id_map.csv", row["id"], "у CONTRACT позиция/номер_проводки должны быть пусты")
            continue
        if row["entity_type"] not in {"DOCUMENT", "PAYMENT"}:
            findings.error("CSV_INVALID", "id_map.csv", row["id"], f"entity_type {row['entity_type']!r}")
            continue
        parsed = parse_id(row["id"])
        allowed = {"DOC"} if row["entity_type"] == "DOCUMENT" else PAYMENT_KINDS
        if parsed is None or parsed["kind"] not in allowed:
            findings.error("CSV_INVALID", "id_map.csv", row["id"], f"форма ID не подходит для {row['entity_type']}")
            continue
        if decode_k(parsed["k"]) != row["ссылка_1С"]:
            findings.error("ID_COLLISION", "id_map.csv", row["id"], "K не декодируется в ссылку_1С")
        if not row["позиция"]:
            findings.error("CSV_INVALID", "id_map.csv", row["id"], "пустая позиция")
        if row["номер_проводки"] != parsed["posting"]:
            findings.error("CSV_INVALID", "id_map.csv", row["id"], f"номер_проводки {row['номер_проводки']!r} не равен номеру из ID")
        if not DATE.fullmatch(row["дата"]):
            findings.error("CSV_INVALID", "id_map.csv", row["id"], f"дата {row['дата']!r}")
        known = position_of_k.setdefault(parsed["k"], row["позиция"])
        if known != row["позиция"]:
            findings.error("ID_COLLISION", "id_map.csv", row["id"], f"один K с разными позициями {known!r} и {row['позиция']!r}")
    summary["id_map"] = dict(Counter(kind for kind, _ in by_key))
    return by_key


def check_selftest(selftest: list[dict] | None, name: str, findings: Findings, summary: dict) -> None:
    if selftest is None:
        return
    got = [(row["case"], row["value"], row["length"]) for row in selftest]
    expected = [(case, value, str(len(value))) for case, value in SELFTEST_CASES]
    if len(got) != len(expected):
        findings.error("CSV_INVALID", name, "PACKAGE", f"случаев {len(got)}, ожидалось {len(expected)}")
    for (case, value, length), (exp_name, exp_value, exp_length) in zip(got, expected):
        if case != exp_name or value != exp_value or length != exp_length:
            findings.error("CSV_INVALID", name, exp_name, f"получено {value!r} (длина {length}), ожидалось {exp_value!r}")
    summary["selftest_cases"] = len(got)


def check_package(directory: Path) -> tuple[Findings, dict]:
    findings = Findings()
    summary: dict = {"directory": str(directory)}
    state, log_text, end, interrupted = check_run_meta(directory, findings, summary, "Э0.2")

    def table(name: str, header: list[str]) -> list[dict] | None:
        if interrupted and not (directory / name).is_file():
            findings.info("NOT_CHECKED", name, "PACKAGE", "файл не создан: запуск прерван")
            return None
        return read_table(directory, name, header, findings)

    check_diagnostics(directory, findings, summary, state)
    id_map = table("id_map.csv", ID_MAP_HEADER) or []
    by_key = check_id_map(id_map, findings, summary)

    ids = table("e02_ids.csv", E02_IDS_HEADER) or []
    seen_ledger: set[str] = set()
    sides: dict[tuple[str, str], list[int]] = defaultdict(list)
    without_id = 0
    for row in ids:
        led = row["ledger_event_id"]
        if not led:
            without_id += 1
            continue
        parsed = parse_id(led)
        if parsed is None or parsed["kind"] != "LED":
            findings.error("CSV_INVALID", "e02_ids.csv", led, "форма ledger_event_id не LED|K|n|N")
            continue
        if led in seen_ledger:
            findings.error("ID_COLLISION", "e02_ids.csv", led, "повтор ledger_event_id")
        seen_ledger.add(led)
        k = parsed["k"]
        sides[(k, parsed["posting"])].append(int(parsed["n"]))
        if row["source_document_id"] != f"DOC|{k}":
            findings.error("CSV_INVALID", "e02_ids.csv", led, "source_document_id не DOC|K строки")
        if row["НомерПроводкиВДокументе"] != parsed["posting"] or row["N"] != parsed["n"]:
            findings.error("CSV_INVALID", "e02_ids.csv", led, "номер проводки/N не совпадают с ID")
        if ("DOCUMENT", row["source_document_id"]) not in by_key:
            findings.error("CSV_INVALID", "e02_ids.csv", led, "source_document_id не разрешается в id_map")
        elif by_key[("DOCUMENT", row["source_document_id"])]["позиция"] != row["ПозицияДокумента"]:
            findings.error("ID_COLLISION", "e02_ids.csv", led, "позиция строки отличается от позиции DOCUMENT в id_map")
        payment = row["payment_id"]
        if payment:
            pay = parse_id(payment)
            if pay is None or pay["kind"] not in PAYMENT_KINDS or pay["k"] != k or row["PaymentKind"] != pay["kind"]:
                findings.error("CSV_INVALID", "e02_ids.csv", led, f"payment_id {payment!r} не согласован с K/PaymentKind")
            elif pay["kind"] != "CASH" and pay["posting"] != parsed["posting"]:
                findings.error("CSV_INVALID", "e02_ids.csv", led, "номер проводки payment_id отличается от строки")
            if ("PAYMENT", payment) not in by_key:
                findings.error("CSV_INVALID", "e02_ids.csv", led, "payment_id не разрешается в id_map")
        elif row["PaymentKind"]:
            findings.error("CSV_INVALID", "e02_ids.csv", led, "PaymentKind без payment_id")
        if row["ДоговорID"] and ("CONTRACT", row["ДоговорID"]) not in by_key:
            findings.error("CSV_INVALID", "e02_ids.csv", led, "ДоговорID не разрешается в id_map")
        if row["Сторона62"] not in {"D", "K"}:
            findings.error("CSV_INVALID", "e02_ids.csv", led, f"Сторона62 {row['Сторона62']!r}")
        if not KOPECKS.fullmatch(row["СуммаКоп"]):
            findings.error("CSV_INVALID", "e02_ids.csv", led, f"СуммаКоп {row['СуммаКоп']!r} не целые копейки")
        for field in ("ДатаОперации", "ДатаДокумента"):
            if not DATE.fullmatch(row[field]):
                findings.error("CSV_INVALID", "e02_ids.csv", led, f"{field} {row[field]!r}")
    for (k, posting), numbers in sides.items():
        if numbers != list(range(1, len(numbers) + 1)):
            findings.error("ID_COLLISION", "e02_ids.csv", f"LED|{k}|{posting}", f"N стороны не 1..m подряд: {numbers}")
    if without_id:
        findings.error("READ_ERROR", "e02_ids.csv", "PACKAGE", f"строк без ledger_event_id: {without_id}")
    summary["e02_ids"] = {
        "rows": len(ids),
        "documents": len({row["source_document_id"] for row in ids if row["source_document_id"]}),
        "payment_kinds": dict(Counter(row["PaymentKind"] for row in ids if row["payment_id"])),
        "accounts": dict(Counter(row["Счет62"] for row in ids)),
        "document_kinds": dict(Counter(row["ВидДокумента"] for row in ids)),
        "multi_line_texts": sum(1 for row in ids for f in ("СодержаниеПроводки", "Содержание", "КомментарийДокумента") if "\n" in row[f] or "\r" in row[f]),
        "texts_with_edge_spaces": sum(1 for row in ids for f in ("СодержаниеПроводки", "Содержание", "КомментарийДокумента") if row[f] != row[f].strip(" ")),
    }

    docs = table("e02_docs.csv", E02_DOCS_HEADER) or []
    seen_docs: set[str] = set()
    for row in docs:
        doc = row["document_id"]
        parsed = parse_id(doc)
        if parsed is None or parsed["kind"] != "DOC":
            findings.error("CSV_INVALID", "e02_docs.csv", doc, "форма document_id не DOC|K")
            continue
        if doc in seen_docs:
            findings.error("ID_COLLISION", "e02_docs.csv", doc, "повтор document_id")
        seen_docs.add(doc)
        mapped = by_key.get(("DOCUMENT", doc))
        if mapped is None:
            findings.error("CSV_INVALID", "e02_docs.csv", doc, "document_id не разрешается в id_map")
        elif (mapped["позиция"], mapped["вид"], mapped["номер"], mapped["дата"]) != (row["ПозицияДокумента"], row["ВидДокумента"], row["НомерДокумента"], row["ДатаДокумента"]):
            findings.error("ID_COLLISION", "e02_docs.csv", doc, "реквизиты документа отличаются от DOCUMENT в id_map")
    summary["e02_docs"] = {"rows": len(docs), "kinds": dict(Counter(row["ВидДокумента"] for row in docs))}
    used = {row["source_document_id"] for row in ids} | seen_docs | {row["payment_id"] for row in ids} | {row["ДоговорID"] for row in ids}
    orphans = [key for key in by_key if key[1] not in used]
    if orphans:
        message = f"записей id_map без ссылок из файлов набора: {len(orphans)}"
        if interrupted:
            findings.info("NOT_CHECKED", "id_map.csv", orphans[0][1], message + " (запуск прерван)")
        else:
            findings.error("CSV_INVALID", "id_map.csv", orphans[0][1], message)

    positions = [row["позиция"] for row in id_map if row["entity_type"] == "DOCUMENT"]
    summary["positions"] = {
        "lengths": dict(Counter(len(p) for p in positions)),
        "with_trailing_spaces": sum(1 for p in positions if p != p.rstrip(" ")),
        "trailing_space_counts": dict(Counter(len(p) - len(p.rstrip(" ")) for p in positions)),
    }

    check_selftest(table("e02_csv_selftest.csv", SELFTEST_HEADER), "e02_csv_selftest.csv", findings, summary)

    # Without checked objects the set shows nothing about ID stability or reference reversibility.
    not_checked = []
    if not interrupted:
        if not seen_ledger:
            not_checked.append("нет строк проводок 62 с ledger_event_id: ID проводок, оплат и их документов не проверены")
        if not seen_docs:
            not_checked.append("нет документов-кандидатов: ТекущийДокумент() типизированной выборки не проверен")
        for reason in not_checked:
            findings.add("INCOMPLETE", "NOT_CHECKED", "e02_ids.csv", "PACKAGE", reason)
        for kind in ("CASH", "BANK"):
            if kind not in summary["e02_ids"]["payment_kinds"]:
                findings.info("NOT_CHECKED", "e02_ids.csv", "PACKAGE", f"в окне нет оплат {kind}: их ID не проверены")
    expected_result = "FAIL" if state == "FAILED" else ("INCOMPLETE" if not_checked else "PASS")

    if log_text is not None:
        window = re.findall(r"START Э0\.2 .*?окно=(\S+)", log_text)
        summary["log_result"] = end[-1] if end else None
        summary["window"] = window[0] if window else None
        if not end:
            findings.info("RUN_NOT_FINISHED", "1c_export.log", "PACKAGE", "нет строки END: запуск прерван до завершения")
        elif end[-1] != expected_result:
            findings.error("CSV_INVALID", "1c_export.log", "PACKAGE", f"итог журнала {end[-1]}, по статусу {state!r} и составу ожидался {expected_result}")
    else:
        findings.error("CSV_INVALID", "1c_export.log", "PACKAGE", "журнал запуска отсутствует")
    summary["verdict"] = "FAIL" if findings.errors or interrupted else ("INCOMPLETE" if not_checked else "PASS")
    summary["files_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()}
    return findings, summary


def compare_packages(first: Path, second: Path) -> tuple[Findings, dict]:
    findings = Findings()
    tables = {}
    for directory in (first, second):
        local = Findings()
        manifest = read_key_value(directory, "manifest.csv", MANIFEST_KEYS, local) or {}
        tables[directory] = {
            "source_id": manifest.get("source_id", ""),
            "ids": read_table(directory, "e02_ids.csv", E02_IDS_HEADER, local) or [],
            "docs": read_table(directory, "e02_docs.csv", E02_DOCS_HEADER, local) or [],
            "map": read_table(directory, "id_map.csv", ID_MAP_HEADER, local) or [],
            "window": re.findall(r"START Э0\.2 .*?окно=(\S+)", (directory / "1c_export.log").read_bytes().decode("cp1251", "replace")) if (directory / "1c_export.log").is_file() else [],
        }
        for item in local.items:
            findings.add(item["severity"], item["code"], f"{directory.name}/{item['file']}", item["key"], item["message"])
    a, b = tables[first], tables[second]
    # K and DOC|K are stable only within one source area (№1 §7): sets of different bases are not comparable.
    if not a["source_id"] or a["source_id"] != b["source_id"]:
        findings.error("COMPARE", "manifest.csv", "source_id", f"разные или пустые source_id: {a['source_id']!r} и {b['source_id']!r}")
    if a["window"][:1] != b["window"][:1]:
        findings.error("COMPARE", "1c_export.log", "PACKAGE", f"разные окна чтения {a['window'][:1]} и {b['window'][:1]}")
    summary = {}
    for name, key_fields, fields in (
        ("e02_ids.csv", ("ledger_event_id",), E02_IDS_HEADER),
        ("e02_docs.csv", ("document_id",), E02_DOCS_HEADER),
        ("id_map.csv", ("entity_type", "id"), ID_MAP_HEADER),
    ):
        part = {"e02_ids.csv": "ids", "e02_docs.csv": "docs", "id_map.csv": "map"}[name]
        left = {tuple(row[f] for f in key_fields): row for row in a[part]}
        right = {tuple(row[f] for f in key_fields): row for row in b[part]}
        only_left, only_right = sorted(set(left) - set(right)), sorted(set(right) - set(left))
        if only_left or only_right:
            findings.error("COMPARE", name, "PACKAGE", f"ключи только в первом: {len(only_left)}, только во втором: {len(only_right)}; пример {(only_left or only_right)[0]}")
        changed = Counter()
        for key in set(left) & set(right):
            for field in fields:
                if left[key][field] != right[key][field]:
                    changed[field] += 1
        if changed:
            findings.error("COMPARE", name, "PACKAGE", f"различаются поля общих ключей: {dict(changed)}")
        summary[name] = {"common": len(set(left) & set(right)), "only_first": len(only_left), "only_second": len(only_right), "changed_fields": dict(changed)}
    return findings, summary


# --- static ------------------------------------------------------------------------------------------

def git_show(path: str) -> str | None:
    completed = subprocess.run(["git", "show", f"{BASELINE_COMMIT}:{path}"], cwd=ROOT, capture_output=True)
    return completed.stdout.decode("utf-8-sig") if completed.returncode == 0 else None


def mixed_identifiers(text: str, code_only) -> set[str]:
    return {
        word for word in re.findall(r"[A-Za-zА-Яа-яЁё_][\wЁё]*", code_only(text))
        if re.search(r"[A-Za-z]", word) and re.search(r"[А-Яа-яЁё]", word)
    }


def silent_handlers(text: str, code_only) -> list[int]:
    # Every "Исключение" block must report the error (ОписаниеОшибки) before "КонецПопытки".
    lines = code_only(text).splitlines()
    result = []
    for number, line in enumerate(lines, 1):
        if re.match(r"\s*Исключение\b", line, re.I):
            body = []
            for following in lines[number:]:
                if re.match(r"\s*КонецПопытки\b", following, re.I):
                    break
                body.append(following)
            if "ОписаниеОшибки()" not in "\n".join(body):
                result.append(number)
    return result


def argument_count_mismatches(text: str, token) -> list[tuple[int, str, int, int]]:
    # Calls of the module's own routines with a different number of arguments than the definition.
    # String literals are kept as "" so commas inside them do not count; calls split across lines are skipped.
    code = token.sub(lambda m: "" if m[0].startswith("//") else '""', text)
    params = {
        m[1]: 0 if not m[2].strip() else len(m[2].split(","))
        for m in re.finditer(r"(?m)^\s*(?:Функция|Процедура)\s+(\w+)\s*\((.*?)\)", code)
    }
    result = []
    for number, line in enumerate(code.splitlines(), 1):
        if re.match(r"\s*(Функция|Процедура)\s", line, re.I):
            continue
        for match in re.finditer(r"(?<![.\w])(\w+)\s*\(", line):
            if match[1] not in params:
                continue
            index, depth, commas = match.end(), 1, 0
            while index < len(line) and depth:
                depth += {"(": 1, ")": -1}.get(line[index], 0)
                commas += line[index] == "," and depth == 1
                index += 1
            if depth:
                continue
            count = commas + 1 if line[match.end():index - 1].strip() else 0
            if count != params[match[1]]:
                result.append((number, match[1], count, params[match[1]]))
    return result


def method_parameter_limits(helper_path: Path) -> dict[str, int] | None:
    # Largest parameter count of every Russian method name (lower case) over all its signatures in the 1C 7.7 syntax
    # helper. All item kinds count, including "A" - accounting objects such as Операция: ПолучитьПроводку() returns
    # the next posting of ВыбратьПроводки(), and a number passed to it fails only at run time ("Слишком много
    # параметров передано при вызове функции/процедуры объекта", run KUDIR4_20261011_001).
    if not helper_path.is_file():
        return None
    text = helper_path.read_bytes().decode("cp1251", "replace")
    limits: dict[str, int] = {}
    item = r'\{"Item","[^"]*","([^"]*)","[^"]*",\s*"([^"]*)",\s*"[^"]*"(?:,\s*"@Синтаксис:\s*([^@"]*))?'
    for name, template, syntax in re.findall(item, text):
        name = name.strip().lower()
        for signature in (template, syntax):
            inner = re.search(r"\((.*)\)", signature)
            if inner and re.fullmatch(r"\w+", name):
                limits[name] = max(limits.get(name, 0), len(inner[1].split(",")) if inner[1].strip() else 0)
    return limits


def method_calls_over_limit(text: str, token, limits: dict[str, int]) -> list[tuple[int, str, int, int]]:
    # Object method calls ".Имя(...)" with more arguments than any signature of Имя in the syntax helper.
    # String literals are kept as "" so commas inside them do not count; calls split across lines are skipped.
    code = token.sub(lambda m: "" if m[0].startswith("//") else '""', text)
    result = []
    for number, line in enumerate(code.splitlines(), 1):
        for match in re.finditer(r"\.(\w+)\s*\(", line):
            limit = limits.get(match[1].lower())
            if limit is None:
                continue
            index, depth, commas = match.end(), 1, 0
            while index < len(line) and depth:
                depth += {"(": 1, ")": -1}.get(line[index], 0)
                commas += line[index] == "," and depth == 1
                index += 1
            if depth:
                continue
            count = commas + 1 if line[match.end():index - 1].strip() else 0
            if count > limit:
                result.append((number, match[1], count, limit))
    return result


def table_column_mismatches(text: str, token) -> list[tuple[int, str, str]]:
    # A ТаблицаЗначений column read or assigned but never created by НоваяКолонка is a runtime error in 1C 7.7
    # ("Поле агрегатного объекта не обнаружено"), not a compile error. Columns are collected per variable name
    # across the module (a table is often filled in one routine and read in another under the same name).
    with_literals = token.sub(lambda m: "" if m[0].startswith("//") else m[0], text)
    tables = set(re.findall(r'(?<![.\w])(\w+)\s*=\s*СоздатьОбъект\("ТаблицаЗначений"\)', with_literals))
    columns: dict[str, set[str]] = defaultdict(set)
    for name, column in re.findall(r'(?<![.\w])(\w+)\.НоваяКолонка\("(\w+)"', with_literals):
        columns[name].add(column)
    properties = {"НомерСтроки"}
    result = []
    blank = token.sub(lambda m: "" if m[0].startswith("//") else '""', text)
    for number, line in enumerate(blank.splitlines(), 1):
        for name, attribute in re.findall(r"(?<![.\w])(\w+)\.(\w+)\b(?!\s*\()", line):
            if name in tables and attribute not in columns[name] | properties:
                result.append((number, name, attribute))
    return result


def source_line_length_issues(text: str) -> list[tuple[int, int]]:
    # Physical lines, including comments and literals; CR/LF are not part of the length.
    return [(number, len(line)) for number, line in enumerate(text.splitlines(), 1)
            if len(line) > MAX_SOURCE_LINE_LENGTH]


def check_static(form_action: str = FORM_ACTION) -> tuple[Findings, dict]:
    sys.path.insert(0, str(HERE))
    import check_e01 as base  # generic narrow checks written for E0.1

    findings = Findings()
    text = base.read(MODULE)
    definitions, duplicates, forward = base.routines(text)
    helper = base.syntax_helper_names()
    summary = {"module": str(MODULE.relative_to(ROOT)), "version": text.splitlines()[0], "routines": len(definitions)}
    summary["max_source_line_length"] = max(map(len, text.splitlines()), default=0)
    for number, length in source_line_length_issues(text):
        findings.error("STATIC", MODULE.name, f"line {number}",
                       f"длина физической строки {length}, защитный предел проекта {MAX_SOURCE_LINE_LENGTH}: разбейте выражение на несколько строк")
    summary["v4_routines"] = sorted(name for name in definitions if name.startswith("КУДиР4_"))
    for name in duplicates:
        findings.error("STATIC", MODULE.name, name, "процедура определена дважды")
    for name in sorted(forward - definitions.keys()):
        findings.error("STATIC", MODULE.name, name, "объявление Далее без тела")
    for issue in base.calls_before_definition(text, definitions, forward):
        findings.error("STATIC", MODULE.name, issue["name"], f"вызов в строке {issue['line']} до определения в строке {issue['defined']} без Далее")
    for name in base.forward_mismatches(text):
        findings.error("STATIC", MODULE.name, name, "сигнатура Далее отличается от определения")
    lexical = base.lexical_checks(text)
    if any(lexical.values()):
        findings.error("STATIC", MODULE.name, "PACKAGE", f"блоки/скобки: {lexical}")
    for issue in base.builtin_name_clashes(text):
        findings.error("STATIC", MODULE.name, issue["name"], f"имя встроенной функции 1С как переменная, строка {issue['line']}")
    if helper is None:
        findings.error("STATIC", MODULE.name, "PACKAGE", f"синтакс-помощник не найден: {base.SYNTAX_HELPER}")
    else:
        for name, numbers in base.unknown_global_calls(text, helper).items():
            findings.error("STATIC", MODULE.name, name, f"глобальный вызов не известен синтакс-помощнику 7.7, строки {numbers[:5]}")
    for number, name, count, expected in argument_count_mismatches(text, base.TOKEN):
        findings.error("STATIC", MODULE.name, name, f"строка {number}: аргументов {count}, в определении {expected}")
    for number, name, count, limit in method_calls_over_limit(text, base.TOKEN, method_parameter_limits(base.SYNTAX_HELPER) or {}):
        findings.error("STATIC", MODULE.name, name, f"строка {number}: у метода {name} аргументов {count}, в синтакс-помощнике не больше {limit}")
    for number, name, attribute in table_column_mismatches(text, base.TOKEN):
        findings.error("STATIC", MODULE.name, f"{name}.{attribute}", f"строка {number}: колонка не создана НоваяКолонка")
    for number in silent_handlers(text, base.code_only):
        findings.error("STATIC", MODULE.name, f"line {number}", "Исключение без ОписаниеОшибки(): ошибка проглатывается")
    baseline = git_show("1cv77/kudir_export.txt")
    if baseline is None:
        findings.error("STATIC", MODULE.name, "PACKAGE", f"нет базовой версии {BASELINE_COMMIT} для проверки смешанных имён")
    else:
        for word in sorted(mixed_identifiers(text, base.code_only) - mixed_identifiers(baseline, base.code_only)):
            findings.error("STATIC", MODULE.name, word, "новый идентификатор смешивает латиницу и кириллицу")
    try:
        text.encode("cp1251")
    except UnicodeEncodeError as exc:
        findings.error("STATIC", MODULE.name, f"offset {exc.start}", f"символ {text[exc.start]!r} вне Windows-1251")

    ert = base.ert_checks().get("kudir_export")
    if not isinstance(ert, dict):
        findings.error("STATIC", ERT.name, "PACKAGE", "ERT не прочитан (нет ert_module.py из 1cv77-devtools)")
    else:
        summary["ert_sha256"] = ert["sha256"]
        summary["ert_version"] = ert["version"]
        if not ert["module_matches_text"]:
            findings.error("STATIC", ERT.name, "PACKAGE", f"модуль ERT отличается от TXT, строки {ert['different_lines'][:10]}")
        actions = [c["formula"] for c in ert["dialog_controls"] if c["class"] == "BUTTON"]
        summary["ert_buttons"] = [(c["caption"], c["formula"]) for c in ert["dialog_controls"] if c["class"] == "BUTTON"]
        if form_action not in actions:
            findings.error("STATIC", ERT.name, "PACKAGE", f"в диалоге нет кнопки с формулой {form_action}")
        if "Сформировать()" in actions:
            findings.error("STATIC", ERT.name, "PACKAGE", "в диалоге осталась кнопка отключённой выгрузки v2")
        if ert["undeclared_form_identifiers"]:
            findings.error("STATIC", ERT.name, "PACKAGE", f"реквизиты выб* не объявлены в диалоге/модуле: {ert['undeclared_form_identifiers']}")
    return findings, summary


def report(findings: Findings, summary: dict, json_path: str | None) -> int:
    payload = {"summary": summary, "errors": findings.errors, "findings": findings.items}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if json_path:
        Path(json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if findings.errors or summary.get("verdict") == "FAIL":
        return 1
    return 2 if summary.get("verdict") == "INCOMPLETE" else 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("static")
    one = sub.add_parser("package")
    one.add_argument("directory", type=Path)
    one.add_argument("--json")
    two = sub.add_parser("compare")
    two.add_argument("first", type=Path)
    two.add_argument("second", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "static":
        return report(*check_static(), None)
    if args.mode == "package":
        return report(*check_package(args.directory), args.json)
    return report(*compare_packages(args.first, args.second), None)


if __name__ == "__main__":
    raise SystemExit(main())
