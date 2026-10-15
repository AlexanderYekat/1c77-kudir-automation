"""Read-only checks for E0.3 of plan No. 13 (ledger62.csv: sides, signs, roles, payment membership, texts).

    python reports/check_e03.py static
    python reports/check_e03.py package exchange/KUDIR4_20261010_005 [--json out.json]
    python reports/check_e03.py compare exchange/KUDIR4_20261010_005 exchange/KUDIR4_20261010_006
    python reports/check_e03.py sheet exchange/KUDIR4_20261010_005 [--out sverka.md]

static  - the narrow text checks of check_e02 (declaration order, signatures, built-in names, calls against the
          1C 7.7 syntax helper, silent handlers, ТаблицаЗначений columns, cp1251, TXT/ERT parity) with the E0.3
          form button ВыгрузкаЭ03(). It is not a 1C compiler.
package - one directory written by the "Набор Э0.3" button: manifest/export_status/log, diagnostics, id_map,
          ledger62.csv by №1 §§6-12.1, 18, 21-22 and the CSV self-test. MovementRole is derived again here from
          accounts, sign and document kind and compared with the file; payment_id membership and forms, primary
          fields and their diagnostics, every side of every 62 posting, one money side per posting and the order
          of positions are checked. Files prove what 1C wrote, not completeness against the database.
compare - two runs over the same window and source must give identical ledger62 and id_map.
sheet   - a reconciliation sheet for the owner (Markdown): turnovers by subaccount, sample cash, bank (several
          payments of one statement), manual, refund, unknown, 62->62 and shipment movements with their rows.

Exit codes: 0 - PASS; 1 - ERROR findings or failed run (FAIL); 2 - no ledger rows in the window (INCOMPLETE).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_e02 as e02  # noqa: E402  shared CSV reader, ID parsing, manifest/diagnostics/id_map checks
from check_e02 import DATE, KOPECKS, Findings, parse_id, read_key_value, read_table  # noqa: E402

LABEL = "Э0.3"
FORM_ACTION = "ВыгрузкаЭ03()"
LEDGER_FILE = "ledger62.csv"
SELFTEST_FILE = "csv_selftest.csv"
# №1 §12, canonical order.
LEDGER_HEADER = [
    "ledger_event_id", "source_document_id", "payment_id", "ДатаОперации", "ПозицияДокумента",
    "НомерСтрокиВДокументе", "НомерПроводкиВДокументе", "ВидДокумента", "НомерДокумента", "ДатаДокумента",
    "СуммаКоп", "СчетДебета", "СчетКредита", "Счет62", "Сторона62", "КонтрагентID", "Контрагент", "ДоговорID",
    "Договор", "НомерДоговора", "ДатаДоговора", "ВидРасчетовID", "ВидРасчетовСПокупателем", "СодержаниеПроводки",
    "Содержание", "ПредставлениеПроводки", "КомментарийДокумента", "ПервичныйДокумент", "MovementRole",
    "PaymentKind", "PaymentGranularity", "ВидДокументаОплаты", "НомерДокументаОплаты", "ДатаДокументаОплаты",
    "НазначениеПлатежа", "ИсточникНазначения", "КомментарийДокументаОплаты",
]
PAYMENT_FIELDS = [
    "payment_id", "PaymentKind", "PaymentGranularity", "ВидДокументаОплаты", "НомерДокументаОплаты",
    "ДатаДокументаОплаты", "НазначениеПлатежа", "ИсточникНазначения", "КомментарийДокументаОплаты",
]
ROLES = {"CASH_IN", "CASH_OUT", "REVERSAL", "NONCASH", "UNKNOWN"}
MONEY_ROLES = {"CASH_IN", "CASH_OUT"}
SCOPE_ACCOUNTS = ("62.1", "62.2")
# №1 §14: document kind -> payment kind. Compared case-insensitively, as КУДиР4_ВидОплаты does with Врег.
PAYMENT_KIND_OF_DOC = {"ПРИХОДНЫЙОРДЕР": "CASH", "РАСХОДНЫЙОРДЕР": "CASH", "ВЫПИСКА": "BANK", "ОПЕРАЦИЯ": "MANUAL"}
GRANULARITY = {"CASH": "DOCUMENT", "BANK": "POSTING", "MANUAL": "POSTING"}
PRIMARY_CODES = {"PRIMARY_NOT_CONNECTED", "PRIMARY_PARTIAL", "PRIMARY_NOT_FOUND", "PRIMARY_AMBIGUOUS", "PRIMARY_ERROR"}


# --- rules ---------------------------------------------------------------------------------------------

def is_money(account: str) -> bool:
    # КУДиР_ЭтоДенежныйСчет: 50/51/55 and their subaccounts (№1 §4).
    return account.strip()[:2] in {"50", "51", "55"}


def scope_account(account: str) -> str:
    """№1 §4: 62.1, 62.2 and their subaccounts (62.1.1, ...) are in scope - КУДиР4_СубсчетОбласти; "" - outside."""
    for base in SCOPE_ACCOUNTS:
        if account == base or account.startswith(base + "."):
            return base
    return ""


def is_62(account: str) -> bool:
    return account.strip()[:2] == "62"


def payment_kind(doc_kind: str) -> str:
    return PAYMENT_KIND_OF_DOC.get(doc_kind.upper(), "")


def expected_role(side: str, other_account: str, doc_kind: str, amount: int) -> tuple[str, str]:
    """MovementRole of one 62 side by the E0.3 rule (КУДиР4_РольДвижения), derived independently of the file.

    Accounts, sign and confirmed document kind decide; a negative amount (storno or refund) is never classified
    automatically, the accountant decides it in Python (№1 §§12, 17; №2 §6.2).
    """
    if amount < 0:
        return "UNKNOWN", "отрицательная сумма"
    if not is_money(other_account):
        return "NONCASH", ""
    if not payment_kind(doc_kind):
        return "UNKNOWN", f"денежная корреспонденция в документе вида {doc_kind!r} вне перечня №1 §14"
    if payment_kind(doc_kind) == "MANUAL":
        # Owner's decision 10.10.2026 (KP0.3): a money posting of a manual operation is disputed - in the base
        # these are "СОПОСТАВЛЕНИЕ ДОГОВОРОВ", storno and re-posting of an advance through 51, not new money.
        return "UNKNOWN", "денежная проводка ручной операции"
    if side == "K":
        if doc_kind.upper() == "РАСХОДНЫЙОРДЕР":
            return "UNKNOWN", "поступление в расходном кассовом ордере"
        return "CASH_IN", ""
    if doc_kind.upper() == "ПРИХОДНЫЙОРДЕР":
        return "UNKNOWN", "выдача в приходном кассовом ордере"
    return "CASH_OUT", ""


def other_account(row: dict) -> str:
    return row["СчетКредита"] if row["Сторона62"] == "D" else row["СчетДебета"]


def amount_of(row: dict) -> int | None:
    return int(row["СуммаКоп"]) if KOPECKS.fullmatch(row["СуммаКоп"]) else None


def rub(kopecks: int) -> str:
    sign = "-" if kopecks < 0 else ""
    whole, part = divmod(abs(kopecks), 100)
    return f"{sign}{whole:,}".replace(",", " ") + f",{part:02d}"


# --- package -------------------------------------------------------------------------------------------

def check_ledger(rows: list[dict], by_key: dict, diag: list[dict], findings: Findings, summary: dict) -> None:
    has = {(d["code"], d["record_key"], d["field"]) for d in diag}
    has_key = {(d["code"], d["record_key"]) for d in diag}
    unavailable_fields = {d["field"] for d in diag if d["code"] in {"SOURCE_FIELD_UNAVAILABLE", "READ_ERROR"}}
    seen: set[str] = set()
    postings: dict[tuple[str, str], list[dict]] = defaultdict(list)
    payments: dict[str, list[dict]] = defaultdict(list)
    doc_money: Counter = Counter()
    doc_other: Counter = Counter()
    order_k: list[str] = []
    last_position: dict[str, str] = {}
    last_posting: dict[str, int] = {}
    without_id = 0
    out_of_scope: set[str] = set()

    def need(code: str, key: str, field: str | None, led: str, what: str) -> None:
        present = (code, key, field) in has if field is not None else (code, key) in has_key
        if not present:
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"нет диагностики {code} [{key}] {field or ''}: {what}")

    for row in rows:
        led = row["ledger_event_id"]
        if not led:
            without_id += 1
            continue
        parsed = parse_id(led)
        if parsed is None or parsed["kind"] != "LED":
            findings.error("CSV_INVALID", LEDGER_FILE, led, "форма ledger_event_id не LED|K|n|N")
            continue
        if led in seen:
            findings.error("ID_COLLISION", LEDGER_FILE, led, "повтор ledger_event_id")
        seen.add(led)
        k, posting = parsed["k"], parsed["posting"]
        doc_id = f"DOC|{k}"
        postings[(k, posting)].append(row)
        if row["source_document_id"] != doc_id:
            findings.error("CSV_INVALID", LEDGER_FILE, led, "source_document_id не DOC|K строки")
        if row["НомерПроводкиВДокументе"] != posting:
            findings.error("CSV_INVALID", LEDGER_FILE, led, "НомерПроводкиВДокументе не совпадает с ID")
        if row["НомерСтрокиВДокументе"]:
            findings.error("CSV_INVALID", LEDGER_FILE, led, "НомерСтрокиВДокументе должен быть пуст (№1 §6)")
        mapped = by_key.get(("DOCUMENT", doc_id))
        if mapped is None:
            findings.error("CSV_INVALID", LEDGER_FILE, led, "source_document_id не разрешается в id_map")
        elif (mapped["позиция"], mapped["вид"], mapped["номер"], mapped["дата"]) != (row["ПозицияДокумента"], row["ВидДокумента"], row["НомерДокумента"], row["ДатаДокумента"]):
            findings.error("ID_COLLISION", LEDGER_FILE, led, "позиция/вид/номер/дата строки отличаются от DOCUMENT в id_map")
        for field in ("ДатаОперации", "ДатаДокумента"):
            if not DATE.fullmatch(row[field]):
                findings.error("CSV_INVALID", LEDGER_FILE, led, f"{field} {row[field]!r}")
        amount = amount_of(row)
        if amount is None:
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"СуммаКоп {row['СуммаКоп']!r} не целые копейки")
            continue
        side = row["Сторона62"]
        if side not in {"D", "K"}:
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"Сторона62 {side!r}")
            continue
        if not row["СчетДебета"] or not row["СчетКредита"]:
            findings.error("CSV_INVALID", LEDGER_FILE, led, "пустой счёт проводки")
        if row["Счет62"] != (row["СчетДебета"] if side == "D" else row["СчетКредита"]) or not is_62(row["Счет62"]):
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"Счет62 {row['Счет62']!r} не счёт 62 стороны {side}")
        if not scope_account(row["Счет62"]):
            out_of_scope.add(row["Счет62"])
        other = other_account(row)

        # Order (№1 §22): documents follow the 1C time scale, postings inside a document by number.
        if not order_k or order_k[-1] != k:
            if k in last_position:
                findings.error("ID_COLLISION", LEDGER_FILE, led, "строки документа идут не подряд: документ встречается повторно")
            elif order_k and row["ПозицияДокумента"] <= last_position[order_k[-1]]:
                need("ORDER_UNKNOWN", doc_id, "ПозицияДокумента", led, "позиция не больше позиции предыдущего документа")
            order_k.append(k)
            last_position[k] = row["ПозицияДокумента"]
        elif int(posting) < last_posting[k]:
            need("ORDER_UNKNOWN", doc_id, "НомерПроводкиВДокументе", led, "номер проводки меньше предыдущего в документе")
        last_posting[k] = int(posting)

        # Role, re-derived (№1 §12).
        role = row["MovementRole"]
        expected, reason = expected_role(side, other, row["ВидДокумента"], amount)
        if role not in ROLES:
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"MovementRole {role!r} вне перечня")
        elif role != expected:
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"MovementRole {role}, по счетам/знаку/виду ожидалась {expected} {reason}".strip())
        if role == "UNKNOWN":
            need("MOVEMENT_ROLE_UNKNOWN", led, "MovementRole", led, "UNKNOWN без причины")
            if is_money(other):
                need("PAYMENT_MEMBERSHIP_UNKNOWN", led, "payment_id", led, "денежная корреспонденция без payment_id")
        if is_money(other):
            doc_money[doc_id] += 1
        else:
            doc_other[doc_id] += 1

        # Payment membership and forms (№1 §§7, 8, 12): payment_id only for CASH_IN/CASH_OUT.
        kind = payment_kind(row["ВидДокумента"])
        payment = row["payment_id"]
        if role in MONEY_ROLES:
            expected_payment = f"CASH|{k}" if kind == "CASH" else f"{kind}|{k}|{posting}"
            if payment != expected_payment:
                findings.error("CSV_INVALID", LEDGER_FILE, led, f"payment_id {payment!r}, ожидался {expected_payment!r}")
                continue
            payments[payment].append(row)
            if row["PaymentKind"] != kind or row["PaymentGranularity"] != GRANULARITY[kind]:
                findings.error("CSV_INVALID", LEDGER_FILE, led, f"PaymentKind/Granularity {row['PaymentKind']}/{row['PaymentGranularity']}, ожидались {kind}/{GRANULARITY[kind]}")
            pay_map = by_key.get(("PAYMENT", payment))
            if pay_map is None:
                findings.error("CSV_INVALID", LEDGER_FILE, led, "payment_id не разрешается в id_map")
            elif (pay_map["позиция"], pay_map["вид"], pay_map["номер_проводки"]) != (row["ПозицияДокумента"], row["ВидДокумента"], "" if kind == "CASH" else posting):
                findings.error("ID_COLLISION", LEDGER_FILE, led, "PAYMENT в id_map не совпадает с документом/проводкой строки")
            check_primary(row, kind, payment, led, has, findings)
            source = row["ИсточникНазначения"]
            if source == "POSTING_CONTENT":
                if row["НазначениеПлатежа"] != row["СодержаниеПроводки"]:
                    findings.error("CSV_INVALID", LEDGER_FILE, led, "НазначениеПлатежа не равно СодержаниеПроводки при POSTING_CONTENT")
            elif source == "UNAVAILABLE":
                if row["НазначениеПлатежа"] or "СодержаниеПроводки" not in unavailable_fields:
                    findings.error("CSV_INVALID", LEDGER_FILE, led, "UNAVAILABLE с непустым назначением или без диагностики СодержаниеПроводки")
            else:
                findings.error("CSV_INVALID", LEDGER_FILE, led, f"ИсточникНазначения {source!r} не используется экспортёром Э0.3")
            if row["КомментарийДокументаОплаты"] != row["КомментарийДокумента"]:
                findings.error("CSV_INVALID", LEDGER_FILE, led, "КомментарийДокументаОплаты отличается от комментария документа оплаты")
        else:
            filled = [field for field in PAYMENT_FIELDS if row[field]]
            if filled:
                findings.error("CSV_INVALID", LEDGER_FILE, led, f"поля оплаты заполнены у строки с ролью {role}: {filled}")

        # Analytics of its own side (№1 §11).
        customer = row["КонтрагентID"]
        if not customer:
            need("ANALYTICS_UNKNOWN", led, "КонтрагентID", led, "пустой покупатель")
            if row["ДоговорID"]:
                findings.error("CSV_INVALID", LEDGER_FILE, led, "ДоговорID без покупателя")
        elif not re.fullmatch(r"C\|[^|]+", customer):
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"КонтрагентID {customer!r} не C|<код>")
        contract = row["ДоговорID"]
        if contract:
            if not contract.startswith(f"D|{customer}|"):
                findings.error("CSV_INVALID", LEDGER_FILE, led, "ДоговорID не D|<КонтрагентID стороны>|<код договора>")
            if ("CONTRACT", contract) not in by_key:
                findings.error("CSV_INVALID", LEDGER_FILE, led, "ДоговорID не разрешается в id_map")
        elif row["Договор"] or row["НомерДоговора"] or row["ДатаДоговора"]:
            findings.error("CSV_INVALID", LEDGER_FILE, led, "реквизиты договора без ДоговорID")
        if row["ДатаДоговора"] and not DATE.fullmatch(row["ДатаДоговора"]):
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"ДатаДоговора {row['ДатаДоговора']!r}")
        if row["ВидРасчетовID"]:
            if not row["ВидРасчетовID"].startswith("VR|"):
                findings.error("CSV_INVALID", LEDGER_FILE, led, "ВидРасчетовID не VR|<идентификатор>")
        elif row["ВидРасчетовСПокупателем"]:
            findings.error("CSV_INVALID", LEDGER_FILE, led, "вид расчётов без ВидРасчетовID")

    # Every 62 side of every correspondence is present once, sides are numbered 1..m within the document and
    # posting number, D before K (№1 §§7, 12). A complex posting holds several correspondences under one number:
    # its rows are split into correspondences in file order - a 62 -> 62 correspondence gives a D row followed
    # by a K row with the same accounts and amount, any other gives one row of the 62 side.
    transfers = {"postings": 0, "same_customer": 0, "between_customers": 0}
    complex_postings = 0
    for (k, posting), sides in postings.items():
        key = f"LED|{k}|{posting}"
        numbers = [parse_id(row["ledger_event_id"])["n"] for row in sides]
        if numbers != [str(n) for n in range(1, len(sides) + 1)]:
            findings.error("ID_COLLISION", LEDGER_FILE, key, f"N стороны не 1..m подряд: {numbers}")
        if any(row["ДатаОперации"] != sides[0]["ДатаОперации"] for row in sides):
            findings.error("CSV_INVALID", LEDGER_FILE, key, "строки одной проводки различаются датой операции")
        correspondences = 0
        i = 0
        while i < len(sides):
            row = sides[i]
            correspondences += 1
            if is_62(row["СчетДебета"]) and is_62(row["СчетКредита"]):
                pair = sides[i:i + 2]
                same = len(pair) == 2 and all((r["СчетДебета"], r["СчетКредита"], r["СуммаКоп"]) == (row["СчетДебета"], row["СчетКредита"], row["СуммаКоп"]) for r in pair)
                if not same or [r["Сторона62"] for r in pair] != ["D", "K"]:
                    findings.error("CSV_INVALID", LEDGER_FILE, row["ledger_event_id"], f"у корреспонденции {row['СчетДебета']} -> {row['СчетКредита']} нет пары сторон D, K подряд: сторона потеряна или задвоена")
                    i += 1
                    continue
                transfers["postings"] += 1
                transfers["same_customer" if pair[0]["КонтрагентID"] == pair[1]["КонтрагентID"] else "between_customers"] += 1
                i += 2
                continue
            expected = "D" if is_62(row["СчетДебета"]) else "K"
            if row["Сторона62"] != expected:
                findings.error("CSV_INVALID", LEDGER_FILE, row["ledger_event_id"], f"сторона {row['Сторона62']}, по счетам {row['СчетДебета']} -> {row['СчетКредита']} ожидалась {expected}")
            i += 1
        complex_postings += correspondences > 1

    # Payments: CASH - one document; BANK/MANUAL - one posting number, every money correspondence of a complex
    # posting is its own row and counts once (№1 §§7, 12.1).
    money = Counter()
    per_kind = Counter()
    for payment, members in payments.items():
        kind = members[0]["PaymentKind"]
        per_kind[kind] += 1
        for row in members:
            money[(kind, row["MovementRole"])] += int(row["СуммаКоп"])

    # Non-cash sides in a document with money sides: their membership is not established (one warning per document).
    for doc_id in doc_money:
        if doc_other[doc_id]:
            need("PAYMENT_MEMBERSHIP_UNKNOWN", doc_id, "payment_id", doc_id, "неденежные стороны в документе с денежными")

    for account in sorted(out_of_scope):
        if not any(d["code"] == "COVERAGE_INCOMPLETE" and d["field"] == "Счет62" and account in d["message"] for d in diag):
            findings.error("CSV_INVALID", LEDGER_FILE, "PACKAGE", f"счёт {account} вне области 62.1+62.2 без ERROR COVERAGE_INCOMPLETE")
    if without_id:
        findings.error("READ_ERROR", LEDGER_FILE, "PACKAGE", f"строк без ledger_event_id: {without_id}")

    turnovers = defaultdict(lambda: {"D": 0, "K": 0})
    roles = defaultdict(lambda: [0, 0])
    for row in rows:
        amount = amount_of(row)
        if amount is None or row["Сторона62"] not in {"D", "K"}:
            continue
        turnovers[row["Счет62"]][row["Сторона62"]] += amount
        roles[row["MovementRole"]][0] += 1
        roles[row["MovementRole"]][1] += amount
    cash_in = sum(v for (kind, role), v in money.items() if role == "CASH_IN")
    cash_out = sum(v for (kind, role), v in money.items() if role == "CASH_OUT")
    summary["ledger62"] = {
        "rows": len(rows),
        "postings": len(postings),
        "documents": len(order_k),
        "roles": {role: {"rows": n, "kopecks": s} for role, (n, s) in sorted(roles.items())},
        "turnovers_kopecks": {account: dict(v) for account, v in sorted(turnovers.items())},
        "payments": dict(per_kind),
        "money_kopecks": {"cash_in": cash_in, "cash_out": cash_out, "net": cash_in - cash_out,
                          "by_kind": {f"{kind}:{role}": v for (kind, role), v in sorted(money.items())}},
        "transfers_62_62": transfers,
        "complex_postings": complex_postings,
        "multi_payment_statements": sum(1 for n in Counter(p.split("|")[1] for p in payments if p.startswith("BANK|")).values() if n > 1),
        "texts_with_edge_spaces": sum(1 for row in rows for f in ("СодержаниеПроводки", "Содержание") if row[f] != row[f].strip(" ")),
        "multi_line_texts": sum(1 for row in rows for f in ("СодержаниеПроводки", "Содержание", "КомментарийДокумента", "ПервичныйДокумент") if "\n" in row[f] or "\r" in row[f]),
        "primary_document_filled": sum(1 for row in rows if row["ПервичныйДокумент"].strip()),
        "presentation_filled": sum(1 for row in rows if row["ПредставлениеПроводки"].strip()),
    }


def check_primary(row: dict, kind: str, payment: str, led: str, has: set, findings: Findings) -> None:
    """Primary payment document fields (№1 §§8, 18): cash - the cash document itself; bank - owner's function;
    manual - only a proven source. An empty field must be explained by a diagnostic on payment_id and field."""
    fields = ("НомерДокументаОплаты", "ДатаДокументаОплаты")
    if kind == "CASH":
        if (row["ВидДокументаОплаты"], row["НомерДокументаОплаты"], row["ДатаДокументаОплаты"]) != (row["ВидДокумента"], row["НомерДокумента"], row["ДатаДокумента"]):
            findings.error("CSV_INVALID", LEDGER_FILE, led, "у CASH первичка - сам кассовый документ")
        return
    if row["ВидДокументаОплаты"]:
        findings.error("CSV_INVALID", LEDGER_FILE, led, f"ВидДокументаОплаты {kind} не передаётся экспортёром Э0.3")
    allowed = PRIMARY_CODES if kind == "BANK" else PRIMARY_CODES | {"SOURCE_FIELD_UNAVAILABLE"}
    for field in fields:
        if row[field]:
            continue
        if not any((code, payment, field) in has for code in allowed):
            findings.error("CSV_INVALID", LEDGER_FILE, led, f"пустое {field} {kind}-оплаты без диагностики {sorted(allowed)}")
    if row["ДатаДокументаОплаты"] and not DATE.fullmatch(row["ДатаДокументаОплаты"]):
        findings.error("CSV_INVALID", LEDGER_FILE, led, f"ДатаДокументаОплаты {row['ДатаДокументаОплаты']!r}")
    # A real payment order may carry the same number and date as the statement: equality alone does not prove
    # substitution (№1 §18), so it is shown for manual review, not reported as an error.
    if row["НомерДокументаОплаты"] and (row["НомерДокументаОплаты"], row["ДатаДокументаОплаты"]) == (row["НомерДокумента"], row["ДатаДокумента"]):
        findings.info("PRIMARY_EQUALS_SOURCE", LEDGER_FILE, payment, "номер и дата первички совпадают с реквизитами выписки/операции: сверить с платёжным документом")


def check_package(directory: Path) -> tuple[Findings, dict]:
    findings = Findings()
    summary: dict = {"directory": str(directory)}
    state, log_text, end, interrupted = e02.check_run_meta(directory, findings, summary, LABEL)

    def table(name: str, header: list[str]) -> list[dict] | None:
        if interrupted and not (directory / name).is_file():
            findings.info("NOT_CHECKED", name, "PACKAGE", "файл не создан: запуск прерван")
            return None
        return read_table(directory, name, header, findings)

    diagnostics = e02.check_diagnostics(directory, findings, summary, state)
    id_map = table("id_map.csv", e02.ID_MAP_HEADER) or []
    by_key = e02.check_id_map(id_map, findings, summary)
    ledger = table(LEDGER_FILE, LEDGER_HEADER) or []
    check_ledger(ledger, by_key, diagnostics, findings, summary)

    used = {row["source_document_id"] for row in ledger} | {row["payment_id"] for row in ledger} | {row["ДоговорID"] for row in ledger}
    orphans = [key for key in by_key if key[1] not in used]
    if orphans:
        message = f"записей id_map без ссылок из ledger62: {len(orphans)}"
        if interrupted:
            findings.info("NOT_CHECKED", "id_map.csv", orphans[0][1], message + " (запуск прерван)")
        else:
            findings.error("CSV_INVALID", "id_map.csv", orphans[0][1], message)
    positions = [row["позиция"] for row in id_map if row["entity_type"] == "DOCUMENT"]
    summary["positions"] = {
        "lengths": dict(Counter(len(p) for p in positions)),
        "trailing_space_counts": dict(Counter(len(p) - len(p.rstrip(" ")) for p in positions)),
    }
    e02.check_selftest(table(SELFTEST_FILE, e02.SELFTEST_HEADER), SELFTEST_FILE, findings, summary)

    not_checked = []
    if not interrupted:
        if not ledger:
            not_checked.append("нет строк ledger62: роли, оплаты и ID не проверены")
        for reason in not_checked:
            findings.add("INCOMPLETE", "NOT_CHECKED", LEDGER_FILE, "PACKAGE", reason)
        info = summary.get("ledger62", {})
        roles = info.get("roles", {})
        for kind in ("CASH", "BANK", "MANUAL"):
            if kind not in info.get("payments", {}):
                findings.info("NOT_CHECKED", LEDGER_FILE, "PACKAGE", f"в окне нет оплат {kind}")
        if "CASH_OUT" not in roles:
            findings.info("NOT_CHECKED", LEDGER_FILE, "PACKAGE", "в окне нет денежных возвратов CASH_OUT")
        if "UNKNOWN" not in roles:
            findings.info("NOT_CHECKED", LEDGER_FILE, "PACKAGE", "в окне нет движений UNKNOWN (сторно/отрицательных сумм)")
        if not info.get("transfers_62_62", {}).get("postings"):
            findings.info("NOT_CHECKED", LEDGER_FILE, "PACKAGE", "в окне нет проводок 62 -> 62")
        if not info.get("multi_payment_statements"):
            findings.info("NOT_CHECKED", LEDGER_FILE, "PACKAGE", "в окне нет выписки с несколькими оплатами")
    expected_result = "FAIL" if state == "FAILED" else ("INCOMPLETE" if not_checked else "PASS")
    if log_text is not None:
        window = re.findall(rf"START {LABEL} .*?окно=(\S+)", log_text)
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
            "map": read_table(directory, "id_map.csv", e02.ID_MAP_HEADER, local) or [],
            "window": re.findall(rf"START {LABEL} .*?окно=(\S+)", log.read_bytes().decode("cp1251", "replace")) if log.is_file() else [],
        }
        for item in local.items:
            findings.add(item["severity"], item["code"], f"{directory.name}/{item['file']}", item["key"], item["message"])
    a, b = tables[first], tables[second]
    if not a["source_id"] or a["source_id"] != b["source_id"]:
        findings.error("COMPARE", "manifest.csv", "source_id", f"разные или пустые source_id: {a['source_id']!r} и {b['source_id']!r}")
    if a["window"][:1] != b["window"][:1]:
        findings.error("COMPARE", "1c_export.log", "PACKAGE", f"разные окна чтения {a['window'][:1]} и {b['window'][:1]}")
    summary = {}
    for name, part, key_fields, fields in (
        (LEDGER_FILE, "ledger", ("ledger_event_id",), LEDGER_HEADER),
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
    local = Findings()
    rows = read_table(directory, LEDGER_FILE, LEDGER_HEADER, local) or []
    manifest = read_key_value(directory, "manifest.csv", e02.MANIFEST_KEYS, local) or {}
    start = e02.run_start(e02.log_of(directory))
    window = ["..".join(start[1])] if start else []
    docs: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        docs[row["source_document_id"]].append(row)

    def doc_title(doc_rows: list[dict]) -> str:
        r = doc_rows[0]
        return f"{r['ВидДокумента']} № {r['НомерДокумента']} от {r['ДатаДокумента']} (операция {r['ДатаОперации']})"

    def table_rows(selected: list[dict]) -> list[str]:
        out = ["| Пров. | N | Дт | Кт | Сумма, руб | Сторона | Покупатель | Договор | Вид расчётов | Роль | Оплата | Первичка |",
               "|---|---|---|---|---:|---|---|---|---|---|---|---|"]
        for r in selected:
            n = parse_id(r["ledger_event_id"])["n"] if parse_id(r["ledger_event_id"]) else ""
            pay = r["PaymentKind"] + (f" (проводка {r['НомерПроводкиВДокументе']})" if r["PaymentGranularity"] == "POSTING" else (" (документ)" if r["PaymentKind"] else ""))
            primary = " ".join(x for x in (r["ВидДокументаОплаты"], r["НомерДокументаОплаты"], r["ДатаДокументаОплаты"]) if x) or ("—" if r["payment_id"] else "")
            customer = f"{r['КонтрагентID'][2:]} {r['Контрагент']}".strip()
            contract = r["ДоговорID"].split("|")[-1] if r["ДоговорID"] else ""
            out.append(f"| {r['НомерПроводкиВДокументе']} | {n} | {r['СчетДебета']} | {r['СчетКредита']} | {rub(int(r['СуммаКоп']))} | {r['Сторона62']} {r['Счет62']} | {customer} | {contract} {r['НомерДоговора']} | {r['ВидРасчетовID'][3:]} | {r['MovementRole']} | {pay} | {primary} |")
        return out

    def section(title: str, what: str, doc_ids: list[str], only=None) -> list[str]:
        out = [f"## {title}", "", what, ""]
        if not doc_ids:
            return out + ["В окне нет таких движений — не проверено.", ""]
        for doc_id in doc_ids:
            selected = [r for r in docs[doc_id] if only is None or only(r)]
            money_in = sum(int(r["СуммаКоп"]) for r in selected if r["MovementRole"] == "CASH_IN")
            money_out = sum(int(r["СуммаКоп"]) for r in selected if r["MovementRole"] == "CASH_OUT")
            out += [f"### {doc_title(docs[doc_id])}", ""]
            out += table_rows(selected)
            out += ["", f"Поступило по строкам CASH_IN: {rub(money_in)} руб; возвращено CASH_OUT: {rub(money_out)} руб.", ""]
        return out

    def pick(predicate, limit: int) -> list[str]:
        found = [doc_id for doc_id, doc_rows in docs.items() if predicate(doc_rows)]
        if len(found) <= limit:
            return found
        step = (len(found) - 1) / (limit - 1) if limit > 1 else 0
        return [found[round(i * step)] for i in range(limit)]

    kind = lambda doc_rows: payment_kind(doc_rows[0]["ВидДокумента"])  # noqa: E731
    bank = [doc_id for doc_id, d in docs.items() if kind(d) == "BANK"]
    bank.sort(key=lambda doc_id: -sum(1 for r in docs[doc_id] if r["payment_id"]))
    bank_pick = bank[:1] + [doc_id for doc_id in bank[1:] if any(r["MovementRole"] == "CASH_OUT" for r in docs[doc_id])][:1]
    if len(bank_pick) < 2 and len(bank) > 1:
        bank_pick.append(bank[-1])

    turnovers = defaultdict(lambda: {"D": 0, "K": 0})
    roles = defaultdict(lambda: [0, 0])
    for r in rows:
        turnovers[r["Счет62"]][r["Сторона62"]] += int(r["СуммаКоп"])
        roles[r["MovementRole"]][0] += 1
        roles[r["MovementRole"]][1] += int(r["СуммаКоп"])
    customers = Counter(r["КонтрагентID"] for r in rows if r["КонтрагентID"])
    lines = [
        f"# Ведомость сверки КП0.3 — {directory.name}",
        "",
        f"Источник `{manifest.get('source_id', '')}`, окно чтения {window[0] if window else '?'}, строк ledger62 {len(rows)}, документов {len(docs)}.",
        "Суммы в рублях; знак — как в проводке (сторно отрицательное). Сверять с документами и отчётами 1С за то же окно.",
        "",
        "## 1. Обороты по субсчетам",
        "",
        "Сформируйте в 1С оборотно-сальдовую ведомость по счёту 62 с субсчетами за окно чтения. Обороты Дт/Кт каждого субсчёта должны совпасть до копейки. Совпадение итога не заменяет проверки отдельных движений ниже.",
        "",
        "| Счёт | Оборот Дт | Оборот Кт |",
        "|---|---:|---:|",
    ]
    for account, v in sorted(turnovers.items()):
        lines.append(f"| {account} | {rub(v['D'])} | {rub(v['K'])} |")
    lines += ["", "| Роль | Строк | Сумма |", "|---|---:|---:|"]
    for role, (n, s) in sorted(roles.items()):
        lines.append(f"| {role} | {n} | {rub(s)} |")
    lines += ["", "## 2. Покупатели", "", "Для трёх покупателей с наибольшим числом движений сверьте обороты Дт/Кт 62 (оба субсчёта вместе) с ОСВ по 62 в разрезе контрагентов.", "",
              "| Покупатель | Строк | Оборот Дт | Оборот Кт |", "|---|---:|---:|---:|"]
    for customer, n in customers.most_common(3):
        d = sum(int(r["СуммаКоп"]) for r in rows if r["КонтрагентID"] == customer and r["Сторона62"] == "D")
        k = sum(int(r["СуммаКоп"]) for r in rows if r["КонтрагентID"] == customer and r["Сторона62"] == "K")
        name = next(r["Контрагент"] for r in rows if r["КонтрагентID"] == customer)
        lines.append(f"| {customer[2:]} {name} | {n} | {rub(d)} | {rub(k)} |")
    lines.append("")
    lines += section("3. Кассовые документы (CASH)", "Откройте каждый ПКО/РКО: все проводки 62 документа — строки ниже, одна оплата на документ. Сверьте сумму, Дт/Кт, покупателя, договор, вид расчётов.",
                     pick(lambda d: kind(d) == "CASH", 3))
    lines += section("4. Банковские выписки (BANK)", "Откройте выписку: каждая проводка с 51 — отдельная оплата со своим номером проводки. Сверьте число оплат, суммы, покупателей и договоры. Первичка — номер и дата платёжки из строки выписки (`КУДиР4_ПервичкаБанка`): сверьте с документом в колонке «ПервичныйДокумент» строки; без первички в строке — PRIMARY_NOT_FOUND.",
                     bank_pick)
    lines += section("5. Ручные операции с деньгами", "Денежная проводка ручной «Операции» — UNKNOWN без оплаты (решение владельца 10.10.2026): оплата это, перенос между договорами или корректировка, решает бухгалтер. Сверьте суммы со знаком, счета, покупателя и договор.",
                     [doc_id for doc_id, d in docs.items() if kind(d) == "MANUAL" and any(is_money(other_account(r)) for r in d)][:10])
    lines += section("6. Денежные возвраты (CASH_OUT)", "Дт 62 — Кт 50/51/55 с положительной суммой. Подтвердите, что это фактическая выдача денег покупателю (возврат), а не другое событие.",
                     [doc_id for doc_id, d in docs.items() if any(r["MovementRole"] == "CASH_OUT" for r in d)][:10], only=lambda r: r["MovementRole"] == "CASH_OUT")
    lines += section("7. Неустановленная роль (UNKNOWN)", "Отрицательные суммы и денежные корреспонденции вне перечня видов. Движение влияет на сальдо, оплатой не считается до решения бухгалтера. Сверьте сумму со знаком и счета.",
                     [doc_id for doc_id, d in docs.items() if any(r["MovementRole"] == "UNKNOWN" for r in d)][:15], only=lambda r: r["MovementRole"] == "UNKNOWN")
    lines += section("8. Проводки 62 → 62 (перенос, зачёт аванса)", "Две строки на проводку: N=1 — сторона Дт, N=2 — сторона Кт, у каждой своя аналитика; денег нет, по одному покупателю влияние на сальдо нулевое.",
                     [doc_id for doc_id, d in docs.items() if any(is_62(r["СчетДебета"]) and is_62(r["СчетКредита"]) for r in d)][:10],
                     only=lambda r: is_62(r["СчетДебета"]) and is_62(r["СчетКредита"]))
    lines += section("9. Отгрузки и акты (NONCASH)", "Реализация Дт 62 — Кт 90: сверьте сумму, покупателя, договор и вид расчётов.",
                     pick(lambda d: d[0]["ВидДокумента"] == "РасходнаяНакладная", 1) + pick(lambda d: d[0]["ВидДокумента"] == "ОказаниеУслуг", 1))
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
