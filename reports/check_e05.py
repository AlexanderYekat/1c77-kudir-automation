"""Read-only checks for E0.5 of plan No. 13 (direct balance snapshots, quarterly controls, coverage).

    python reports/check_e05.py static
    python reports/check_e05.py package exchange/KUDIR4_20261012_002 [--json out.json]
    python reports/check_e05.py compare exchange/KUDIR4_20261012_002 exchange/KUDIR4_20261012_003
    python reports/check_e05.py sheet exchange/KUDIR4_20261012_002 [--out sverka.md]

static  - the narrow text checks of check_e02 with the E0.5 form button ВыгрузкаЭ05(). It is not a 1C compiler.
package - one directory written by the "Набор Э0.5" button: the E0.4 checks of ledger62.csv and documents.csv, then
          balance_snapshots.csv and ledger_quarters.csv by №1 §§23.1, 23.4, 27-28: key shapes and uniqueness, DAY_START
          at every quarter start of the coverage (a coinciding year start is one boundary), the same key set at every
          boundary covering all customers/contracts of ledger62 and documents, BEFORE_DOCUMENT before every document
          with a money correspondence of 62 for every customer/contract of its sides, KNOWN/UNKNOWN without zeros for
          the unknown. Reconciliations with zero tolerance: customer = sum of its contracts; DAY_START + ledger62 movements
          of the quarter = next DAY_START; DAY_START + movements of earlier documents of the quarter = BEFORE_DOCUMENT
          (the document itself excluded); general opening = sum of customers; closing - opening = movements of the
          quarter; closing = next opening; ledger_row_count. The current quarter keeps its closing empty. A mismatch is
          an ERROR finding of the check; a mismatch the export declared as WARNING SNAPSHOT_MISMATCH and a customer
          with an unconfirmed contract slice (ANALYTICS_UNKNOWN) stay visible as WARNING findings and are counted as
          unconfirmed, never as successful reconciliations (№1 §27). An unknown required snapshot (SNAPSHOT_UNKNOWN,
          level ERROR) is kept for the analysis, but the set does not pass.
compare - two runs over the same area must give identical ledger62, documents, id_map, snapshots and controls.
sheet   - a reconciliation sheet for the owner (Markdown): quarterly controls against the trial balance of 62,
          DAY_START and BEFORE_DOCUMENT samples with the movements between them.

Exit codes: 0 - PASS; 1 - ERROR findings or failed run (FAIL); 2 - nothing to check (INCOMPLETE).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import check_e02 as e02  # noqa: E402  shared CSV reader, ID parsing, manifest/diagnostics/id_map checks
import check_e03 as e03  # noqa: E402  ledger62.csv checks of E0.3
import check_e04 as e04  # noqa: E402  documents.csv checks of E0.4
from check_e02 import DATE, KOPECKS, Findings, parse_id, read_key_value, read_table  # noqa: E402
from check_e03 import LEDGER_FILE, LEDGER_HEADER, rub  # noqa: E402
from check_e04 import DOCUMENTS_FILE, DOCUMENTS_HEADER  # noqa: E402

LABEL = "Э0.5"
FORM_ACTION = "ВыгрузкаЭ05()"
SNAPSHOTS_FILE = "balance_snapshots.csv"
QUARTERS_FILE = "ledger_quarters.csv"
SELFTEST_FILE = "csv_selftest.csv"
HISTORY_START = "2026-01-01"
# №1 §23.1, canonical order.
SNAPSHOTS_HEADER = [
    "BoundaryKind", "BoundaryDate", "BeforeDocumentID", "ПозицияДокумента", "Slice", "КонтрагентID", "ДоговорID",
    "ContractState", "ОстатокДтКоп", "ОстатокКтКоп", "Status",
]
# Key of a snapshot row (№1 §23.1) in its order; record_key of diagnostics is this key as a JSON array.
SNAPSHOT_KEY = ("BoundaryKind", "BoundaryDate", "BeforeDocumentID", "Slice", "КонтрагентID", "ContractState", "ДоговорID")
# №1 §23.4, canonical order.
QUARTERS_HEADER = [
    "quarter_start", "quarter_end", "covered_through", "full_quarter", "opening_debit_kopecks", "opening_credit_kopecks",
    "closing_debit_kopecks", "closing_credit_kopecks", "control_status", "ledger_action", "ledger_source_run_id",
    "ledger_row_count",
]
VAT_ON_TOP_LOG = "Э0.5 НДС сверху: "
# Per-account control lines of the run log, for the trial balance of 62 (КУДиР4_ОбщиеОстатки).
CONTROL_LOG = re.compile(r"Э0\.5 контроль (\d{4}-\d{2}-\d{2}) счёт (\S+): СНД=(-?\d+) СНК=(-?\d+)(?: СКД=(-?\d+) СКК=(-?\d+) \(конец (\d{4}-\d{2}-\d{2})\))? коп")
EXAMPLES = 5


def window_of(log_text: str | None) -> tuple[str, str] | None:
    found = re.findall(rf"START {LABEL} .*?окно=(\d{{4}}-\d{{2}}-\d{{2}})\.\.(\d{{4}}-\d{{2}}-\d{{2}})", log_text or "")
    return found[0] if found else None


def record_key(*components: str) -> str:
    """record_key of the 1C module (КУДиР4_КлючЗаписиСнимка): a JSON array, only \\ and " escaped, no spaces."""
    return json.dumps(list(components), ensure_ascii=False, separators=(",", ":"))


def quarter_start(date: str) -> str:
    month = (int(date[5:7]) - 1) // 3 * 3 + 1
    return f"{date[:4]}-{month:02d}-01"


def quarter_end(start: str) -> str:
    year, month = int(start[:4]), int(start[5:7]) + 2
    return f"{year}-{month:02d}-{31 if month in (3, 12) else 30}"


def next_quarter(start: str) -> str:
    year, month = int(start[:4]), int(start[5:7]) + 3
    return f"{year + 1}-01-01" if month > 12 else f"{year}-{month:02d}-01"


def quarter_starts(horizon: str) -> list[str]:
    """Quarter starts of the coverage: from history_start to the quarter of match_horizon_end (№1 §§4, 23.1)."""
    result, start = [], HISTORY_START
    while start <= horizon:
        result.append(start)
        start = next_quarter(start)
    return result


def delta(row: dict) -> int | None:
    """Change of the balance by one ledger62 row (№1 §12): D -> +СуммаКоп, K -> -СуммаКоп, whatever the role."""
    amount = e03.amount_of(row)
    if amount is None or row["Сторона62"] not in {"D", "K"}:
        return None
    return amount if row["Сторона62"] == "D" else -amount


def signed(row: dict) -> int | None:
    """S = Дт - Кт of a KNOWN snapshot row with integer kopecks, else None (unknown is not zero)."""
    if row["Status"] != "KNOWN" or not (KOPECKS.fullmatch(row["ОстатокДтКоп"]) and KOPECKS.fullmatch(row["ОстатокКтКоп"])):
        return None
    return int(row["ОстатокДтКоп"]) - int(row["ОстатокКтКоп"])


def slice_key(row: dict) -> tuple[str, str, str, str]:
    return row["Slice"], row["КонтрагентID"], row["ContractState"], row["ДоговорID"]


