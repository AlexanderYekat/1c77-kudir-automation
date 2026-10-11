"""Reference for the bank primary function КУДиР4_ПервичкаБанка (ТЗ №1 §18) from a BANKDIAG run.

    python reports/check_bank_primary.py reference exchange/BANKDIAG_20261011_001 [--out expected.csv]
    python reports/check_bank_primary.py compare exchange/BANKDIAG_20261011_001 exchange/KUDIR4_<run>
    python reports/check_bank_primary.py sheet exchange/BANKDIAG_20261011_001 exchange/KUDIR4_<run> [--out sverka.md]

reference - the algorithm of the 1C function over the statement rows and postings written by the «Выписки»
            button: every money posting takes the next free row with the same direction, amount, corr. account,
            subconto 1-3 and a posting text that starts the row's payment purpose; a row without a posting is
            skipped. The paired row gives the primary: the ПервичныйДокумент reference or the
            РеквизитыПлатДокументов list. Writes bank_primary_expected.csv into the BANKDIAG directory unless
            --out is given.
compare   - BANK payments of an E0.3 run against the reference by source_document_id and posting number:
            number, date and state (FOUND when the fields are filled without PRIMARY_* diagnostics, otherwise the
            diagnostic code).
sheet     - a reconciliation sheet for the owner (Markdown): sample payments of a run by case (ordinary, earlier
            payment order date, identical rows, one order on several rows, refund, list of orders, row without a
            posting above, no primary) with the statement row to open in 1C.

The reference reads the presentation «Вид Номер (дд.мм.гг)» of ПервичныйДокумент, while 1C reads НомерДок and
ДатаДок of the document itself, so a different number format shows up in compare as a mismatch.
Exit codes: 0 - PASS; 1 - ERROR findings.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import re
import sys
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_e02 as e02  # noqa: E402  Findings and the JSON report shared by the checkers

EXPECTED_FILE = "bank_primary_expected.csv"
EXPECTED_HEADER = ["source_document_id", "НомерДокумента", "ДатаДокумента", "НомерПроводки", "строка", "Номер",
                   "Дата", "Сост", "Причина"]
PRESENTATION = re.compile(r"^(?P<kind>.+) (?P<num>\S+) \((?P<d>\d\d)\.(?P<m>\d\d)\.(?P<y>\d\d)\)$")


def read_csv(path: Path) -> list[dict]:
    with io.open(path, encoding="cp1251", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def kopecks(text: str) -> int:
    # КУДиР_Копейки: Окр(Сумма * 100, 0), symmetric in sign.
    return int((Decimal(text or "0") * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def is_money(account: str) -> bool:
    # КУДиР_ЭтоДенежныйСчет.
    return account.strip()[:2] in {"50", "51", "55"}


def row_key(row: dict) -> str:
    # КУДиР4_ПервичкаКлючСтроки.
    income, outgo = kopecks(row["Приход"]), kopecks(row["Расход"])
    if income > 0 and outgo == 0:
        key = f"IN|{income}"
    elif outgo > 0 and income == 0:
        key = f"OUT|{outgo}"
    else:
        return ""
    return "|".join([key, row["КоррСчет"].strip(), row["Субконто1"], row["Субконто2"], row["Субконто3"]])


def posting_key(posting: dict) -> str:
    # КУДиР4_ПервичкаКлючПроводки.
    amount = int(posting["СуммаКоп"])
    if is_money(posting["СчетДт"]):
        side, corr = "K", posting["СчетКт"]
        key = f"IN|{amount}"
    elif is_money(posting["СчетКт"]):
        side, corr = "D", posting["СчетДт"]
        key = f"OUT|{amount}"
    else:
        return ""
    prefix = "Кт" if side == "K" else "Дт"
    return "|".join([key, corr.strip(), posting[prefix + "1"], posting[prefix + "2"], posting[prefix + "3"]])


def parse_date(text: str) -> datetime.date | None:
    # КУДиР4_ПервичкаДата: дд.мм.гг or дд.мм.гггг, two-digit years are 20xx; anything else is not a date.
    text = text.strip()
    if len(text) not in (8, 10) or text[2] != "." or text[5] != ".":
        return None
    day, month, year = text[:2], text[3:5], text[6:]
    if not (day.isdigit() and month.isdigit() and year.isdigit()) or not text.isascii():
        return None
    try:
        return datetime.date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
    except ValueError:
        return None


def parse_list(text: str, field_length: int) -> tuple[str, str, str, str]:
    """КУДиР4_ПервичкаСписок: «№ номер от дд.мм.гг; ...» -> (state, number, ISO date, reason)."""
    # Single spaces in the pattern: a cut right after a space loses it to the right trim, so a text from field
    # length - 1 counts as full (statement 0022: 99 characters «...; № 101051 от» in a field of 100).
    full = field_length > 0 and len(text) >= field_length - 1
    numbers: list[str] = []
    dates: list[datetime.date] = []
    cut = False
    items = text.split(";")
    while items and not items[-1].strip():
        items.pop()
    for index, raw in enumerate(items):
        item = raw.strip()
        tail = full and index == len(items) - 1
        if not item:
            continue
        if not item.startswith("№"):
            return "ERROR", "", "", f"элемент [{item}] не по шаблону «№ номер от дд.мм.гг»: [{text}]"
        item = item[1:].strip()
        at = (item + " ").find(" от ")
        if at < 0:
            if tail:
                cut = True
                break
            return "ERROR", "", "", f"элемент [№ {item}] не по шаблону «№ номер от дд.мм.гг»: [{text}]"
        number = item[:at].strip()
        if not number or " " in number:
            return "ERROR", "", "", f"номер в элементе [№ {item}] не по шаблону: [{text}]"
        numbers.append(number)
        if tail:
            cut = True
            break
        date = parse_date((item + " ")[at + 4:])
        if date is None:
            return "ERROR", "", "", f"дата в элементе [№ {item}] не по шаблону дд.мм.гг: [{text}]"
        dates.append(date)
    if not numbers:
        return "ERROR", "", "", f"в РеквизитыПлатДокументов нет ни одного целого номера: [{text}]"
    reasons = []
    date_iso = dates[0].isoformat() if dates else ""
    if len(set(dates)) > 1:
        date_iso = ""
        reasons.append("у платёжек списка разные даты, дата не выбирается")
    if cut:
        reasons.append(f"список занимает всё поле ({field_length} символов) и мог обрезаться: номера прочитанных платёжек")
    if reasons:
        return "PARTIAL", "; ".join(numbers), date_iso, "; ".join(reasons) + f": [{text}]"
    return "FOUND", "; ".join(numbers), date_iso, ""


def row_primary(row: dict, field_length: int) -> tuple[str, str, str, str]:
    """КУДиР4_ПервичкаСтроки over the presentation of the reference -> (state, number, ISO date, reason)."""
    reference = row["ПервичныйДокумент"].strip()
    details = row["РеквизитыПлатДокументов"].rstrip()
    if not reference:
        if not details.strip():
            return "NOT_FOUND", "", "", "первичный документ не указан (ПервичныйДокумент и РеквизитыПлатДокументов пусты)"
        return parse_list(details, field_length)
    match = PRESENTATION.match(reference)
    if not match:
        return "UNPARSED", "", "", f"представление ПервичныйДокумент не разобрано: [{reference}]"
    number = match["num"]
    date_iso = f"20{match['y']}-{match['m']}-{match['d']}"
    if details.strip():
        state, list_number, list_date, _ = parse_list(details, field_length)
        if not (state == "FOUND" and list_number == number and list_date == date_iso):
            return "AMBIGUOUS", "", "", f"заполнены и ПервичныйДокумент [{reference}], и РеквизитыПлатДокументов [{details.strip()}], они расходятся"
    return "FOUND", number, date_iso, ""


def pair_statement(rows: list[dict], postings: list[dict], field_length: int) -> tuple[str, str, dict]:
    """КУДиР4_ПервичкаВыписки -> (statement state, reason, {posting number: (row number, state, number, date, reason)})."""
    result: dict[str, tuple[int, str, str, str, str]] = {}
    skipped: list[tuple[int, str]] = []
    paired: list[tuple[int, str]] = []
    pointer = 0
    for index, posting in enumerate(postings, 1):
        if posting["НомерПроводки"] != str(index):
            return "ERROR", f"номер проводки {posting['НомерПроводки']} не равен порядковому {index}", {}
        key = posting_key(posting)
        if not key:
            continue
        text = posting["СодержаниеПроводки"].rstrip()
        row = pointer
        while row < len(rows):
            if row_key(rows[row]) == key and rows[row]["НазначениеПлатежа"].rstrip()[:len(text)] == text:
                break
            row += 1
        if row == len(rows):
            return "AMBIGUOUS", f"денежной проводке {index} нет следующей строки выписки с тем же счётом, суммой, субконто и текстом", {}
        skipped += [(n + 1, row_key(rows[n])) for n in range(pointer, row)]
        paired.append((row + 1, key))
        state, number, date, reason = row_primary(rows[row], field_length)
        if reason:
            reason = f"строка {row + 1} выписки: {reason}"
        result[str(index)] = (row + 1, state, number, date, reason)
        pointer = row + 1
    skipped += [(n + 1, row_key(rows[n])) for n in range(pointer, len(rows))]
    for number, key in skipped:
        for other, other_key in paired:
            if key and key == other_key:
                return "AMBIGUOUS", f"строка {number} без проводки совпадает по счёту, сумме и субконто со строкой {other}: какая из них проведена, не доказуемо", {}
    return "", "", result


def field_length_of(directory: Path) -> int:
    for column in read_csv(directory / "bank_columns.csv"):
        if column["идентификатор"] == "РеквизитыПлатДокументов":
            return int(column["длина"])
    return 0


def load_bankdiag(directory: Path) -> tuple[list[dict], dict, dict, int]:
    rows_by: dict[str, list[dict]] = defaultdict(list)
    postings_by: dict[str, list[dict]] = defaultdict(list)
    for row in read_csv(directory / "bank_rows.csv"):
        rows_by[row["source_document_id"]].append(row)
    for posting in read_csv(directory / "bank_postings.csv"):
        postings_by[posting["source_document_id"]].append(posting)
    return read_csv(directory / "bank_statements.csv"), rows_by, postings_by, field_length_of(directory)


def reference(directory: Path) -> tuple[list[dict], Counter]:
    statements, rows_by, postings_by, field_length = load_bankdiag(directory)
    expected: list[dict] = []
    states: Counter = Counter()
    for statement in statements:
        sid = statement["source_document_id"]
        state, reason, result = pair_statement(rows_by[sid], postings_by[sid], field_length)
        for posting in postings_by[sid]:
            if not posting_key(posting):
                continue
            number = posting["НомерПроводки"]
            if state:
                line, row_state, primary, date, why = "", state, "", "", reason
            else:
                line, row_state, primary, date, why = result[number]
            expected.append({"source_document_id": sid, "НомерДокумента": statement["НомерДокумента"],
                             "ДатаДокумента": statement["ДатаДокумента"], "НомерПроводки": number, "строка": str(line),
                             "Номер": primary, "Дата": date, "Сост": row_state, "Причина": why})
            states[row_state] += 1
    return expected, states


def write_expected(expected: list[dict], path: Path) -> None:
    with io.open(path, "w", encoding="cp1251", newline="") as handle:
        writer = csv.DictWriter(handle, EXPECTED_HEADER, delimiter=";", lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(expected)


def compare(bankdiag: Path, run: Path) -> tuple[e02.Findings, dict]:
    findings = e02.Findings()
    expected, states = reference(bankdiag)
    by_posting = {(item["source_document_id"], item["НомерПроводки"]): item for item in expected}
    codes: dict[str, set[str]] = defaultdict(set)
    for diag in read_csv(run / "diagnostics.csv"):
        if diag["code"].startswith("PRIMARY_"):
            codes[diag["record_key"]].add(diag["code"])
    payments: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    for row in read_csv(run / "ledger62.csv"):
        if row["PaymentKind"] == "BANK" and row["payment_id"]:
            payments[row["payment_id"]].add((row["source_document_id"], row["НомерПроводкиВДокументе"],
                                             row["НомерДокументаОплаты"], row["ДатаДокументаОплаты"]))
    result: Counter = Counter()
    for payment, variants in sorted(payments.items()):
        if len(variants) > 1:
            findings.error("PRIMARY_MISMATCH", "ledger62.csv", payment, f"у строк одной оплаты разная первичка: {sorted(variants)}")
            continue
        sid, posting, number, date = next(iter(variants))
        found = codes.get(payment, set())
        if len(found) > 1:
            findings.error("PRIMARY_MISMATCH", "diagnostics.csv", payment, f"несколько кодов первички: {sorted(found)}")
            continue
        actual = next(iter(found))[len("PRIMARY_"):] if found else "FOUND"
        item = by_posting.get((sid, posting))
        if item is None:
            result["not in BANKDIAG"] += 1
            continue
        result[f"expected {item['Сост']}"] += 1
        if (actual, number, date) != (item["Сост"], item["Номер"], item["Дата"]):
            findings.error("PRIMARY_MISMATCH", "ledger62.csv", payment,
                           f"1С: {actual} [{number}] [{date}]; эталон: {item['Сост']} [{item['Номер']}] [{item['Дата']}], "
                           f"выписка {item['НомерДокумента']} от {item['ДатаДокумента']}, строка {item['строка']}; {item['Причина']}")
        else:
            result["matched"] += 1
    summary = {"bankdiag": str(bankdiag), "run": str(run), "bank_payments": len(payments),
               "reference_states": dict(states), "compare": dict(result)}
    return findings, summary


def ru_date(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if len(iso) == 10 else iso


def rub(kopecks: str) -> str:
    return f"{int(kopecks) / 100:,.2f}".replace(",", " ").replace(".", ",")


def cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def sheet(bankdiag: Path, run: Path) -> str:
    """Reconciliation sheet for the owner: sample BANK payments of a run, one section per case, to be checked in 1C.
    Samples are chosen by rules over the data, so the code holds no customer names."""
    statements, rows_by, postings_by, field_length = load_bankdiag(bankdiag)
    codes: dict[str, str] = {}
    for diag in read_csv(run / "diagnostics.csv"):
        if diag["code"].startswith("PRIMARY_"):
            codes[diag["record_key"]] = diag["code"][len("PRIMARY_"):]
    payments = {(row["source_document_id"], row["НомерПроводкиВДокументе"]): row
                for row in read_csv(run / "ledger62.csv") if row["PaymentKind"] == "BANK" and row["payment_id"]}
    items: list[dict] = []
    for statement in statements:
        sid = statement["source_document_id"]
        rows = rows_by[sid]
        state, _, result = pair_statement(rows, postings_by[sid], field_length)
        if state:
            continue
        paired = {value[0] for value in result.values()}
        skipped = [n for n in range(1, len(rows) + 1) if n not in paired]
        for posting in postings_by[sid]:
            led = payments.get((sid, posting["НомерПроводки"]))
            if led is None:
                continue
            line = result[posting["НомерПроводки"]][0]
            items.append({"sid": sid, "led": led, "row": rows[line - 1], "line": line, "key": row_key(rows[line - 1]),
                          "after_skip": any(n < line for n in skipped), "state": codes.get(led["payment_id"], "FOUND")})
    used: set[str] = set()
    sections: list[tuple[str, str, list[dict]]] = []

    def take(title: str, note: str, chosen: list[dict]) -> None:
        chosen = [item for item in chosen if item["led"]["payment_id"] not in used]
        if chosen:
            used.update(item["led"]["payment_id"] for item in chosen)
            sections.append((title, note, chosen))

    def lag(item: dict) -> int:
        led = item["led"]
        return (datetime.date.fromisoformat(led["ДатаДокумента"]) - datetime.date.fromisoformat(led["ДатаДокументаОплаты"])).days

    by_reference = [i for i in items if i["state"] == "FOUND" and i["row"]["ПервичныйДокумент"].strip()]
    incoming = [i for i in by_reference if i["led"]["MovementRole"] == "CASH_IN"]
    take("Платёжка указана ссылкой, дата совпадает с выпиской", "Обычный случай.",
         [i for i in incoming if lag(i) == 0][:2])
    late = sorted((i for i in incoming if lag(i) > 0), key=lambda i: -lag(i))
    take("Дата платёжки раньше выписки", "Дата берётся из платёжки, а не из выписки.",
         [late[0], next(i for i in late if i["sid"] != late[0]["sid"])] if len(late) > 1 else late)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in by_reference:
        groups[(item["sid"], item["key"])].append(item)
    twins = [g for g in groups.values() if len(g) > 1 and len({i["led"]["НомерДокументаОплаты"] for i in g}) == len(g)]
    if twins:
        take("Одинаковые строки с разными платёжками",
             "Строки различаются только назначением платежа: у каждой оплаты должна быть своя платёжка, номера не перепутаны.",
             twins[0] + (max(twins, key=len) if max(twins, key=len) is not twins[0] else []))
    shared: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for item in incoming:
        shared[(item["sid"], item["row"]["ПервичныйДокумент"].strip(), item["led"]["КонтрагентID"])].append(item)
    split = [g for g in shared.values() if len(g) > 1 and len({i["row"]["НазначениеПлатежа"].rstrip() for i in g}) == 1]
    take("Одна платёжка на нескольких строках", "Товар и услуги оплачены одной платёжкой: номер и дата у строк одинаковые.",
         split[0] if split else [])
    take("Возврат покупателю", "Своя платёжка («Плат.пор.»).",
         [i for i in by_reference if i["led"]["MovementRole"] == "CASH_OUT"][:1])
    take("Несколько платёжек в одной строке",
         "Список из колонки РеквизитыПлатДокументов. У PARTIAL список в 1С обрезан длиной поля: по банковской выписке проверьте, "
         "сколько платёжек было на самом деле.",
         [i for i in items if i["row"]["РеквизитыПлатДокументов"].strip()])
    take("Выше в выписке строка без проводки",
         "Перед оплатой стоит строка «Сдача наличных в банк», по которой проводки нет: покупатель и сумма строки должны совпасть, "
         "то есть оплата сопоставлена своей строке, а не соседней.",
         [i for i in by_reference if i["after_skip"]][:1])
    missing = [i for i in items if i["state"] == "NOT_FOUND"]
    take("Первички в строке нет", "Обе колонки строки пусты: NOT_FOUND верно, номер и дата в выгрузке пусты.",
         [i for i in missing if i["row"]["НазначениеПлатежа"].strip().lower().startswith("экв")][:1]
         + [i for i in missing if i["row"]["НазначениеПлатежа"].startswith("Оплата по документу")][:1])

    version = next((re.search(r"version=(\S+)", line)[1] for line in
                    io.open(run / "1c_export.log", encoding="cp1251").read().splitlines() if " START Э0.3 version=" in line), "?")
    total = Counter(codes.get(led["payment_id"], "FOUND") for led in payments.values())
    lines = [
        "# Сверка первички банковских оплат с 1С", "",
        f"Запуск `{run.name}` (версия `{version}`), строки выписок — `{bankdiag.name}`. "
        f"BANK-оплат {len(payments)}: " + ", ".join(f"{state} {count}" for state, count in total.most_common()) + ".", "",
        "Как сверять:",
        "- Откройте выписку с указанными номером и датой, найдите строку табличной части с указанным номером. "
        "Покупатель и сумма строки должны совпасть с ведомостью — так проверяется, что оплата сопоставлена своей строке.",
        "- FOUND по ссылке: откройте документ из колонки «ПервичныйДокумент» строки. Его номер и дата должны совпасть "
        "с колонкой «В выгрузке». Для нескольких оплат полезно сверить номер и дату ещё и с банковской выпиской: это номер "
        "платёжки плательщика.",
        "- Список платёжек: колонка «РеквизитыПлатДокументов» строки.",
        "- NOT_FOUND: обе колонки строки пусты.",
        "- В последней колонке отметьте «да» или «нет».", "",
    ]
    for number, (title, note, chosen) in enumerate(sections, 1):
        lines += [f"## {number}. {title}", "", note, "",
                  "| Выписка | Строка | Проводка | Покупатель | Сумма, ₽ | Назначение платежа | В строке выписки | В выгрузке | Статус | Совпало |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for item in chosen:
            led, row = item["led"], item["row"]
            source = row["ПервичныйДокумент"].strip() or (f"список: {row['РеквизитыПлатДокументов'].strip()}"
                                                          if row["РеквизитыПлатДокументов"].strip() else "—")
            exported = " от ".join(x for x in (led["НомерДокументаОплаты"], ru_date(led["ДатаДокументаОплаты"])) if x) or "—"
            purpose = row["НазначениеПлатежа"].strip()
            lines.append(f"| {led['НомерДокумента']} от {ru_date(led['ДатаДокумента'])} | {item['line']} | "
                         f"{led['НомерПроводкиВДокументе']} | {cell(led['Контрагент'])} | {rub(led['СуммаКоп'])} | "
                         f"{cell(purpose[:60] + ('…' if len(purpose) > 60 else ''))} | {cell(source)} | {cell(exported)} | "
                         f"{item['state']} | |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    ref = sub.add_parser("reference")
    ref.add_argument("bankdiag", type=Path)
    ref.add_argument("--out", type=Path)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("bankdiag", type=Path)
    cmp_.add_argument("run", type=Path)
    out_ = sub.add_parser("sheet")
    out_.add_argument("bankdiag", type=Path)
    out_.add_argument("run", type=Path)
    out_.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "sheet":
        text = sheet(args.bankdiag, args.run)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
            print(f"записано {args.out}")
        else:
            print(text)
        return 0
    if args.mode == "reference":
        expected, states = reference(args.bankdiag)
        out = args.out or args.bankdiag / EXPECTED_FILE
        write_expected(expected, out)
        findings = e02.Findings()
        for item in expected:
            if item["Сост"] in {"UNPARSED", "ERROR", "AMBIGUOUS"}:
                findings.error(f"PRIMARY_{item['Сост']}", "bank_rows.csv", f"{item['НомерДокумента']}|{item['НомерПроводки']}", item["Причина"])
        return e02.report(findings, {"bankdiag": str(args.bankdiag), "written": str(out), "states": dict(states)}, None)
    return e02.report(*compare(args.bankdiag, args.run), None)


if __name__ == "__main__":
    raise SystemExit(main())
