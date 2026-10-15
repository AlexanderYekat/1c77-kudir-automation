"""Final acceptance of E0 of plan No. 13 (E0.7): the file checks of №1 §28 on one published FULL package and one list of
control examples for the owner's check in 1C.

    python reports/check_e07.py files exchange/KUDIR4_<run> [--accepted exchange/KUDIR4_<run> ...] [--out files.md] [--json out.json]
    python reports/check_e07.py sheet exchange/KUDIR4_<run> [--bankdiag exchange/BANKDIAG_20261011_001] --out exchange/sverka-<run>-e07.md

files - check_e06 package (form, publication and every content check with zero tolerance) and check_e06 compare with each
        accepted package, laid out by the seven groups of №1 §28 with the numbers behind each group: profile and files;
        keys, payment forms, positions, posting numbers and references; quarters; movements and money; snapshots and
        general controls; documents, VAT and diagnostics; comparison of packages. Then what the package leaves unknown
        (diagnostics by severity, code and file; UNKNOWN fields of the tables) and the size, records and sha256 of every
        file. This is a check of the files: completeness against the base is shown by check_dbf_e04, check_dbf_e05 and
        the standard 1C reports (run_1c_sheet).
sheet - the control examples for the owner, chosen inside the full package with no customer or movement filtered out:
        quarterly controls and snapshots (check_e05 sheet), movements (check_e03 sheet), documents (check_e04 sheet) and,
        with --bankdiag, the primary of bank payments (check_bank_primary sheet). The sheet names customers, so inside
        the working folder it is written only under exchange/, which is not in git.

Exit codes: 0 - the package passes and every comparison has no differences; 1 - otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import check_bank_primary as bank  # noqa: E402
import check_e02 as e02  # noqa: E402
import check_e03 as e03  # noqa: E402
import check_e04 as e04  # noqa: E402
import check_e05 as e05  # noqa: E402
import check_e06 as e06  # noqa: E402
from check_e02 import Findings, read_table  # noqa: E402
from check_e03 import LEDGER_FILE, LEDGER_HEADER, rub  # noqa: E402
from check_e04 import DOCUMENTS_FILE, DOCUMENTS_HEADER  # noqa: E402
from check_e05 import QUARTERS_FILE, QUARTERS_HEADER, SNAPSHOT_KEY, SNAPSHOTS_FILE, SNAPSHOTS_HEADER  # noqa: E402

# №1 §28: the group of each finding by the file it names; ID_COLLISION is a key problem wherever it is found.
GROUPS = (
    ("1", "Профиль, состав файлов, CSV, завершённость", {"manifest.csv", "export_status.csv", "PACKAGE", e06.LOG_FILE, e06.SELFTEST_FILE}),
    ("2", "Уникальность ключей, формы оплат, позиции, номера проводок, ссылки", {"id_map.csv"}),
    ("3", "Кварталы охвата", {QUARTERS_FILE}),
    ("4", "Движения, денежные роли, тексты", {LEDGER_FILE}),
    ("5", "Снимки и общие контроли", {SNAPSHOTS_FILE}),
    ("6", "Документы, НДС, диагностика", {DOCUMENTS_FILE, "diagnostics.csv"}),
    ("7", "Сравнение сохранённых пакетов", set()),
)
POSTING_NUMBER = re.compile(r"[1-9]\d*")
QUARTER_NAMES = {"01": "I", "04": "II", "07": "III", "10": "IV"}


def group_of(item: dict) -> str:
    if item["code"] == "ID_COLLISION":
        return "2"
    return next((number for number, _, files in GROUPS if item["file"] in files), "1")


def table_facts(directory: Path) -> dict:
    """Numbers of №1 §28 items 2-6 counted straight from the files, beside the checks of check_e06."""
    local = Findings()
    ledger = read_table(directory, LEDGER_FILE, LEDGER_HEADER, local) or []
    documents = read_table(directory, DOCUMENTS_FILE, DOCUMENTS_HEADER, local) or []
    snapshots = read_table(directory, SNAPSHOTS_FILE, SNAPSHOTS_HEADER, local) or []
    quarters = read_table(directory, QUARTERS_FILE, QUARTERS_HEADER, local) or []
    id_map = read_table(directory, "id_map.csv", e02.ID_MAP_HEADER, local) or []
    diagnostics = read_table(directory, "diagnostics.csv", e02.DIAG_HEADER, local) or []

    def keys(rows: list[dict], fields: tuple[str, ...]) -> dict:
        found = [tuple(row[f] for f in fields) for row in rows]
        return {"records": len(found), "duplicates": len(found) - len(set(found))}

    payment_rows = [row["payment_id"] for row in ledger if row["payment_id"]]
    positions = ({row["ПозицияДокумента"] for row in ledger} | {row["ПозицияДокумента"] for row in documents}
                 | {row["ПозицияДокумента"] for row in snapshots if row["ПозицияДокумента"]} | {row["позиция"] for row in id_map if row["позиция"]})
    map_ids = {row["id"] for row in id_map}
    references = [row[f] for row in ledger for f in ("source_document_id", "payment_id", "ДоговорID") if row[f]]
    references += [row[f] for row in documents for f in ("document_id", "ДоговорID") if row[f]]
    references += [row[f] for row in snapshots for f in ("BeforeDocumentID", "ДоговорID") if row[f]]
    posting_numbers = [row["НомерПроводкиВДокументе"] for row in ledger]
    posting_numbers += [row["номер_проводки"] for row in id_map if row["entity_type"] == "PAYMENT" and not row["id"].startswith("CASH|")]
    return {
        "read_problems": len(local.items),
        "records": {LEDGER_FILE: len(ledger), DOCUMENTS_FILE: len(documents), SNAPSHOTS_FILE: len(snapshots), QUARTERS_FILE: len(quarters),
                    "id_map.csv": len(id_map), "diagnostics.csv": len(diagnostics)},
        "keys": {LEDGER_FILE: keys(ledger, ("ledger_event_id",)), DOCUMENTS_FILE: keys(documents, ("document_id",)),
                 SNAPSHOTS_FILE: keys(snapshots, SNAPSHOT_KEY), QUARTERS_FILE: keys(quarters, ("quarter_start",)),
                 "id_map.csv": keys(id_map, ("entity_type", "id"))},
        "payments": {"rows": dict(Counter(p.split("|")[0] for p in payment_rows)),
                     "distinct": dict(Counter(p.split("|")[0] for p in set(payment_rows)))},
        "positions": {"distinct": len(positions), "lengths": dict(Counter(len(p) for p in positions)),
                      "trailing_spaces": dict(Counter(len(p) - len(p.rstrip(" ")) for p in positions))},
        "posting_numbers": {"values": len(posting_numbers), "not_positive_integers": sum(1 for n in posting_numbers if not POSTING_NUMBER.fullmatch(n))},
        "references": {"values": len(references), "resolved": sum(1 for r in references if r in map_ids)},
        "unknown": {
            "MovementRole UNKNOWN": sum(1 for row in ledger if row["MovementRole"] == "UNKNOWN"),
            "VATCompositionStatus UNKNOWN": sum(1 for row in documents if row["VATCompositionStatus"] == "UNKNOWN"),
            "снимки Status UNKNOWN": sum(1 for row in snapshots if row["Status"] == "UNKNOWN"),
            "снимки ContractState UNKNOWN": sum(1 for row in snapshots if row["ContractState"] == "UNKNOWN"),
            "кварталы control_status UNKNOWN": sum(1 for row in quarters if row["control_status"] == "UNKNOWN"),
            "BANK-оплаты без номера первички": len({row["payment_id"] for row in ledger if row["PaymentKind"] == "BANK" and not row["НомерДокументаОплаты"]}),
            "BANK-оплаты без даты первички": len({row["payment_id"] for row in ledger if row["PaymentKind"] == "BANK" and not row["ДатаДокументаОплаты"]}),
        },
        "diagnostics": sorted(Counter((row["severity"], row["code"], row["file"]) for row in diagnostics).items()),
        "quarters": quarters,
        "ledger_rows_by_quarter_sum": sum(int(row["ledger_row_count"]) for row in quarters if row["ledger_row_count"].isdigit()),
    }


def check_files(directory: Path, accepted: list[Path]) -> tuple[Findings, dict]:
    findings, summary = e06.check_package(directory)
    facts = table_facts(directory)
    comparisons = []
    for other in accepted:
        found, compared = e06.compare_packages(other, directory)
        differences = [item for item in found.items if item["severity"] == "ERROR"]
        comparisons.append({"accepted": other.name, "differences": len(differences), "first": differences[:5], "manifest": compared.get("manifest")})
        for item in differences:
            findings.add("ERROR", "COMPARE", item["file"], item["key"], f"сравнение с {other.name}: {item['message']}")
    start = e02.run_start(e02.log_of(directory))
    summary.update({"facts": facts, "comparisons": comparisons, "module_version": start[0] if start else None})
    groups = {number: {"errors": 0, "warnings": 0} for number, _, _ in GROUPS}
    for item in findings.items:
        number = "7" if item["code"] == "COMPARE" else group_of(item)
        if item["severity"] == "ERROR":
            groups[number]["errors"] += 1
        elif item["severity"] == "WARNING":
            groups[number]["warnings"] += 1
    summary["groups"] = groups
    summary["package_verdict"] = summary.get("verdict")
    summary["verdict"] = "PASS" if summary["package_verdict"] == "PASS" and not findings.errors else "FAIL"
    return findings, summary


def counts(values: dict) -> str:
    return ", ".join(f"{key} {value}" for key, value in values.items()) or "нет"


def evidence(summary: dict) -> dict[str, str]:
    facts, ledger, documents = summary["facts"], summary.get("ledger62", {}), summary.get("documents", {})
    snapshots, rec, quarters = summary.get("balance_snapshots", {}), summary.get("reconciliation", {}), summary.get("ledger_quarters", {})
    records, keys = facts["records"], facts["keys"]
    money = ledger.get("money_kopecks", {})
    roles = ledger.get("roles", {})
    transfers = ledger.get("transfers_62_62", {})
    vs = summary.get("documents_vs_ledger", {})
    day = snapshots.get("day_start", {})
    before = snapshots.get("before_document", {})
    quarter_text = "; ".join(
        f"{QUARTER_NAMES.get(start[5:7], start)} {start}..{q['covered_through']} full_quarter={q['full_quarter']} {q['control_status']} {q['ledger_action']} строк {q['ledger_row_count']}"
        for start, q in quarters.get("quarters", {}).items())
    return {
        "1": (f"export_status {summary.get('status')}, coverage_status {summary.get('coverage_status')}, итог журнала {summary.get('log_result')}; "
              f"source_id {summary.get('source_id')}, source_read_mode {summary.get('source_read_mode')}, охват {e05.HISTORY_START}..{summary.get('match_horizon_end')}; "
              f"восемь файлов №1 §5 и технические {sorted(e06.TECHNICAL_FILES)}, посторонних и *.tmp нет; "
              "строгое чтение: cp1251 без BOM, «;», удвоенные кавычки, записи CRLF, точные заголовки и число полей; "
              f"записей: {counts(records)}; самопроверка CSV — {summary.get('selftest_cases')} случаев"),
        "2": ("ключи (записей / повторов): " + "; ".join(f"{name} {k['records']}/{k['duplicates']}" for name, k in keys.items())
              + f"; оплаты (разных / строк): " + ", ".join(f"{kind} {facts['payments']['distinct'].get(kind, 0)}/{facts['payments']['rows'].get(kind, 0)}"
                                                       for kind in ("CASH", "BANK", "MANUAL"))
              + f"; позиции: разных {facts['positions']['distinct']}, длины {counts(facts['positions']['lengths'])}, хвостовых пробелов {counts(facts['positions']['trailing_spaces'])}"
              + f"; номера проводок: {facts['posting_numbers']['values']}, не положительных целых {facts['posting_numbers']['not_positive_integers']}"
              + f"; ссылки в id_map: {facts['references']['resolved']} из {facts['references']['values']}; id_map {counts(summary.get('id_map', {}))}"),
        "3": (f"{quarter_text}; кварталы подряд с {e05.HISTORY_START}, сумма ledger_row_count {facts['ledger_rows_by_quarter_sum']} = строк ledger62 {records[LEDGER_FILE]}; "
              f"сверки контролей: {counts(quarters.get('reconciled', {}))}"),
        "4": ("роли (строк, сумма): " + ", ".join(f"{role} {v['rows']} ({rub(v['kopecks'])})" for role, v in roles.items())
              + f"; деньги: поступило {rub(money.get('cash_in', 0))}, возвращено {rub(money.get('cash_out', 0))}, нетто {rub(money.get('net', 0))}"
              + f"; 62 → 62: проводок {transfers.get('postings', 0)} (две стороны NONCASH, между покупателями {transfers.get('between_customers', 0)}); сложных проводок {ledger.get('complex_postings', 0)}"
              + f"; тексты с краевыми пробелами {ledger.get('texts_with_edge_spaces', 0)}, многострочные {ledger.get('multi_line_texts', 0)}"),
        "5": (f"строк {snapshots.get('rows', 0)}; DAY_START " + ", ".join(f"{d}: {v['customers']} + {v['contract_rows']}" for d, v in day.items())
              + f"; BEFORE_DOCUMENT: документов {before.get('documents', 0)} из {before.get('money_documents_in_ledger', 0)} денежных, строк {before.get('rows', 0)}"
              + f"; неизвестных обязательных {snapshots.get('unknown_required_rows', 0)}"
              + "; сверки с допуском 0 (проверок / расхождений): " + ", ".join(f"{name} {'/'.join(str(x) for x in v)}" for name, v in rec.items())),
        "6": ("документы: " + "; ".join(f"{kind} {v.get('rows', 0)} ({', '.join(f'{k} {n}' for k, n in v.items() if k != 'rows')})" for kind, v in documents.get("by_kind", {}).items())
              + f"; с ledger62: {counts(vs)}; ставки по агрегатам {counts(documents.get('implied_vat_rates', {}))}"
              + f"; diagnostics: {counts(summary.get('diagnostics', {}))}"),
        "7": ("; ".join(f"с {c['accepted']}: различий {c['differences']}" for c in summary["comparisons"]) or "сравнение не выполнялось")
             + "; все кварталы REFRESHED этим запуском (FULL). REUSED/REFRESHED из кеша — Э9, здесь не проверяется",
    }


def render(directory: Path, findings: Findings, summary: dict) -> str:
    facts = summary["facts"]
    found = evidence(summary)
    lines = [
        f"# Проверка файлов по №1 §28 — {directory.name}", "",
        f"Версия модуля {summary.get('module_version')}; `check_e06 package` — {summary['package_verdict']}, "
        f"итог с учётом сравнений — **{summary['verdict']}** (ошибок {findings.errors}). Файлы доказывают, что записала 1С, а не полноту относительно базы.", "",
        "| № | Группа §28 | Что показывают файлы | Итог |", "|---|---|---|---|",
    ]
    for number, title, _ in GROUPS:
        group = summary["groups"][number]
        verdict = "FAIL" if group["errors"] else ("PASS, WARNING " + str(group["warnings"]) if group["warnings"] else "PASS")
        lines.append(f"| {number} | {title} | {found[number]} | {verdict} |")
    if findings.errors:
        lines += ["", "## Ошибки", ""]
        lines += [f"- {item['file']} `{item['key']}` {item['code']}: {item['message']}" for item in findings.items if item["severity"] == "ERROR"][:50]
    lines += ["", "## Что пакет оставляет неизвестным", "", "| Уровень | Код | Файл | Записей |", "|---|---|---|---:|"]
    lines += [f"| {severity} | {code} | {name} | {count} |" for (severity, code, name), count in facts["diagnostics"]]
    lines += ["", "Неизвестные значения в таблицах: " + counts(facts["unknown"]) + ".", "",
              "## Файлы пакета", "", "| Файл | Записей | sha256 |", "|---|---:|---|"]
    for name, digest in summary.get("files_sha256", {}).items():
        lines.append(f"| {name} | {facts['records'].get(name, '')} | `{digest}` |")
    return "\n".join(lines) + "\n"


def sheet(directory: Path, bankdiag: Path | None) -> str:
    # The package check of section 4 is the one of the FULL package: the E0.5 check refuses a published package.
    parts = [("А", "Квартальные контроли и снимки", e05.sheet(directory, e06.check_package, "check_e06 package")),
             ("Б", "Движения по 62.1/62.2", e03.sheet(directory)),
             ("В", "Документы: счета, реализации, акты, НДС", e04.sheet(directory))]
    if bankdiag:
        parts.append(("Г", "Первичка банковских оплат", bank.sheet(bankdiag, directory)))
    local = Findings()
    manifest = e02.read_key_value(directory, "manifest.csv", e02.MANIFEST_KEYS, local) or {}
    start = e02.run_start(e02.log_of(directory))
    lines = [
        f"# Контрольные примеры КП0.7 — {directory.name}", "",
        f"Итоговый пакет FULL: источник `{manifest.get('source_id', '')}`, охват {manifest.get('history_start', '')}..{manifest.get('match_horizon_end', '')}, "
        f"версия модуля `{start[0] if start else '?'}`. Примеры выбраны внутри полного пакета: остальные покупатели и движения не отфильтрованы.",
        "Сверка выборочная. Значения в 1С сравнивайте с ведомостью до копейки; в каждом пункте отметьте «да» или впишите, что показала 1С.",
        "Ведомость содержит наименования покупателей — не копировать в git.", "",
        "Состав: " + "; ".join(f"часть {letter} — {title}" for letter, title, _ in parts) + ".", "",
    ]
    for letter, title, text in parts:
        body = text.split("\n", 1)[1] if "\n" in text else ""
        lines += [f"## Часть {letter}. {title}", "", re.sub(r"^(#{1,5}) ", r"#\1 ", body, flags=re.M).strip("\n"), ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    one = sub.add_parser("files")
    one.add_argument("directory", type=Path)
    one.add_argument("--accepted", type=Path, action="append", default=[], help="an accepted package of the same area to compare with")
    one.add_argument("--out", type=Path)
    one.add_argument("--json", type=Path)
    two = sub.add_parser("sheet")
    two.add_argument("directory", type=Path)
    two.add_argument("--bankdiag", type=Path)
    two.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "sheet":
        out = args.out.resolve()
        if ROOT in out.parents and ROOT / "exchange" not in out.parents:
            parser.error("ведомость с наименованиями покупателей пишется только в exchange/ (не в git)")
        out.write_text(sheet(args.directory, args.bankdiag), encoding="utf-8")
        print(f"записано {args.out}")
        return 0
    findings, summary = check_files(args.directory, args.accepted)
    text = render(args.directory, findings, summary)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "errors": findings.errors, "findings": findings.items}, ensure_ascii=False, indent=1, default=str) + "\n",
                             encoding="utf-8")
    print(text)
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