def ledger_keys(row: dict) -> list[tuple[str, str, str, str]]:
    """Snapshot keys a ledger62 row changes: its customer and its customer-contract pair (empty contract - EMPTY)."""
    customer = row["КонтрагентID"]
    if not customer:
        return []
    contract = row["ДоговорID"]
    return [("CUSTOMER", customer, "ALL", ""), ("CUSTOMER_CONTRACT", customer, "VALUE" if contract else "EMPTY", contract)]


def documents_in_order(ledger: list[dict]) -> dict[str, list[dict]]:
    by_doc: dict[str, list[dict]] = {}
    for row in ledger:
        if row["source_document_id"]:
            by_doc.setdefault(row["source_document_id"], []).append(row)
    return by_doc


def is_money_document(rows: list[dict]) -> bool:
    """A document with a money correspondence of 62 (№1 §23.1: receipt, refund, unresolved money group)."""
    return any(e03.is_money(e03.other_account(r)) for r in rows)


# --- balance_snapshots.csv -----------------------------------------------------------------------------

def check_snapshots(rows: list[dict], ledger: list[dict], documents: list[dict], by_key: dict, diag: list[dict],
                    horizon: str | None, findings: Findings, summary: dict) -> dict:
    """Row shapes, required keys and coverage; returns the snapshots grouped for the reconciliations.

    An UNKNOWN row is the unknown result of the required query - ERROR SNAPSHOT_UNKNOWN of the run; such a set keeps
    the rows for the analysis but does not pass the acceptance of the snapshots (№1 §27), whatever the diagnostics
    say. The only other UNKNOWN is the unconfirmed contract slice of a known query result (ANALYTICS_UNKNOWN): the
    ContractState=UNKNOWN row of an unread contract, and a pair absent from the result of that customer (export -03).
    """
    levels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for d in diag:
        if d["file"] == SNAPSHOTS_FILE:
            levels[(d["code"], d["record_key"])].add(d["severity"])
    explained = set(levels)
    unknown_required = 0
    unread_pairs: list[tuple[str, str, str, str]] = []
    seen: set[tuple] = set()
    day: dict[str, dict[tuple, dict]] = defaultdict(dict)
    before: dict[str, dict[tuple, dict]] = defaultdict(dict)
    before_meta: dict[str, tuple[str, str]] = {}
    statuses = Counter()
    for row in rows:
        kind, date, doc, position = row["BoundaryKind"], row["BoundaryDate"], row["BeforeDocumentID"], row["ПозицияДокумента"]
        part, customer, state, contract, status = row["Slice"], row["КонтрагентID"], row["ContractState"], row["ДоговорID"], row["Status"]
        key = tuple(row[f] for f in SNAPSHOT_KEY)
        label = record_key(*key)
        statuses[f"{kind} {part} {state} {status}"] += 1
        if key in seen:
            findings.error("ID_COLLISION", SNAPSHOTS_FILE, label, "повтор ключа снимка (№1 §23.1: одна строка на ключ)")
            continue
        seen.add(key)
        if not DATE.fullmatch(date):
            findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"BoundaryDate {date!r}")
            continue
        if kind == "DAY_START":
            if doc or position:
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "у DAY_START ID и позиция документа пусты")
        elif kind == "BEFORE_DOCUMENT":
            parsed = parse_id(doc)
            mapped = by_key.get(("DOCUMENT", doc))
            if parsed is None or parsed["kind"] != "DOC":
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "BeforeDocumentID не DOC|K")
            elif mapped is None:
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "BeforeDocumentID не разрешается в id_map")
            elif mapped["позиция"] != position:
                findings.error("ID_COLLISION", SNAPSHOTS_FILE, label, "позиция снимка отличается от позиции DOCUMENT в id_map")
            if before_meta.setdefault(doc, (date, position)) != (date, position):
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "у одного документа снимки с разными датой или позицией")
        else:
            findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"BoundaryKind {kind!r}")
            continue
        if not re.fullmatch(r"C\|[^|]+", customer):
            findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"КонтрагентID {customer!r} не C|<код>")
        if part == "CUSTOMER":
            if state != "ALL" or contract:
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "CUSTOMER: ContractState ALL, договор пуст")
        elif part == "CUSTOMER_CONTRACT":
            if state == "VALUE":
                if not re.fullmatch(r"D\|C\|[^|]+\|[^|]+", contract):
                    findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "VALUE: ДоговорID не D|C|<код владельца>|<код договора>")
                elif ("CONTRACT", contract) not in by_key:
                    findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "ДоговорID не разрешается в id_map")
            elif state in {"EMPTY", "UNKNOWN"}:
                if contract:
                    findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"{state}: ДоговорID должен быть пуст")
            else:
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"ContractState {state!r}")
        else:
            findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"Slice {part!r}")
            continue
        if status == "KNOWN":
            if signed(row) is None:
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"KNOWN без целых копеек: {row['ОстатокДтКоп']!r}/{row['ОстатокКтКоп']!r}")
            if state == "UNKNOWN":
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "неизвестный договор не может иметь известный остаток")
        elif status == "UNKNOWN":
            if row["ОстатокДтКоп"] or row["ОстатокКтКоп"]:
                findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "UNKNOWN с числами: неизвестный остаток не записывается нулём или суммой")
            if state == "UNKNOWN":
                if ("ANALYTICS_UNKNOWN", label) not in explained:
                    findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "договор UNKNOWN без ANALYTICS_UNKNOWN")
            elif part == "CUSTOMER_CONTRACT" and ("ANALYTICS_UNKNOWN", label) in explained:
                unread_pairs.append((kind, date, doc, customer))
            else:
                unknown_required += 1
                owner = record_key(kind, date, doc, "CUSTOMER", customer, "ALL", "")
                reasons = [levels.get(("SNAPSHOT_UNKNOWN", k), set()) for k in (label, owner, record_key(kind, date))]
                if not any(reasons):
                    findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "UNKNOWN без SNAPSHOT_UNKNOWN (№1 §27)")
                elif not any("ERROR" in r for r in reasons):
                    findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, "SNAPSHOT_UNKNOWN обязательного запроса - уровня ERROR (№1 §27), в diagnostics только WARNING")
        else:
            findings.error("CSV_INVALID", SNAPSHOTS_FILE, label, f"Status {status!r}")
        (day[date] if kind == "DAY_START" else before[doc])[slice_key(row)] = row
    # A pair is unconfirmed only next to the unread contract of its customer at the same boundary.
    for kind, date, doc, customer in unread_pairs:
        group = day[date] if kind == "DAY_START" else before[doc]
        if ("CUSTOMER_CONTRACT", customer, "UNKNOWN", "") not in group:
            findings.error("CSV_INVALID", SNAPSHOTS_FILE, record_key(kind, date, doc, "CUSTOMER_CONTRACT", customer, "UNKNOWN", ""),
                           "пара UNKNOWN по ANALYTICS_UNKNOWN без строки непрочитанного договора покупателя на той же границе")
    if unknown_required:
        findings.error("SNAPSHOT_UNKNOWN", SNAPSHOTS_FILE, "PACKAGE",
                       f"неизвестных обязательных снимков {unknown_required}: строки сохранены для разбора причины, приёмку полноты снимков набор не проходит (№1 §27)")

    required: dict[tuple[str, str], str] = {}
    for name, source in ((LEDGER_FILE, ledger), (DOCUMENTS_FILE, documents)):
        for row in source:
            if row["КонтрагентID"]:
                required.setdefault((row["КонтрагентID"], row["ДоговорID"]), name)
    # DAY_START: every quarter start of the coverage, once; the same required key set at every boundary. An empty area -
    # no snapshot at all and no customer in ledger62 or documents - has no row to write at any boundary (№1 §4); its
    # zero general openings are then reconciled by check_quarters.
    expected_days = quarter_starts(horizon) if horizon else []
    if not rows and not required:
        findings.info("EMPTY_AREA", SNAPSHOTS_FILE, "PACKAGE", "в области нет покупателей: DAY_START не пишется, нулевые остатки сверяются с ledger_quarters")
    elif sorted(day) != expected_days:
        findings.error("COVERAGE_INCOMPLETE", SNAPSHOTS_FILE, "PACKAGE", f"DAY_START на {sorted(day)}, ожидались начала кварталов охвата {expected_days}")
    # The ContractState=UNKNOWN row of an unread contract is not a required key: it exists only where it happened.
    def required_keys(keys: dict) -> set[tuple]:
        return {k for k in keys if k[2] != "UNKNOWN"}

    reference = required_keys(day.get(HISTORY_START, {}))
    for date, keys in sorted(day.items()):
        if required_keys(keys) != reference:
            extra, missing = required_keys(keys) - reference, reference - required_keys(keys)
            findings.error("COVERAGE_INCOMPLETE", SNAPSHOTS_FILE, record_key("DAY_START", date),
                           f"набор ключей отличается от {HISTORY_START}: лишних {len(extra)}, нет {len(missing)}; пример {sorted(extra or missing)[0]}")
    customers = {k[1] for k in reference if k[0] == "CUSTOMER"}
    pairs = {(k[1], k[3]) for k in reference if k[0] == "CUSTOMER_CONTRACT" and k[2] in {"VALUE", "EMPTY"}}
    for customer in sorted({c for c, _ in pairs} - customers)[:EXAMPLES]:
        findings.error("COVERAGE_INCOMPLETE", SNAPSHOTS_FILE, customer, "договорные строки DAY_START без строки CUSTOMER")
    missing = sorted(set(required) - pairs)
    for customer, contract in missing[:EXAMPLES]:
        findings.error("COVERAGE_INCOMPLETE", SNAPSHOTS_FILE, record_key("DAY_START", HISTORY_START, "", "CUSTOMER_CONTRACT", customer, "VALUE" if contract else "EMPTY", contract),
                       f"покупатель/договор из {required[(customer, contract)]} без DAY_START (№1 §23.1: аналитика ledger и documents всей области)")
    if len(missing) > EXAMPLES:
        findings.error("COVERAGE_INCOMPLETE", SNAPSHOTS_FILE, "PACKAGE", f"всего пар покупатель-договор без DAY_START: {len(missing)}")
    incoming = {(k[1], k[3]) for k in reference if k[0] == "CUSTOMER_CONTRACT"} - set(required)

    # BEFORE_DOCUMENT: before every document with a money correspondence, for every customer/contract of its sides.
    by_doc = documents_in_order(ledger)
    money_docs = [doc for doc, doc_rows in by_doc.items() if is_money_document(doc_rows)]
    for doc in money_docs:
        doc_rows = by_doc[doc]
        snaps = before.get(doc)
        title = f"{doc_rows[0]['ВидДокумента']} {doc_rows[0]['НомерДокумента']} от {doc_rows[0]['ДатаДокумента']}"
        if not snaps:
            findings.error("COVERAGE_INCOMPLETE", SNAPSHOTS_FILE, doc, f"нет снимков BEFORE_DOCUMENT перед денежным документом {title}")
            continue
        if before_meta[doc] != (doc_rows[0]["ДатаОперации"], doc_rows[0]["ПозицияДокумента"]):
            findings.error("CSV_INVALID", SNAPSHOTS_FILE, doc, f"дата/позиция снимка {before_meta[doc]} не равны дате операции и позиции документа {title} в ledger62")
        for r in doc_rows:
            for key in ledger_keys(r):
                if key not in snaps:
                    findings.error("COVERAGE_INCOMPLETE", SNAPSHOTS_FILE, record_key("BEFORE_DOCUMENT", before_meta[doc][0], doc, *key[:2], *key[2:]),
                                   f"нет снимка перед {title} для стороны {r['ledger_event_id']}")
    for doc in sorted(set(before) - set(money_docs))[:EXAMPLES]:
        findings.error("CSV_INVALID", SNAPSHOTS_FILE, doc, "снимок BEFORE_DOCUMENT перед документом без денежной корреспонденции 62 в ledger62")

    summary["balance_snapshots"] = {
        "rows": len(rows),
        "day_start": {date: {"customers": sum(1 for k in keys if k[0] == "CUSTOMER"), "contract_rows": sum(1 for k in keys if k[0] != "CUSTOMER"),
                             "customers_nonzero": sum(1 for k, r in keys.items() if k[0] == "CUSTOMER" and signed(r) not in (0, None)),
                             "customers_sum_kopecks": sum(signed(r) or 0 for k, r in keys.items() if k[0] == "CUSTOMER")}
                      for date, keys in sorted(day.items())},
        "before_document": {"documents": len(before), "money_documents_in_ledger": len(money_docs),
                            "rows": sum(len(v) for v in before.values())},
        "required_pairs": len(required), "incoming_only_pairs": len(incoming),
        "statuses": dict(sorted(statuses.items())),
        "unknown_required_rows": unknown_required, "unread_contract_pairs": len(unread_pairs),
    }
    return {"day": day, "before": before, "before_meta": before_meta, "by_doc": by_doc, "money_docs": money_docs, "reference": reference,
            "declared_mismatch": {d["record_key"] for d in diag if d["file"] == SNAPSHOTS_FILE and d["code"] == "SNAPSHOT_MISMATCH" and d["severity"] == "WARNING"}}


