"""Read-only checks for E0.4 of plan No. 13 (documents.csv: candidate documents, attributes, comments, VAT aggregates).

    python reports/check_e04.py static
    python reports/check_e04.py package exchange/KUDIR4_20261012_001 [--json out.json]
    python reports/check_e04.py compare exchange/KUDIR4_20261012_001 exchange/KUDIR4_20261012_002
    python reports/check_e04.py sheet exchange/KUDIR4_20261012_001 [--out sverka.md]

static  - the narrow text checks of check_e02 (declaration order, signatures, built-in names, calls against the
          1C 7.7 syntax helper, silent handlers, ТаблицаЗначений columns, cp1251, TXT/ERT parity) with the E0.4
          form button ВыгрузкаЭ04(). It is not a 1C compiler.
package - one directory written by the "Набор Э0.4" button: manifest/export_status/log, diagnostics, id_map, ledger62.csv
          (the E0.3 checks) and documents.csv by №1 §§14-15.1, 21-23.3: role/type by kind, IDs resolved in id_map,
          mandatory fields, flags, VAT aggregates KNOWN only when consistent and UNKNOWN only with empty aggregates and a
          diagnostic, analytics, order of positions. Cross-checks with ledger62: every candidate document with 62
          postings in the window is in documents.csv with the same requisites; amounts, customers and settlement kinds
          of posted shipments/acts against their 62 postings are reported for review. Files prove what 1C wrote, not
          completeness against the database.
compare - two runs over the same window and source must give identical ledger62, documents and id_map.
sheet   - a reconciliation sheet for the owner (Markdown): totals per kind for the 1C document journals, sample invoices,
          shipments/acts with their base invoice comment, VAT composition, money-event documents, review lists.

Exit codes: 0 - PASS; 1 - ERROR findings or failed run (FAIL); 2 - no ledger rows or no candidate documents (INCOMPLETE).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

import check_e02 as e02  # noqa: E402  shared CSV reader, ID parsing, manifest/diagnostics/id_map checks
import check_e03 as e03  # noqa: E402  ledger62.csv checks of E0.3
from check_e02 import DATE, KOPECKS, Findings, parse_id, read_key_value, read_table  # noqa: E402
from check_e03 import LEDGER_FILE, LEDGER_HEADER, rub  # noqa: E402

LABEL = "Э0.4"
FORM_ACTION = "ВыгрузкаЭ04()"
DOCUMENTS_FILE = "documents.csv"
SELFTEST_FILE = "csv_selftest.csv"
# №1 §15, canonical order.
DOCUMENTS_HEADER = [
    "document_id", "DocumentRole", "DocumentType", "ВидДокумента", "НомерДокумента", "ДатаДокумента",
    "ПозицияДокумента", "КонтрагентID", "Контрагент", "ДоговорID", "Договор", "НомерДоговора", "ДатаДоговора",
    "ВидРасчетовID", "ВидРасчетовСПокупателем", "СуммаКоп", "СуммаОблагаемаяКоп", "СуммаНеоблагаемаяКоп",
    "СуммаНДСКоп", "Проведен", "ПомеченНаУдаление", "Комментарий", "Содержание", "VATCompositionStatus",
]
# №1 §14: kind -> (DocumentRole, DocumentType). Compared case-insensitively, as КУДиР4_ТипДокумента does with Врег.
CANDIDATES = {
    "СЧЕТ": ("INVOICE", "INVOICE"),
    "РАСХОДНАЯНАКЛАДНАЯ": ("RECEIVABLE", "GOODS_SHIPMENT"),
    "ОКАЗАНИЕУСЛУГ": ("RECEIVABLE", "SERVICE_ACT"),
}
VAT_FIELDS = ("СуммаОблагаемаяКоп", "СуммаНеоблагаемаяКоп", "СуммаНДСКоп")
BASE_LINK = re.compile(r"введен\s+на\s+основании", re.I)
# Log line of a document with VAT added on top of Сумма (Всего = Сумма + НДС): "<prefix><kind> <number> от <date>; ...".
VAT_ON_TOP_LOG = "Э0.4 НДС сверху: "


def candidate(kind: str) -> tuple[str, str] | None:
    return CANDIDATES.get(kind.upper())


def window_of(log_text: str | None) -> tuple[str, str] | None:
    found = re.findall(rf"START {LABEL} .*?окно=(\d{{4}}-\d{{2}}-\d{{2}})\.\.(\d{{4}}-\d{{2}}-\d{{2}})", log_text or "")
    return found[0] if found else None


def vat_on_top(log_text: str | None) -> list[tuple[str, str, str]]:
    """(kind, number, date) of documents the export logged as having VAT on top of Сумма, in log order."""
    return re.findall(rf"{re.escape(VAT_ON_TOP_LOG)}(\S+) (.*?) от (\d{{4}}-\d{{2}}-\d{{2}});", log_text or "")


def vat_aggregates(row: dict) -> tuple[int, int, int] | None:
    """(облагаемая, необлагаемая, НДС) of a KNOWN row when all three are integer kopecks, else None."""
    values = [row[f] for f in VAT_FIELDS]
    if row["VATCompositionStatus"] != "KNOWN" or not all(KOPECKS.fullmatch(v) for v in values):
        return None
    taxable, exempt, vat = map(int, values)
    return taxable, exempt, vat


def implied_rate(vat: int, taxable: int) -> str:
    """VAT rate implied by the aggregates when the amount includes VAT: НДС / (облагаемая - НДС), in whole percent."""
    base = taxable - vat
    return f"{round(vat * 100 / base, 1):g}%" if base > 0 else "?"


# --- documents.csv -------------------------------------------------------------------------------------

def check_documents(rows: list[dict], by_key: dict, ledger: list[dict], diag: list[dict], window: tuple[str, str] | None,
                    findings: Findings, summary: dict) -> None:
    has = {(d["code"], d["record_key"], d["field"]) for d in diag}
    file_errors = {(d["code"], d["field"]) for d in diag if d["file"] == DOCUMENTS_FILE and d["severity"] == "ERROR"}
    doc_errors = {d["record_key"] for d in diag if d["file"] == DOCUMENTS_FILE and d["severity"] == "ERROR" and d["field"] == "document_id"}

    def need(code: str, key: str, field: str, what: str) -> None:
        if (code, key, field) not in has:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, key, f"нет диагностики {code} [{key}] {field}: {what}")

    def explained(key: str, field: str, codes: set[str]) -> bool:
        # A field left empty because the attribute is not readable: ERROR once per kind and field (КУДиР4_ОшибкаВида)
        # or the document was not opened by the typed object at all.
        return key in doc_errors or any((code, field) in file_errors for code in codes) or any((code, key, field) in has for code in codes)

    seen: set[str] = set()
    previous_position = ""
    by_kind: dict[str, Counter] = defaultdict(Counter)
    amounts: dict[str, Counter] = defaultdict(Counter)
    rates: Counter = Counter()
    vat_unknown_reasons: Counter = Counter()
    comments = Counter()
    for row in rows:
        doc = row["document_id"]
        kind = row["ВидДокумента"]
        if not doc:
            if not any(d["field"] == "document_id" and d["code"] == "READ_ERROR" for d in diag if d["file"] == DOCUMENTS_FILE):
                findings.error("READ_ERROR", DOCUMENTS_FILE, "PACKAGE", f"строка без document_id без ERROR: {kind} {row['НомерДокумента']}")
            continue
        parsed = parse_id(doc)
        if parsed is None or parsed["kind"] != "DOC":
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "форма document_id не DOC|K")
            continue
        if doc in seen:
            findings.error("ID_COLLISION", DOCUMENTS_FILE, doc, "повтор document_id")
        seen.add(doc)
        mapped = by_key.get(("DOCUMENT", doc))
        if mapped is None:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "document_id не разрешается в id_map")
        elif (mapped["позиция"], mapped["вид"], mapped["номер"], mapped["дата"]) != (row["ПозицияДокумента"], kind, row["НомерДокумента"], row["ДатаДокумента"]):
            findings.error("ID_COLLISION", DOCUMENTS_FILE, doc, "позиция/вид/номер/дата отличаются от DOCUMENT в id_map")

        # Role and type by the confirmed list of kinds (№1 §14).
        expected = candidate(kind)
        if expected is None:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"вид {kind!r} вне перечня кандидатов №1 §14")
            continue
        role, doc_type = expected
        if (row["DocumentRole"], row["DocumentType"]) != expected:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"роль/тип {row['DocumentRole']}/{row['DocumentType']}, для вида {kind} ожидались {role}/{doc_type}")
        if not DATE.fullmatch(row["ДатаДокумента"]):
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"ДатаДокумента {row['ДатаДокумента']!r}")
        elif window and not window[0] <= row["ДатаДокумента"] <= window[1]:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"дата {row['ДатаДокумента']} вне окна чтения {window[0]}..{window[1]}")
        if not row["НомерДокумента"]:
            need("SOURCE_FIELD_UNAVAILABLE", doc, "НомерДокумента", "пустой номер")

        # Order (№1 §22): rows follow the 1C time scale; a position not above the previous one needs ORDER_UNKNOWN.
        position = row["ПозицияДокумента"]
        if not position:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "пустая позиция")
        else:
            if previous_position and position <= previous_position:
                need("ORDER_UNKNOWN", doc, "ПозицияДокумента", "позиция не больше позиции предыдущего документа")
            previous_position = position

        # Flags (№1 §15): 0/1, an unreadable flag is an export error, never a substituted 0.
        for field in ("Проведен", "ПомеченНаУдаление"):
            if row[field] not in {"0", "1"} and not (row[field] == "" and explained(doc, "Проведен", {"READ_ERROR"})):
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"{field} {row[field]!r} не 0/1 и не объяснён ERROR")

        # Analytics (№1 §§7, 11, 15).
        customer = row["КонтрагентID"]
        if not customer:
            if not explained(doc, "КонтрагентID", {"READ_ERROR"}):
                need("ANALYTICS_UNKNOWN", doc, "КонтрагентID", "пустой покупатель")
            if row["Контрагент"] or row["ДоговорID"]:
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "наименование или договор без КонтрагентID")
        elif not re.fullmatch(r"C\|[^|]+", customer):
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"КонтрагентID {customer!r} не C|<код>")
        contract = row["ДоговорID"]
        if contract:
            # ID договора строится по его владельцу (код договора уникален только у владельца): договор другого
            # покупателя допустим лишь с предупреждением, что связь документа с договором не подтверждена.
            if not re.fullmatch(r"D\|C\|[^|]+\|[^|]+", contract):
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "ДоговорID не D|C|<код владельца>|<код договора>")
            elif not contract.startswith(f"D|{customer}|"):
                need("ANALYTICS_UNKNOWN", doc, "ДоговорID", "договор другого покупателя (ДоговорID не D|<КонтрагентID документа>|…)")
            if ("CONTRACT", contract) not in by_key:
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "ДоговорID не разрешается в id_map")
        elif row["Договор"] or row["НомерДоговора"] or row["ДатаДоговора"]:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "реквизиты договора без ДоговорID")
        if row["ДатаДоговора"] and not DATE.fullmatch(row["ДатаДоговора"]):
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"ДатаДоговора {row['ДатаДоговора']!r}")
        vr = row["ВидРасчетовID"]
        if role == "INVOICE":
            if vr or row["ВидРасчетовСПокупателем"]:
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "у счёта вид расчётов не читается (№1 §15)")
        elif vr:
            if not vr.startswith("VR|"):
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "ВидРасчетовID не VR|<идентификатор>")
        else:
            if row["ВидРасчетовСПокупателем"]:
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "вид расчётов без ВидРасчетовID")
            if row["Проведен"] == "1" and not explained(doc, "ВидРасчетовID", {"READ_ERROR", "ANALYTICS_UNKNOWN"}):
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "у проведённого исполнения пуст вид расчётов без диагностики (№1 §15)")

        # Amount (№1 §§15, 21): integer kopecks; an unreadable total is an export error, not zero.
        amount = int(row["СуммаКоп"]) if KOPECKS.fullmatch(row["СуммаКоп"]) else None
        if amount is None and not (row["СуммаКоп"] == "" and explained(doc, "СуммаКоп", {"READ_ERROR"})):
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"СуммаКоп {row['СуммаКоп']!r} не целые копейки и не объяснена ERROR")

        # VAT aggregates (№1 §15.1).
        status = row["VATCompositionStatus"]
        values = [row[f] for f in VAT_FIELDS]
        aggregates = vat_aggregates(row)
        if status == "KNOWN":
            if amount is None or aggregates is None:
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"KNOWN без целых агрегатов или суммы: {values}, {row['СуммаКоп']!r}")
            else:
                taxable, exempt, vat = aggregates
                if taxable + exempt != amount:
                    findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"KNOWN: облагаемая {taxable} + необлагаемая {exempt} != СуммаКоп {amount}")
                if vat < 0:
                    findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "KNOWN с отрицательным НДС")
                if vat == 0 and taxable != 0:
                    findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "облагаемая сумма без НДС: строка с нулевым НДС входит в необлагаемую")
                if vat > 0:
                    rates[implied_rate(vat, taxable)] += 1
        elif status == "UNKNOWN":
            if any(values):
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"UNKNOWN с непустыми агрегатами {values}: неизвестность не кодируется числами")
            need("VAT_COMPOSITION_UNKNOWN", doc, "VATCompositionStatus", "состав НДС неизвестен")
            reason = next((d["message"] for d in diag if d["code"] == "VAT_COMPOSITION_UNKNOWN" and d["record_key"] == doc), "")
            vat_unknown_reasons[re.sub(r"\d+", "N", reason.split(": ", 1)[-1])[:90]] += 1
        else:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, f"VATCompositionStatus {status!r} вне KNOWN/UNKNOWN")
        if row["Содержание"]:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc, "Содержание непусто: источник не подтверждён (№1 §15)")

        flags = f"проведён {row['Проведен']} помечен {row['ПомеченНаУдаление']}"
        by_kind[kind]["rows"] += 1
        by_kind[kind][flags] += 1
        by_kind[kind][f"VAT {status}"] += 1
        amounts[kind]["СуммаКоп"] += amount or 0
        # Only aggregates verified as integer kopecks are summed; a malformed KNOWN row is already an ERROR above.
        if aggregates is not None:
            for field, value in zip(VAT_FIELDS, aggregates):
                amounts[kind][field] += value
        text = row["Комментарий"]
        comments["base_link" if BASE_LINK.search(text) else ("empty" if not text.strip() else "other")] += 1
        if text != text.strip(" ") and text.strip():
            comments["edge_spaces"] += 1
        if "\n" in text or "\r" in text:
            comments["multi_line"] += 1

    content = [d for d in diag if d["file"] == DOCUMENTS_FILE and d["field"] == "Содержание" and d["code"] == "SOURCE_FIELD_UNAVAILABLE" and d["record_key"] == "PACKAGE"]
    if len(content) != 1:
        findings.error("CSV_INVALID", "diagnostics.csv", "PACKAGE", f"ожидалась одна SOURCE_FIELD_UNAVAILABLE по Содержание documents.csv, найдено {len(content)}")

    summary["documents"] = {
        "rows": len(rows),
        "by_kind": {kind: dict(counter) for kind, counter in sorted(by_kind.items())},
        "kopecks": {kind: dict(counter) for kind, counter in sorted(amounts.items())},
        "implied_vat_rates": dict(rates.most_common()),
        "vat_unknown_reasons": dict(vat_unknown_reasons.most_common(10)),
        "comments": dict(comments),
    }
    check_against_ledger(rows, ledger, window, findings, summary)


def check_against_ledger(rows: list[dict], ledger: list[dict], window: tuple[str, str] | None, findings: Findings, summary: dict) -> None:
    """Documents of candidate kinds that posted to 62 in the window must be in documents.csv with the same requisites.

    Amounts and customers of posted shipments/acts are compared with their Dt 62 - Ct 90 rows, the settlement kind
    (attribute ВыбВидыРасчетовСПокупателями) with the kinds of the document's 62 rows, for review (INFO): a document
    may legitimately post differently, the list is material for the owner, not a verdict.
    """
    docs = {row["document_id"]: row for row in rows if row["document_id"]}
    ledger_docs: dict[str, list[dict]] = defaultdict(list)
    for row in ledger:
        if row["source_document_id"] and candidate(row["ВидДокумента"]):
            ledger_docs[row["source_document_id"]].append(row)
    missing = mismatched = 0
    for doc_id, posted in ledger_docs.items():
        first = posted[0]
        inside = window is None or window[0] <= first["ДатаДокумента"] <= window[1]
        row = docs.get(doc_id)
        if row is None:
            if inside:
                missing += 1
                findings.error("CSV_INVALID", DOCUMENTS_FILE, doc_id, f"документ-кандидат {first['ВидДокумента']} {first['НомерДокумента']} от {first['ДатаДокумента']} с проводками 62 отсутствует в documents.csv")
            continue
        if (row["ВидДокумента"], row["НомерДокумента"], row["ДатаДокумента"], row["ПозицияДокумента"]) != (first["ВидДокумента"], first["НомерДокумента"], first["ДатаДокумента"], first["ПозицияДокумента"]):
            mismatched += 1
            findings.error("ID_COLLISION", DOCUMENTS_FILE, doc_id, "вид/номер/дата/позиция в documents.csv и ledger62 различаются")
        # Both files read Комментарий of the same document exactly (№1 §10): any difference is a lost or altered text.
        if row["Комментарий"] != first["КомментарийДокумента"]:
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc_id, "Комментарий в documents.csv отличается от КомментарийДокумента ledger62")
        if row["Проведен"] == "0":
            findings.error("CSV_INVALID", DOCUMENTS_FILE, doc_id, "у непроведённого документа есть проводки 62: флаг Проведен прочитан неверно")

    review = {"posted_receivables": 0, "without_62": [], "amount_differs": [], "customer_differs": [], "settlement_differs": [], "invoices_with_62": []}
    for doc_id, row in docs.items():
        posted = ledger_docs.get(doc_id, [])
        if row["DocumentRole"] == "INVOICE":
            if posted:
                review["invoices_with_62"].append(doc_id)
            continue
        if row["Проведен"] != "1":
            continue
        review["posted_receivables"] += 1
        kinds = {r["ВидРасчетовID"] for r in posted if r["ВидРасчетовID"]}
        if row["ВидРасчетовID"] and kinds and kinds != {row["ВидРасчетовID"]}:
            review["settlement_differs"].append((doc_id, row["ВидРасчетовID"], sorted(kinds)))
        revenue = [r for r in posted if r["Сторона62"] == "D" and r["СчетКредита"].startswith("90")]
        if not revenue:
            review["without_62"].append(doc_id)
            continue
        total = sum(e03.amount_of(r) or 0 for r in revenue)
        if KOPECKS.fullmatch(row["СуммаКоп"]) and total != int(row["СуммаКоп"]):
            review["amount_differs"].append((doc_id, int(row["СуммаКоп"]), total))
        customers = {r["КонтрагентID"] for r in revenue}
        if customers != {row["КонтрагентID"]}:
            review["customer_differs"].append((doc_id, row["КонтрагентID"], sorted(customers)))
    for name, label in (("without_62", "проведённых исполнений без проводок Дт 62 Кт 90"),
                        ("amount_differs", "исполнений с суммой, отличной от Дт 62 Кт 90"),
                        ("customer_differs", "исполнений с покупателем, отличным от проводки Дт 62 Кт 90"),
                        ("settlement_differs", "исполнений с видом расчётов, отличным от вида их проводок 62"),
                        ("invoices_with_62", "счетов с проводками 62")):
        if review[name]:
            first = review[name][0]
            findings.info("REVIEW", DOCUMENTS_FILE, first[0] if isinstance(first, tuple) else first, f"{label}: {len(review[name])} — см. ведомость sheet")
    summary["documents_vs_ledger"] = {
        "candidate_documents_in_ledger": len(ledger_docs),
        "missing_in_documents": missing,
        "requisites_differ": mismatched,
        "posted_receivables": review["posted_receivables"],
        "posted_receivables_without_90": len(review["without_62"]),
        "amount_differs": len(review["amount_differs"]),
        "customer_differs": len(review["customer_differs"]),
        "settlement_differs": len(review["settlement_differs"]),
        "invoices_with_62": len(review["invoices_with_62"]),
    }


# --- package -------------------------------------------------------------------------------------------

def check_package(directory: Path) -> tuple[Findings, dict]:
    findings = Findings()
    summary: dict = {"directory": str(directory)}
    state, log_text, end, interrupted = e02.check_run_meta(directory, findings, summary, LABEL)
    window = window_of(log_text)

    def table(name: str, header: list[str]) -> list[dict] | None:
        if interrupted and not (directory / name).is_file():
            findings.info("NOT_CHECKED", name, "PACKAGE", "файл не создан: запуск прерван")
            return None
        return read_table(directory, name, header, findings)

    diagnostics = e02.check_diagnostics(directory, findings, summary, state)
    id_map = table("id_map.csv", e02.ID_MAP_HEADER) or []
    by_key = e02.check_id_map(id_map, findings, summary)
    ledger = table(LEDGER_FILE, LEDGER_HEADER) or []
    e03.check_ledger(ledger, by_key, diagnostics, findings, summary)
    documents = table(DOCUMENTS_FILE, DOCUMENTS_HEADER) or []
    check_documents(documents, by_key, ledger, diagnostics, window, findings, summary)
    on_top = vat_on_top(log_text)
    summary["vat_on_top"] = {"documents": len(on_top), "first": [" ".join(item) for item in on_top[:10]]}

    used = {row["source_document_id"] for row in ledger} | {row["payment_id"] for row in ledger} | {row["ДоговорID"] for row in ledger}
    used |= {row["document_id"] for row in documents} | {row["ДоговорID"] for row in documents}
    orphans = [key for key in by_key if key[1] not in used]
    if orphans:
        message = f"записей id_map без ссылок из ledger62/documents: {len(orphans)}"
        if interrupted:
            findings.info("NOT_CHECKED", "id_map.csv", orphans[0][1], message + " (запуск прерван)")
        else:
            findings.error("CSV_INVALID", "id_map.csv", orphans[0][1], message)
    contracts = defaultdict(set)
    for (entity, key), row in by_key.items():
        if entity == "CONTRACT":
            contracts[row["ссылка_1С"]].add(key)
    shared = [keys for keys in contracts.values() if len(keys) > 1]
    if shared:
        findings.info("REVIEW", "id_map.csv", sorted(shared[0])[0], f"один договор под разными ID (покупатель документа не владелец договора?): {len(shared)}")
    positions = [row["позиция"] for row in id_map if row["entity_type"] == "DOCUMENT"]
    summary["positions"] = {
        "lengths": dict(Counter(len(p) for p in positions)),
        "trailing_space_counts": dict(Counter(len(p) - len(p.rstrip(" ")) for p in positions)),
    }
    e02.check_selftest(table(SELFTEST_FILE, e02.SELFTEST_HEADER), SELFTEST_FILE, findings, summary)

    not_checked = []
    if not interrupted:
        if not ledger:
            not_checked.append((LEDGER_FILE, "нет строк ledger62: роли, оплаты и ID не проверены"))
        if not documents:
            not_checked.append((DOCUMENTS_FILE, "нет документов-кандидатов: documents.csv и НДС не проверены"))
        for name, reason in not_checked:
            findings.add("INCOMPLETE", "NOT_CHECKED", name, "PACKAGE", reason)
        info = summary.get("documents", {})
        kinds = {row["DocumentType"] for row in documents}
        for doc_type in ("INVOICE", "GOODS_SHIPMENT", "SERVICE_ACT"):
            if doc_type not in kinds:
                findings.info("NOT_CHECKED", DOCUMENTS_FILE, "PACKAGE", f"в окне нет документов типа {doc_type}")
        statuses = Counter(row["VATCompositionStatus"] for row in documents)
        if not statuses.get("UNKNOWN"):
            findings.info("NOT_CHECKED", DOCUMENTS_FILE, "PACKAGE", "в окне нет документов с неизвестным составом НДС")
        if not info.get("implied_vat_rates"):
            findings.info("NOT_CHECKED", DOCUMENTS_FILE, "PACKAGE", "в окне нет документов с НДС")
        if not any(row["Проведен"] == "0" for row in documents):
            findings.info("NOT_CHECKED", DOCUMENTS_FILE, "PACKAGE", "в окне нет непроведённых документов-кандидатов")
        if not any(row["ПомеченНаУдаление"] == "1" for row in documents):
            findings.info("NOT_CHECKED", DOCUMENTS_FILE, "PACKAGE", "в окне нет помеченных на удаление документов-кандидатов")
        if not info.get("comments", {}).get("base_link"):
            findings.info("NOT_CHECKED", DOCUMENTS_FILE, "PACKAGE", "в окне нет комментариев «Введен на основании»")
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

def sheet(directory: Path) -> str:
    from kudir.base_document_links import numbers_equal, parse_base_link

    local = Findings()
    documents = read_table(directory, DOCUMENTS_FILE, DOCUMENTS_HEADER, local) or []
    ledger = read_table(directory, LEDGER_FILE, LEDGER_HEADER, local) or []
    diag = read_table(directory, "diagnostics.csv", e02.DIAG_HEADER, local) or []
    manifest = read_key_value(directory, "manifest.csv", e02.MANIFEST_KEYS, local) or {}
    log = directory / "1c_export.log"
    log_text = log.read_bytes().decode("cp1251", "replace") if log.is_file() else ""
    window = window_of(log_text)
    vat_reason = {d["record_key"]: d["message"] for d in diag if d["code"] == "VAT_COMPOSITION_UNKNOWN"}
    invoices = [d for d in documents if d["DocumentRole"] == "INVOICE"]

    def money(value: str) -> str:
        return rub(int(value)) if KOPECKS.fullmatch(value) else "—"

    def shown(text: str) -> str:
        return text.replace("\r\n", "⏎").replace("\n", "⏎").replace("\r", "⏎")

    def card(d: dict) -> list[str]:
        customer = f"{d['КонтрагентID'][2:]} {d['Контрагент']}".strip() or "—"
        contract = f"{d['ДоговорID'].split('|')[-1]} «{d['Договор']}»" if d["ДоговорID"] else "—"
        vat = (f"облагаемая {money(d['СуммаОблагаемаяКоп'])}, необлагаемая {money(d['СуммаНеоблагаемаяКоп'])}, НДС {money(d['СуммаНДСКоп'])}"
               if d["VATCompositionStatus"] == "KNOWN" else f"UNKNOWN — {vat_reason.get(d['document_id'], 'причина не найдена')}")
        return [
            f"### {d['ВидДокумента']} № {d['НомерДокумента']} от {d['ДатаДокумента']}",
            "",
            f"- Покупатель: {customer}; договор: {contract}; вид расчётов: {d['ВидРасчетовСПокупателем'] or '—'}",
            f"- Сумма: {money(d['СуммаКоп'])} руб; состав НДС: {vat}",
            f"- Проведён: {d['Проведен']}; помечен на удаление: {d['ПомеченНаУдаление']}",
            f"- Комментарий (точно, в кавычках; перевод строки — ⏎): «{shown(d['Комментарий'])}»",
            "",
        ]

    def unique(rows: list[dict]) -> list[dict]:
        seen: set[str] = set()
        return [r for r in rows if not (r["document_id"] in seen or seen.add(r["document_id"]))]

    def pick(rows: list[dict], predicate, limit: int) -> list[dict]:
        found = [r for r in rows if predicate(r)]
        if len(found) <= limit:
            return found
        step = (len(found) - 1) / (limit - 1) if limit > 1 else 0
        return [found[round(i * step)] for i in range(limit)]

    def known_vat(d: dict) -> int:
        aggregates = vat_aggregates(d)
        return aggregates[2] if aggregates else 0

    lines = [
        f"# Ведомость сверки КП0.4 — {directory.name}",
        "",
        f"Источник `{manifest.get('source_id', '')}`, окно чтения {'..'.join(window) if window else '?'}, документов-кандидатов {len(documents)}, строк ledger62 {len(ledger)}.",
        "Суммы в рублях. Сверять с документами и журналами 1С за то же окно. Тексты в кавычках «» — точно как в CSV, включая пробелы.",
        "",
        "## 1. Итоги по видам",
        "",
        "Откройте журнал документов каждого вида за окно чтения (с непроведёнными и помеченными на удаление): число документов и сумма должны совпасть. Если в журнале есть отбор по проведённым — сверьте строку «проведён 1».",
        "",
        "| Вид | Флаги | Документов | Сумма | НДС (KNOWN) | Состав НДС UNKNOWN |",
        "|---|---|---:|---:|---:|---:|",
    ]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for d in documents:
        groups[(d["ВидДокумента"], "все")].append(d)
        groups[(d["ВидДокумента"], f"проведён {d['Проведен']}, помечен {d['ПомеченНаУдаление']}")].append(d)
    for (kind, flags), rows in sorted(groups.items()):
        total = sum(int(d["СуммаКоп"]) for d in rows if KOPECKS.fullmatch(d["СуммаКоп"]))
        lines.append(f"| {kind} | {flags} | {len(rows)} | {rub(total)} | {rub(sum(known_vat(d) for d in rows))} | {sum(1 for d in rows if d['VATCompositionStatus'] == 'UNKNOWN')} |")
    rates = Counter(implied_rate(known_vat(d), vat_aggregates(d)[0]) for d in documents if known_vat(d) > 0)
    lines += ["", "Ставки, получающиеся из агрегатов (НДС / (облагаемая − НДС)), — проверка, что облагаемая сумма (колонка «Всего») включает НДС:", ""]
    lines += [f"- {rate}: {n} док." for rate, n in rates.most_common()] or ["- документов с НДС нет"]
    lines += [""]

    lines += ["## 2. Счета (INVOICE)", "",
              "Откройте каждый счёт: номер, дата, покупатель, договор, сумма «Всего» и сумма НДС должны совпасть. Вид расчётов у счёта не выгружается.", ""]
    for d in unique(pick(invoices, lambda d: known_vat(d) > 0, 2) + pick(invoices, lambda d: vat_aggregates(d) is not None and known_vat(d) == 0, 1)
                    + pick(invoices, lambda d: d["Проведен"] == "0" or d["ПомеченНаУдаление"] == "1", 1)):
        lines += card(d)

    lines += ["## 3. Реализации и акты со ссылкой на счёт", "",
              "Откройте документ: комментарий «Введен на основании: Счет …» должен совпасть посимвольно (в 1С он может быть виден в поле «Комментарий»). Найденный по номеру и дате счёт — подсказка разбора Python, не реквизит 1С: проверьте, что исполнение введено на основании именно этого счёта.", ""]
    resolved = Counter()
    examples: list[tuple[dict, list[dict]]] = []
    for d in documents:
        if d["DocumentRole"] != "RECEIVABLE":
            continue
        link = parse_base_link(d["Комментарий"])
        if link is None or not link.is_invoice():
            resolved["нет ссылки на счёт"] += 1
            continue
        same_number = [i for i in invoices if numbers_equal(i["НомерДокумента"], link.base_number)]
        found = [i for i in same_number if not link.base_date or i["ДатаДокумента"] == link.base_date]
        if len(found) == 1:
            outcome = "счёт найден"
        elif found:
            outcome = "несколько счетов"
        elif same_number:
            # Run KUDIR4_20261012_001: the invoice was re-dated after the act was entered on its basis.
            outcome, found = "счёт с этим номером есть, дата другая", same_number
        else:
            outcome = "счёта нет в окне"
        resolved[outcome] += 1
        examples.append((d, found, outcome))
    lines += [f"Разбор комментариев исполнений: {', '.join(f'{k} — {v}' for k, v in resolved.most_common()) or 'исполнений нет'}.", ""]
    # Two resolved examples (shipment and act when both exist) and one of each unresolved outcome, if any.
    chosen = [next((e for e in examples if e[2] == "счёт найден" and e[0]["DocumentType"] == t), None) for t in ("GOODS_SHIPMENT", "SERVICE_ACT")]
    chosen += [next((e for e in examples if e[2] == outcome), None) for outcome in resolved if outcome != "счёт найден"]
    for d, found, outcome in [e for e in chosen if e is not None]:
        lines += card(d)
        targets = "; ".join("{} № {} от {}, сумма {}".format(i["ВидДокумента"], i["НомерДокумента"], i["ДатаДокумента"], money(i["СуммаКоп"])) for i in found)
        if outcome == "счёт с этим номером есть, дата другая":
            lines += [f"Счёт по комментарию: по номеру и дате не найден; с тем же номером — {targets}", ""]
        else:
            lines += [f"Счёт по комментарию: {targets or 'не найден в documents.csv'}", ""]

    lines += ["## 4. Состав НДС", "",
              "Документ с облагаемыми и необлагаемыми строками и документ с неизвестным составом. Сверьте сумму НДС по строкам табличной части.", ""]
    mixed = pick(documents, lambda d: known_vat(d) > 0 and vat_aggregates(d)[1] != 0, 1)
    unknown = pick(documents, lambda d: d["VATCompositionStatus"] == "UNKNOWN", 3)
    for d in unique(mixed + unknown):
        lines += card(d)
    if not mixed and not unknown:
        lines += ["В окне нет таких документов — не проверено.", ""]
    on_top = vat_on_top(log_text)
    lines += ["### НДС «сверху»", "",
              "Документы, где «Всего» = «Сумма» + «НДС» (НДС начислен сверх суммы строки). Сумма документа — «Всего», она же сумма проводки Дт 62 Кт 90. Откройте документ: «Всего» и НДС в 1С должны совпасть с карточкой.", ""]
    by_requisites = {(d["ВидДокумента"], d["НомерДокумента"], d["ДатаДокумента"]): d for d in documents}
    for item in on_top[:5]:
        d = by_requisites.get(item)
        lines += card(d) if d else [f"- {' '.join(item)}: нет в documents.csv", ""]
    if len(on_top) > 5:
        lines += [f"Остальные ({len(on_top) - 5}): " + "; ".join(f"{k} № {n} от {t}" for k, n, t in on_top[5:]), ""]
    if not on_top:
        lines += ["В журнале запуска нет документов с НДС «сверху».", ""]
    if rates:
        main_rate = rates.most_common(1)[0][0]
        odd_rate = [d for d in documents if known_vat(d) > 0 and implied_rate(known_vat(d), vat_aggregates(d)[0]) != main_rate]
        lines += [f"### Ставка не {main_rate}", "",
                  f"Документы, где НДС / (облагаемая − НДС) не равно основной ставке {main_rate}: НДС не соответствует облагаемой сумме («Всего» облагаемых строк). Агрегаты выгружены как есть (состав KNOWN). Откройте документ и сверьте НДС по строкам; исправлять ли документ, решает бухгалтер.", ""]
        for d in odd_rate[:5]:
            lines += card(d)
        if len(odd_rate) > 5:
            lines += [f"Остальные ({len(odd_rate) - 5}): " + "; ".join(f"{d['ВидДокумента']} № {d['НомерДокумента']} от {d['ДатаДокумента']}" for d in odd_rate[5:]), ""]
        if not odd_rate:
            lines += ["Нет: у всех документов с НДС одна ставка.", ""]

    lines += ["## 5. Проведённые исполнения против проводок Дт 62 Кт 90", "",
              "Сумма документа сравнивается с суммой его проводок выручки; покупатель — с покупателем проводки. Расхождение — повод открыть документ, не ошибка выгрузки само по себе.", ""]
    ledger_docs: dict[str, list[dict]] = defaultdict(list)
    for r in ledger:
        ledger_docs[r["source_document_id"]].append(r)
    differs = []
    for d in documents:
        if d["DocumentRole"] != "RECEIVABLE" or d["Проведен"] != "1":
            continue
        revenue = [r for r in ledger_docs.get(d["document_id"], []) if r["Сторона62"] == "D" and r["СчетКредита"].startswith("90")]
        total = sum(e03.amount_of(r) or 0 for r in revenue)
        if not revenue or (KOPECKS.fullmatch(d["СуммаКоп"]) and total != int(d["СуммаКоп"])) or {r["КонтрагентID"] for r in revenue} != {d["КонтрагентID"]}:
            differs.append((d, total, revenue))
    if differs:
        lines += ["| Документ | Сумма документа | Дт 62 Кт 90 | Покупатель документа | Покупатели проводок |", "|---|---:|---:|---|---|"]
        for d, total, revenue in differs[:30]:
            lines.append(f"| {d['ВидДокумента']} № {d['НомерДокумента']} от {d['ДатаДокумента']} | {money(d['СуммаКоп'])} | {rub(total) if revenue else 'нет проводок'} | {d['КонтрагентID'][2:]} | {', '.join(sorted({r['КонтрагентID'][2:] for r in revenue}))} |")
        lines += ["", f"Всего расхождений: {len(differs)}." if len(differs) > 30 else "", ""]
    else:
        lines += ["Расхождений нет: у каждого проведённого исполнения сумма и покупатель совпали с проводками Дт 62 Кт 90.", ""]
    kind_differs = []
    for d in documents:
        if d["DocumentRole"] != "RECEIVABLE" or d["Проведен"] != "1" or not d["ВидРасчетовID"]:
            continue
        kinds = {r["ВидРасчетовСПокупателем"] for r in ledger_docs.get(d["document_id"], []) if r["ВидРасчетовID"]}
        ids = {r["ВидРасчетовID"] for r in ledger_docs.get(d["document_id"], []) if r["ВидРасчетовID"]}
        if ids and ids != {d["ВидРасчетовID"]}:
            kind_differs.append((d, sorted(kinds)))
    lines += ["Вид расчётов документа (реквизит «ВыбВидыРасчетовСПокупателями») против вида расчётов его проводок 62:", ""]
    if kind_differs:
        lines += ["| Документ | Вид в документе | Виды в проводках |", "|---|---|---|"]
        for d, kinds in kind_differs[:30]:
            lines.append(f"| {d['ВидДокумента']} № {d['НомерДокумента']} от {d['ДатаДокумента']} | {d['ВидРасчетовСПокупателем']} | {', '.join(kinds)} |")
        lines += ["", f"Всего расхождений: {len(kind_differs)}." if len(kind_differs) > 30 else "", ""]
    else:
        lines += ["Расхождений нет.", ""]

    lines += ["## 6. Документы денежных событий", "",
              "Документы оплат не входят в documents.csv (№1 §14): их комментарий — поле КомментарийДокумента ledger62, первичка банка — поля НомерДокументаОплаты/ДатаДокументаОплаты. Номер и дата выписки в первичку не подставляются.", ""]
    for kind in ("ПриходныйОрдер", "Выписка"):
        rows = [r for r in ledger if r["ВидДокумента"] == kind and r["payment_id"]]
        if rows:
            r = rows[len(rows) // 2]
            lines += [f"- {kind} № {r['НомерДокумента']} от {r['ДатаДокумента']}: комментарий «{shown(r['КомментарийДокумента'])}»; первичка {r['НомерДокументаОплаты'] or '—'} {r['ДатаДокументаОплаты'] or ''}".rstrip()]
    primary = Counter(d["code"] for d in diag if d["code"].startswith("PRIMARY_") and d["field"] == "НомерДокументаОплаты")
    lines += ["", f"Банковские оплаты без первички по diagnostics: {dict(primary) or 'нет'}.", ""]
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