def check_customer_sums(groups: dict[str, dict[tuple, dict]], kind: str, declared: set[str], findings: Findings) -> tuple[int, int, int]:
    """CUSTOMER = sum of its CUSTOMER_CONTRACT rows, EMPTY included (№1 §23.1). Returns (checked, mismatched,
    unconfirmed): a customer with an UNKNOWN contract row (ANALYTICS_UNKNOWN) or a mismatch the export declared as
    WARNING SNAPSHOT_MISMATCH stays visible and unconfirmed, never a successful reconciliation (№1 §27); a mismatch
    the export did not declare is an ERROR. An unknown customer row is the unknown query itself (SNAPSHOT_UNKNOWN)."""
    checked = mismatched = unconfirmed = 0
    for boundary, keys in groups.items():
        contracts: dict[str, list[dict]] = defaultdict(list)
        for k, r in keys.items():
            if k[0] == "CUSTOMER_CONTRACT":
                contracts[k[1]].append(r)
        for k, r in keys.items():
            if k[0] != "CUSTOMER":
                continue
            values = [signed(c) for c in contracts[k[1]]]
            total = signed(r)
            if total is None:
                continue
            label = record_key(kind, r["BoundaryDate"], r["BeforeDocumentID"], "CUSTOMER", k[1], "ALL", "")
            if label in declared or None in values:
                unconfirmed += 1
                if unconfirmed <= EXAMPLES:
                    reason = "выгрузка объявила WARNING SNAPSHOT_MISMATCH" if label in declared else "есть договорная строка UNKNOWN (ANALYTICS_UNKNOWN)"
                    known = sum(v for v in values if v is not None)
                    findings.add("WARNING", "SNAPSHOT_MISMATCH" if label in declared else "ANALYTICS_UNKNOWN", SNAPSHOTS_FILE, label,
                                 f"сверка покупателя с договорами не подтверждена: {reason}; сальдо покупателя {total} коп, известных договорных {known} коп")
                continue
            checked += 1
            if total != sum(values):
                mismatched += 1
                findings.error("SNAPSHOT_MISMATCH", SNAPSHOTS_FILE, label,
                               f"сальдо покупателя {total} коп не равно сумме договорных {sum(values)} коп ({len(values)} строк), выгрузка расхождение не объявила")
    if unconfirmed > EXAMPLES:
        findings.add("WARNING", "SNAPSHOT_MISMATCH", SNAPSHOTS_FILE, "PACKAGE", f"{kind}: всего неподтверждённых сверок покупателя с договорами {unconfirmed}")
    return checked, mismatched, unconfirmed


def reconcile_snapshots(groups: dict, ledger: list[dict], findings: Findings, summary: dict) -> None:
    """Movements between boundaries and up to the document with zero tolerance (№1 §23.1)."""
    day, before, by_doc = groups["day"], groups["before"], groups["by_doc"]
    result: dict = {}
    result["customer_vs_contracts_day"] = check_customer_sums(day, "DAY_START", groups["declared_mismatch"], findings)
    result["customer_vs_contracts_before"] = check_customer_sums(before, "BEFORE_DOCUMENT", groups["declared_mismatch"], findings)

    # DAY_START(q) + movements of q = DAY_START(next q).
    movements: dict[str, Counter] = defaultdict(Counter)
    for row in ledger:
        change = delta(row)
        if change is None or not DATE.fullmatch(row["ДатаОперации"]):
            continue
        for key in ledger_keys(row):
            movements[quarter_start(row["ДатаОперации"])][key] += change
    dates = sorted(day)
    checked = mismatched = 0
    for first, second in zip(dates, dates[1:]):
        for key, row in day[first].items():
            later = day[second].get(key)
            if later is None or signed(row) is None or signed(later) is None:
                continue
            checked += 1
            expected = signed(row) + movements[first][key]
            if expected != signed(later):
                mismatched += 1
                if mismatched <= EXAMPLES:
                    findings.error("SNAPSHOT_MISMATCH", SNAPSHOTS_FILE, record_key("DAY_START", second, "", *key[:2], *key[2:]),
                                   f"остаток на {first} {signed(row)} + движения квартала {movements[first][key]} = {expected}, на {second} {signed(later)}")
    if mismatched > EXAMPLES:
        findings.error("SNAPSHOT_MISMATCH", SNAPSHOTS_FILE, "PACKAGE", f"всего расхождений DAY_START с движениями ledger62: {mismatched}")
    result["day_start_chain"] = (checked, mismatched)

    # DAY_START(quarter of the document) + movements of earlier documents of the quarter = BEFORE_DOCUMENT.
    checked = mismatched = missing = 0
    running: Counter = Counter()
    current = ""
    for doc, doc_rows in by_doc.items():
        date = doc_rows[0]["ДатаОперации"]
        if not DATE.fullmatch(date):
            continue
        start = quarter_start(date)
        if start != current:
            running, current = Counter(), start
        for key, row in before.get(doc, {}).items():
            if signed(row) is None:
                continue
            base = day.get(start, {}).get(key)
            if base is None or signed(base) is None:
                missing += 1
                if missing <= EXAMPLES:
                    findings.error("SNAPSHOT_MISMATCH", SNAPSHOTS_FILE, record_key("BEFORE_DOCUMENT", date, doc, *key[:2], *key[2:]),
                                   f"нет известного DAY_START {start} для сверки снимка перед документом")
                continue
            checked += 1
            expected = signed(base) + running[key]
            if expected != signed(row):
                mismatched += 1
                if mismatched <= EXAMPLES:
                    own = sum(delta(r) or 0 for r in doc_rows if key in ledger_keys(r))
                    hint = " - равен остатку после документа: документ включён в снимок" if signed(row) == expected + own and own else ""
                    findings.error("SNAPSHOT_MISMATCH", SNAPSHOTS_FILE, record_key("BEFORE_DOCUMENT", date, doc, *key[:2], *key[2:]),
                                   f"DAY_START {start} {signed(base)} + движения до документа {running[key]} = {expected}, в снимке {signed(row)}{hint}")
        for r in doc_rows:
            change = delta(r)
            if change is not None:
                for key in ledger_keys(r):
                    running[key] += change
    if mismatched > EXAMPLES:
        findings.error("SNAPSHOT_MISMATCH", SNAPSHOTS_FILE, "PACKAGE", f"всего расхождений BEFORE_DOCUMENT с DAY_START и ledger62: {mismatched}")
    result["before_document"] = (checked, mismatched, missing)
    summary["reconciliation"] = {name: list(value) for name, value in result.items()}


# --- ledger_quarters.csv -------------------------------------------------------------------------------

def check_quarters(rows: list[dict], ledger: list[dict], day: dict, horizon: str | None, run_id: str, diag: list[dict],
                   failed: bool, findings: Findings, summary: dict) -> None:
    """№1 §23.4: one row per quarter of the coverage, four general controls, current quarter without its closing."""
    explained = {(d["code"], d["record_key"], d["field"]) for d in diag if d["file"] == QUARTERS_FILE}
    by_start = {}
    for row in rows:
        start = row["quarter_start"]
        if start in by_start:
            findings.error("ID_COLLISION", QUARTERS_FILE, start, "повтор quarter_start")
        by_start[start] = row
    expected = quarter_starts(horizon) if horizon else []
    if [row["quarter_start"] for row in rows] != expected:
        findings.error("COVERAGE_INCOMPLETE", QUARTERS_FILE, "PACKAGE", f"кварталы {[row['quarter_start'] for row in rows]}, ожидались подряд {expected}")
    counts = Counter(quarter_start(r["ДатаОперации"]) for r in ledger if DATE.fullmatch(r["ДатаОперации"]))
    movements = Counter()
    for r in ledger:
        change = delta(r)
        if change is not None and DATE.fullmatch(r["ДатаОперации"]):
            movements[quarter_start(r["ДатаОперации"])] += change
    reconciled = []
    for start in expected:
        row = by_start.get(start)
        if row is None:
            continue
        end = quarter_end(start)
        full = horizon is not None and end <= horizon
        values = {f: row[f] for f in ("opening_debit_kopecks", "opening_credit_kopecks", "closing_debit_kopecks", "closing_credit_kopecks")}
        if row["quarter_end"] != end:
            findings.error("CSV_INVALID", QUARTERS_FILE, start, f"quarter_end {row['quarter_end']!r}, календарный конец {end}")
        if horizon and row["covered_through"] != min(end, horizon):
            findings.error("CSV_INVALID", QUARTERS_FILE, start, f"covered_through {row['covered_through']!r}, ожидалось {min(end, horizon)}")
        if row["ledger_action"] != "REFRESHED" or row["ledger_source_run_id"] != run_id:
            findings.error("CSV_INVALID", QUARTERS_FILE, start, "FULL: ledger_action REFRESHED и ledger_source_run_id этого запуска")
        if row["ledger_row_count"] != str(counts[start]):
            findings.error("CSV_INVALID", QUARTERS_FILE, start, f"ledger_row_count {row['ledger_row_count']!r}, строк ledger62 в квартале {counts[start]}")
        opening = [values["opening_debit_kopecks"], values["opening_credit_kopecks"]]
        closing = [values["closing_debit_kopecks"], values["closing_credit_kopecks"]]
        if row["control_status"] not in {"KNOWN", "UNKNOWN"} or row["full_quarter"] not in {"0", "1"}:
            findings.error("CSV_INVALID", QUARTERS_FILE, start, f"control_status {row['control_status']!r}, full_quarter {row['full_quarter']!r}")
            continue
        if full:
            if row["control_status"] == "UNKNOWN":
                # Unknown controls are empty, never zeros; the reason is a READ_ERROR of the run (№1 §§23.4, 27).
                if any(opening + closing) or row["full_quarter"] != "0":
                    findings.error("CSV_INVALID", QUARTERS_FILE, start, f"UNKNOWN: суммы пусты, full_quarter 0; получено {values}, {row['full_quarter']!r}")
                if ("READ_ERROR", start, "control_status") not in explained:
                    findings.error("CSV_INVALID", QUARTERS_FILE, start, "контроли полного квартала UNKNOWN без READ_ERROR")
                continue
            if not all(KOPECKS.fullmatch(v) for v in opening + closing):
                findings.error("CSV_INVALID", QUARTERS_FILE, start, f"KNOWN без четырёх целых сумм: {values}")
                continue
            # A failed run is not accepted whatever it declares; a successful one has every full quarter complete.
            if not failed and row["full_quarter"] != "1":
                findings.error("CSV_INVALID", QUARTERS_FILE, start, "full_quarter 0 у полного квартала с известными контролями в успешном запуске")
        else:
            # The current quarter (№1 §23.4): never full, its calendar end is not replaced by the balance at the horizon.
            if any(closing) or row["control_status"] != "UNKNOWN" or row["full_quarter"] != "0":
                findings.error("CSV_INVALID", QUARTERS_FILE, start, "текущий неполный квартал: closing пусты, control_status UNKNOWN, full_quarter 0")
            if not all(KOPECKS.fullmatch(v) for v in opening):
                if ("READ_ERROR", start, "control_status") not in explained:
                    findings.error("CSV_INVALID", QUARTERS_FILE, start, f"начальные остатки текущего квартала не целые копейки: {opening}")
                continue
            if ("COVERAGE_INCOMPLETE", start, "closing_debit_kopecks") not in explained:
                findings.error("CSV_INVALID", QUARTERS_FILE, start, "нет WARNING COVERAGE_INCOMPLETE о неполном квартале")
        # General opening = sum of all customers at the quarter start; movements of a full quarter close it.
        opening_s = int(opening[0]) - int(opening[1])
        customers = [r for k, r in day.get(start, {}).items() if k[0] == "CUSTOMER"]
        total = None
        if customers and all(signed(r) is not None for r in customers):
            total = sum(signed(r) for r in customers)
        elif not day:
            # No DAY_START at any boundary (the empty area of check_snapshots): no customer holds a balance, so both
            # general opening sides are zero; a nonzero one means snapshots were lost, not an empty area.
            total = 0
        if total is not None:
            # Дт and Кт that cancel in the net are still balances of customers, and those need snapshots.
            lost = not day and any(int(v) for v in opening)
            reconciled.append(("opening=customers", start, opening_s == total and not lost))
            if opening_s != total:
                findings.error("SNAPSHOT_MISMATCH", QUARTERS_FILE, start, f"общий остаток на начало {opening_s} коп не равен сумме покупателей DAY_START {total} коп")
            elif lost:
                findings.error("SNAPSHOT_MISMATCH", QUARTERS_FILE, start, f"общие Дт {opening[0]} / Кт {opening[1]} коп на начало без снимков DAY_START: "
                               "ненулевые остатки требуют снимков, даже если взаимно погашаются")
        if full:
            closing_s = int(closing[0]) - int(closing[1])
            reconciled.append(("closing-opening=movements", start, closing_s - opening_s == movements[start]))
            if closing_s - opening_s != movements[start]:
                findings.error("SNAPSHOT_MISMATCH", QUARTERS_FILE, start, f"конец {closing_s} - начало {opening_s} = {closing_s - opening_s} коп, движения ledger62 квартала {movements[start]} коп")
            following = by_start.get(next_quarter(start))
            if following is not None and [following["opening_debit_kopecks"], following["opening_credit_kopecks"]] != closing:
                reconciled.append(("closing=next opening", start, False))
                findings.error("SNAPSHOT_MISMATCH", QUARTERS_FILE, start, f"конечные Дт/Кт {closing} не равны начальным следующего квартала "
                               f"{[following['opening_debit_kopecks'], following['opening_credit_kopecks']]}")
            elif following is not None:
                reconciled.append(("closing=next opening", start, True))
    summary["ledger_quarters"] = {
        "rows": len(rows),
        "quarters": {r["quarter_start"]: {f: r[f] for f in QUARTERS_HEADER[1:10] + ["ledger_row_count"]} for r in rows},
        "reconciled": dict(Counter(f"{name}: {'ok' if ok else 'MISMATCH'}" for name, _, ok in reconciled)),
    }


# --- package -------------------------------------------------------------------------------------------

def check_package(directory: Path) -> tuple[Findings, dict]:
    findings = Findings()
    summary: dict = {"directory": str(directory)}
    state, log_text, end, interrupted = e02.check_run_meta(directory, findings, summary, LABEL)
    window = window_of(log_text)
    horizon = summary.get("match_horizon_end")

    def table(name: str, header: list[str]) -> list[dict] | None:
        if interrupted and not (directory / name).is_file():
            findings.info("NOT_CHECKED", name, "PACKAGE", "файл не создан: запуск прерван")
            return None
        return read_table(directory, name, header, findings)

    if window and (window[0] != HISTORY_START or window[1] != horizon):
        findings.error("CSV_INVALID", "1c_export.log", "PACKAGE", f"окно {window}: начало - history_start {HISTORY_START}, конец - match_horizon_end {horizon}")
    diagnostics = e02.check_diagnostics(directory, findings, summary, state)
    id_map = table("id_map.csv", e02.ID_MAP_HEADER) or []
    by_key = e02.check_id_map(id_map, findings, summary)
    ledger = table(LEDGER_FILE, LEDGER_HEADER) or []
    e03.check_ledger(ledger, by_key, diagnostics, findings, summary)
    documents = table(DOCUMENTS_FILE, DOCUMENTS_HEADER) or []
    e04.check_documents(documents, by_key, ledger, diagnostics, window, findings, summary)
    snapshots = table(SNAPSHOTS_FILE, SNAPSHOTS_HEADER) or []
    groups = check_snapshots(snapshots, ledger, documents, by_key, diagnostics, horizon, findings, summary)
    if snapshots:
        reconcile_snapshots(groups, ledger, findings, summary)
    quarters = table(QUARTERS_FILE, QUARTERS_HEADER) or []
    check_quarters(quarters, ledger, groups["day"], horizon, directory.name, diagnostics, state == "FAILED", findings, summary)

    used = {row["source_document_id"] for row in ledger} | {row["payment_id"] for row in ledger} | {row["ДоговорID"] for row in ledger}
    used |= {row["document_id"] for row in documents} | {row["ДоговорID"] for row in documents}
    used |= {row["ДоговорID"] for row in snapshots} | {row["BeforeDocumentID"] for row in snapshots}
    orphans = [key for key in by_key if key[1] not in used]
    if orphans:
        message = f"записей id_map без ссылок из ledger62/documents/balance_snapshots: {len(orphans)}"
        if interrupted:
            findings.info("NOT_CHECKED", "id_map.csv", orphans[0][1], message + " (запуск прерван)")
        else:
            findings.error("CSV_INVALID", "id_map.csv", orphans[0][1], message)
    e02.check_selftest(table(SELFTEST_FILE, e02.SELFTEST_HEADER), SELFTEST_FILE, findings, summary)

    not_checked = []
    if not interrupted:
        if not ledger:
            not_checked.append((LEDGER_FILE, "нет строк ledger62: роли, оплаты, движения между границами не проверены"))
        if not documents:
            not_checked.append((DOCUMENTS_FILE, "нет документов-кандидатов: documents.csv и НДС не проверены"))
        if not groups["before"]:
            not_checked.append((SNAPSHOTS_FILE, "нет документов с денежной корреспонденцией 62: снимки BEFORE_DOCUMENT не проверены"))
        for name, reason in not_checked:
            findings.add("INCOMPLETE", "NOT_CHECKED", name, "PACKAGE", reason)
        states = {r["ContractState"] for r in snapshots}
        if "EMPTY" not in states:
            findings.info("NOT_CHECKED", SNAPSHOTS_FILE, "PACKAGE", "нет снимков с пустым договором (EMPTY)")
        if not any(r["Status"] == "UNKNOWN" for r in snapshots):
            findings.info("NOT_CHECKED", SNAPSHOTS_FILE, "PACKAGE", "нет неизвестных снимков UNKNOWN: их запись проверена только тестами")
        if horizon and all(quarter_end(q) > horizon for q in quarter_starts(horizon)):
            findings.info("NOT_CHECKED", QUARTERS_FILE, "PACKAGE", "в охвате нет полного квартала: конечные контроли и цепочка кварталов не проверены")
    expected_result = "FAIL" if state == "FAILED" else ("INCOMPLETE" if not_checked else "PASS")
    if log_text is not None:
        summary["log_result"] = end[-1] if end else None
        summary["window"] = "..".join(window) if window else None
        if not end:
            findings.info("RUN_NOT_FINISHED", "1c_export.log", "PACKAGE", "нет строки END: запуск прерван до завершения")
        elif end[-1] != expected_result:
            findings.error("CSV_INVALID", "1c_export.log", "PACKAGE", f"итог журнала {end[-1]}, по статусу {state!r} и составу ожидался {expected_result}")
        if window is None:
            findings.error("CSV_INVALID", "1c_export.log", "PACKAGE", f"нет строки START {LABEL} с окном чтения")
    else:
        findings.error("CSV_INVALID", "1c_export.log", "PACKAGE", "журнал запуска отсутствует")
    summary["verdict"] = "FAIL" if findings.errors or interrupted else ("INCOMPLETE" if not_checked else "PASS")
    summary["files_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()}
    return findings, summary


# --- compare -------------------------------------------------------------------------------------------

def compare_packages(first: Path, second: Path) -> tuple[Findings, dict]:
    findings = Findings()
    tables = {}
    for directory in (first, second):
        local = Findings()
        manifest = read_key_value(directory, "manifest.csv", e02.MANIFEST_KEYS, local) or {}
        log = directory / "1c_export.log"
        tables[directory] = {
            "source_id": manifest.get("source_id", ""),
            "ledger": read_table(directory, LEDGER_FILE, LEDGER_HEADER, local) or [],
            "documents": read_table(directory, DOCUMENTS_FILE, DOCUMENTS_HEADER, local) or [],
            "map": read_table(directory, "id_map.csv", e02.ID_MAP_HEADER, local) or [],
            "snapshots": read_table(directory, SNAPSHOTS_FILE, SNAPSHOTS_HEADER, local) or [],
            "quarters": read_table(directory, QUARTERS_FILE, QUARTERS_HEADER, local) or [],
            "window": window_of(log.read_bytes().decode("cp1251", "replace")) if log.is_file() else None,
        }
        for item in local.items:
            findings.add(item["severity"], item["code"], f"{directory.name}/{item['file']}", item["key"], item["message"])
    a, b = tables[first], tables[second]
    if not a["source_id"] or a["source_id"] != b["source_id"]:
        findings.error("COMPARE", "manifest.csv", "source_id", f"разные или пустые source_id: {a['source_id']!r} и {b['source_id']!r}")
    if a["window"] != b["window"]:
        findings.error("COMPARE", "1c_export.log", "PACKAGE", f"разные окна чтения {a['window']} и {b['window']}")
    summary = {}
    for name, part, key_fields, fields in (
        (LEDGER_FILE, "ledger", ("ledger_event_id",), LEDGER_HEADER),
        (DOCUMENTS_FILE, "documents", ("document_id",), DOCUMENTS_HEADER),
        ("id_map.csv", "map", ("entity_type", "id"), e02.ID_MAP_HEADER),
        (SNAPSHOTS_FILE, "snapshots", SNAPSHOT_KEY, SNAPSHOTS_HEADER),
        # ledger_source_run_id is the run that obtained the quarter: it differs between two FULL runs by design.
        (QUARTERS_FILE, "quarters", ("quarter_start",), [f for f in QUARTERS_HEADER if f != "ledger_source_run_id"]),
    ):
        left = {tuple(row[f] for f in key_fields): row for row in a[part]}
        right = {tuple(row[f] for f in key_fields): row for row in b[part]}
        only_left, only_right = sorted(set(left) - set(right)), sorted(set(right) - set(left))
        if only_left or only_right:
            findings.error("COMPARE", name, "PACKAGE", f"ключи только в первом: {len(only_left)}, только во втором: {len(only_right)}; пример {(only_left or only_right)[0]}")
        changed = Counter(field for key in set(left) & set(right) for field in fields if left[key][field] != right[key][field])
        if changed:
            findings.error("COMPARE", name, "PACKAGE", f"различаются поля общих ключей: {dict(changed)}")
        if [tuple(r[f] for f in key_fields) for r in a[part]] != [tuple(r[f] for f in key_fields) for r in b[part]] and not (only_left or only_right):
            findings.error("COMPARE", name, "PACKAGE", "порядок записей различается")
        summary[name] = {"common": len(set(left) & set(right)), "only_first": len(only_left), "only_second": len(only_right), "changed_fields": dict(changed)}
    return findings, summary


# --- reconciliation sheet ------------------------------------------------------------------------------

def sample_customers(day: dict[str, dict[tuple, dict]]) -> list[tuple[str, str]]:
    """Customers of the sheet (section 2): an incoming balance on several contracts, an incoming balance, a balance
    that arose in 2026, a balance on the empty contract. Also used by run_1c_sheet to fill the sheet from 1C."""
    dates = sorted(day)
    first = day.get(HISTORY_START, {})
    contracts_of = Counter(k[1] for k, r in first.items() if k[0] == "CUSTOMER_CONTRACT" and signed(r))
    incoming = [k[1] for k, r in first.items() if k[0] == "CUSTOMER" and signed(r)]
    samples = []
    several = [c for c in incoming if contracts_of[c] > 1]
    samples += [("входящий остаток по нескольким договорам", c) for c in several[:1]]
    samples += [("входящий остаток", c) for c in incoming if c not in several][:1]
    changing = [k[1] for k, r in day.get(dates[-1], {}).items() if k[0] == "CUSTOMER" and signed(r) and k[1] not in incoming] if dates else []
    samples += [("остаток возник в 2026 году", c) for c in changing[:1]]
    empty = [k[1] for d in dates for k, r in day[d].items() if k[2] == "EMPTY" and signed(r)]
    samples += [("остаток по пустому договору", c) for c in empty[:1]]
    return samples


def sample_documents(by_doc: dict[str, list[dict]], before: dict[str, dict]) -> list[tuple[str, str, str]]:
    """Money documents of the sheet (section 3) as (reason, document, customer)."""
    def find(predicate) -> str | None:
        return next((doc for doc, rows in by_doc.items() if doc in before and predicate(rows)), None)

    def payers(rows: list[dict]) -> Counter:
        return Counter(r["КонтрагентID"] for r in rows if r["MovementRole"] == "CASH_IN")

    chosen = []
    cash = find(lambda rows: rows[0]["ВидДокумента"].upper() == "ПРИХОДНЫЙОРДЕР")
    if cash:
        chosen.append(("приходный кассовый ордер", cash, by_doc[cash][0]["КонтрагентID"]))
    statement = find(lambda rows: rows[0]["ВидДокумента"].upper() == "ВЫПИСКА" and len(payers(rows)) > 3)
    if statement:
        customer = payers(by_doc[statement]).most_common()[-1][0]
        chosen.append(("выписка с оплатами нескольких покупателей", statement, customer))
    twice = find(lambda rows: rows[0]["ВидДокумента"].upper() == "ВЫПИСКА" and any(n > 1 for n in payers(rows).values()))
    if twice:
        chosen.append(("две оплаты одного покупателя в выписке — один снимок", twice, payers(by_doc[twice]).most_common(1)[0][0]))
    refund = find(lambda rows: any(r["MovementRole"] == "CASH_OUT" for r in rows))
    if refund:
        chosen.append(("возврат денег покупателю", refund, next(r["КонтрагентID"] for r in by_doc[refund] if r["MovementRole"] == "CASH_OUT")))
    manual = find(lambda rows: rows[0]["ВидДокумента"].upper() == "ОПЕРАЦИЯ")
    if manual:
        chosen.append(("ручная операция с деньгами (UNKNOWN)", manual, by_doc[manual][0]["КонтрагентID"]))
    return chosen


def sheet(directory: Path) -> str:
    local = Findings()
    ledger = read_table(directory, LEDGER_FILE, LEDGER_HEADER, local) or []
    documents = read_table(directory, DOCUMENTS_FILE, DOCUMENTS_HEADER, local) or []
    snapshots = read_table(directory, SNAPSHOTS_FILE, SNAPSHOTS_HEADER, local) or []
    quarters = read_table(directory, QUARTERS_FILE, QUARTERS_HEADER, local) or []
    manifest = read_key_value(directory, "manifest.csv", e02.MANIFEST_KEYS, local) or {}
    log = directory / "1c_export.log"
    log_text = log.read_bytes().decode("cp1251", "replace") if log.is_file() else ""
    id_map = read_table(directory, "id_map.csv", e02.ID_MAP_HEADER, local) or []
    # Contract name of id_map (Наименование) covers contracts known only from the incoming balances.
    names = {row["id"]: row["номер"] for row in id_map if row["entity_type"] == "CONTRACT"}
    for row in ledger + documents:
        if row["КонтрагентID"]:
            names.setdefault(row["КонтрагентID"], row["Контрагент"])
        if row["ДоговорID"]:
            names.setdefault(row["ДоговорID"], row["Договор"])
    day: dict[str, dict[tuple, dict]] = defaultdict(dict)
    before: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for row in snapshots:
        (day[row["BoundaryDate"]] if row["BoundaryKind"] == "DAY_START" else before[row["BeforeDocumentID"]])[slice_key(row)] = row
    by_doc = documents_in_order(ledger)

    def money(value: str) -> str:
        return rub(int(value)) if KOPECKS.fullmatch(value) else "—"

    def balance(row: dict | None) -> str:
        if row is None:
            return "нет строки"
        if row["Status"] != "KNOWN":
            return "неизвестно (UNKNOWN)"
        s = signed(row)
        return f"Дт {money(row['ОстатокДтКоп'])} / Кт {money(row['ОстатокКтКоп'])} (сальдо {rub(s)}{' долг' if s > 0 else ' аванс' if s < 0 else ''})"

    def who(customer: str) -> str:
        return f"{customer[2:]} {names.get(customer, '')}".strip()

    def contract_name(key: tuple) -> str:
        if key[2] == "EMPTY":
            return "пустой договор"
        if key[2] == "UNKNOWN":
            return "договор не прочитан"
        return f"договор {key[3].split('|')[-1]} «{names.get(key[3], '')}»"

    dates = sorted(day)
    lines = [
        f"# Ведомость сверки КП0.5 — {directory.name}",
        "",
        f"Источник `{manifest.get('source_id', '')}`, охват {HISTORY_START}..{manifest.get('match_horizon_end', '?')}. Суммы в рублях; сальдо = Дт − Кт: плюс — долг покупателя, минус — аванс.",
        "Счета 62.1 и 62.2 в снимках сложены; в 1С сверяйте их сумму (или каждый субсчёт, если ведомость показывает их отдельно).",
        "",
        "## 1. Общие квартальные контроли (ledger_quarters.csv)",
        "",
        "Откройте оборотно-сальдовую ведомость по счёту 62 с разворотом по субсчетам за каждый квартал. Сальдо на начало и на конец по 62.1 и 62.2 должно совпасть со строками журнала; в таблице они сложены. У текущего квартала конечные остатки должны быть пусты: дата выгрузки — не конец квартала.",
        "",
        "| Квартал | Охват до | Полный | Начало Дт | Начало Кт | Конец Дт | Конец Кт | Статус | Строк ledger62 |",
        "|---|---|---|---:|---:|---:|---:|---|---:|",
    ]
    for q in quarters:
        lines.append(f"| {q['quarter_start']}..{q['quarter_end']} | {q['covered_through']} | {q['full_quarter']} | {money(q['opening_debit_kopecks'])} | {money(q['opening_credit_kopecks'])} | "
                     f"{money(q['closing_debit_kopecks']) if q['closing_debit_kopecks'] else 'пусто'} | {money(q['closing_credit_kopecks']) if q['closing_credit_kopecks'] else 'пусто'} | {q['control_status']} | {q['ledger_row_count']} |")
    accounts = CONTROL_LOG.findall(log_text)
    if accounts:
        lines += ["", "По субсчетам (журнал запуска):", "", "| Квартал | Счёт | Сальдо на начало Дт | Кт | Сальдо на конец Дт | Кт |", "|---|---|---:|---:|---:|---:|"]
        for start, account, snd, snk, skd, skk, end in accounts:
            lines.append(f"| {start}{'..' + end if end else ''} | {account} | {rub(int(snd))} | {rub(int(snk))} | {rub(int(skd)) if skd else '—'} | {rub(int(skk)) if skk else '—'} |")
    lines += [""]

    lines += ["## 2. Остатки на начало кварталов (DAY_START)", "",
              "Строки пишутся для всех покупателей и договоров входящих остатков, ledger62 и documents, включая нулевые. Сумма сальдо всех покупателей на дату должна равняться общему сальдо на начало квартала из раздела 1.", "",
              "| Дата | Покупателей | С ненулевым сальдо | Договорных строк | Сумма сальдо покупателей |", "|---|---:|---:|---:|---:|"]
    for date in dates:
        keys = day[date]
        customers = [r for k, r in keys.items() if k[0] == "CUSTOMER"]
        lines.append(f"| {date} | {len(customers)} | {sum(1 for r in customers if signed(r))} | {len(keys) - len(customers)} | {rub(sum(signed(r) or 0 for r in customers))} |")
    lines += [""]

    # Samples: incoming balance with several contracts, a customer whose balance changes between quarters, EMPTY.
    def history(customer: str) -> list[str]:
        out = ["| Граница | " + " | ".join(dates) + " |", "|---|" + "---:|" * len(dates)]
        keys = sorted({k for d in dates for k in day[d] if k[1] == customer}, key=lambda k: (k[0] != "CUSTOMER", k[3]))
        for key in keys:
            title = "Покупатель (CUSTOMER)" if key[0] == "CUSTOMER" else contract_name(key)
            out.append(f"| {title} | " + " | ".join(rub(signed(day[d][key])) if day[d].get(key) is not None and signed(day[d][key]) is not None else "?" for d in dates) + " |")
        moves = Counter()
        for row in ledger:
            if row["КонтрагентID"] == customer and delta(row) is not None:
                moves[quarter_start(row["ДатаОперации"])] += delta(row)
        out.append("| Движения ledger62 за квартал | " + " | ".join(rub(moves[d]) for d in dates) + " |")
        return out

    samples = sample_customers(day)
    lines += ["### Примеры покупателей", "",
              "Для каждого покупателя откройте ОСВ по счёту 62 с разворотом по контрагентам и договорам (или карточку счёта 62 по покупателю) на начало каждой даты: сальдо покупателя и каждого договора должно совпасть. Разность соседних дат равна движениям ledger62 за квартал — это проверено автоматически, в 1С достаточно сверить сами остатки.", ""]
    for reason, customer in samples:
        lines += [f"#### {who(customer)} — {reason}", ""] + history(customer) + [""]
    if not samples:
        lines += ["Нет покупателей с ненулевым остатком на границах.", ""]

    lines += ["## 3. Остатки перед денежными документами (BEFORE_DOCUMENT)", "",
              "Снимок — остаток покупателя после всех предыдущих документов и до проводок самого документа. Откройте карточку счёта 62 по покупателю: сальдо в строке перед первой проводкой документа должно равняться «до документа», после его проводок — «после документа». Сам документ в снимок не входит.", ""]

    def example(reason: str, doc: str, customer: str) -> list[str]:
        doc_rows = by_doc.get(doc, [])
        if not doc_rows:
            return []
        head = doc_rows[0]
        own = [r for r in doc_rows if r["КонтрагентID"] == customer]
        snaps = before.get(doc, {})
        out = [f"#### {head['ВидДокумента']} № {head['НомерДокумента']} от {head['ДатаДокумента']} — {reason}", "",
               f"Покупатель {who(customer)}. Проводки документа по 62 этого покупателя:", ""]
        out += [f"- проводка {r['НомерПроводкиВДокументе']}: Дт {r['СчетДебета']} Кт {r['СчетКредита']}, {rub(e03.amount_of(r) or 0)}, роль {r['MovementRole']}, {contract_name(ledger_keys(r)[1])}" for r in own]
        out += [""]
        for key in sorted((k for k in snaps if k[1] == customer), key=lambda k: (k[0] != "CUSTOMER", k[3])):
            row = snaps[key]
            change = sum(delta(r) or 0 for r in own if key in ledger_keys(r))
            after = f"{rub(signed(row) + change)}" if signed(row) is not None else "?"
            title = "Покупатель" if key[0] == "CUSTOMER" else contract_name(key)
            out.append(f"- {title}: до документа {balance(row)}; движения документа {rub(change)}; после документа {after}")
        return out + [""]

    chosen = sample_documents(by_doc, before)
    for reason, doc, customer in chosen:
        lines += example(reason, doc, customer)
    if not chosen:
        lines += ["Снимков перед документами нет.", ""]

    findings, summary = check_package(directory)
    rec = summary.get("reconciliation", {})
    quarters_rec = summary.get("ledger_quarters", {}).get("reconciled", {})
    lines += ["## 4. Автоматические сверки (check_e05 package)", "",
              f"Итог проверки: **{summary.get('verdict')}**, ошибок {findings.errors}.", "",
              f"- покупатель = сумма договоров: DAY_START проверено {rec.get('customer_vs_contracts_day', ['?'])[0]}, расхождений {rec.get('customer_vs_contracts_day', ['?', '?'])[1]}, "
              f"не подтверждено {rec.get('customer_vs_contracts_day', ['?'] * 3)[2]}; "
              f"BEFORE_DOCUMENT проверено {rec.get('customer_vs_contracts_before', ['?'])[0]}, расхождений {rec.get('customer_vs_contracts_before', ['?', '?'])[1]}, "
              f"не подтверждено {rec.get('customer_vs_contracts_before', ['?'] * 3)[2]};",
              f"- DAY_START + движения квартала = следующий DAY_START: проверено ключей {rec.get('day_start_chain', ['?'])[0]}, расхождений {rec.get('day_start_chain', ['?', '?'])[1]};",
              f"- DAY_START + движения до документа = BEFORE_DOCUMENT: проверено {rec.get('before_document', ['?'])[0]}, расхождений {rec.get('before_document', ['?', '?'])[1]};",
              f"- общие контроли: {quarters_rec or 'нет'}.", ""]
    for item in [i for i in findings.items if i["severity"] == "ERROR"][:20]:
        lines.append(f"- ERROR {item['code']} {item['file']} [{item['key']}]: {item['message']}")
    return "\n".join(lines) + "\n"


# --- static --------------------------------------------------------------------------------------------

def check_static() -> tuple[Findings, dict]:
    return e02.check_static(FORM_ACTION)


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
    out = sub.add_parser("sheet")
    out.add_argument("directory", type=Path)
    out.add_argument("--out")
    args = parser.parse_args(argv)
    if args.mode == "static":
        return e02.report(*check_static(), None)
    if args.mode == "package":
        return e02.report(*check_package(args.directory), args.json)
    if args.mode == "compare":
        return e02.report(*compare_packages(args.first, args.second), None)
    text = sheet(args.directory)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"записано {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
