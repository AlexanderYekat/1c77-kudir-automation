"""Э0.5: reports/check_e05.py на синтетических каталогах запуска «Набор Э0.5».

Каталог Э0.4 (ledger62 с ПКО, выпиской, реализацией и ручной операцией; documents) дополняется снимками и
контролями так, как их пишет 1С: входящие остатки на 01.01.2026 (аванс по договору, второй договор того же
покупателя, остаток по пустому договору покупателя без движений), DAY_START на 01.01 и 01.04, BEFORE_DOCUMENT перед
ПКО, выпиской и ручной операцией, I квартал полный, II - текущий неполный. Значения вычисляются из движений
ledger62, поэтому верный набор сходится до копейки, а каждый дефект - снимок с самим документом, пропущенный ключ,
ноль вместо неизвестности, конец текущего квартала, расхождение общего контроля - даёт ERROR.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports"))
sys.path.insert(0, str(ROOT / "tests"))

import check_e02 as e02  # noqa: E402
import check_e05 as chk  # noqa: E402
import test_check_e03 as base3  # noqa: E402
import test_check_e04 as base4  # noqa: E402

RUN_ID = "KUDIR4_20261012_002"
HORIZON = "2026-04-15"
C1, C2, C3 = base3.C1, base3.C2, "C|00003"
D1, D2, D3 = base3.D1, base3.D2, "D|C|00001|0003"
# Входящие остатки на 01.01.2026 (S = Дт - Кт): аванс по договору 1, долг по второму договору покупателя 1, долг
# покупателя 3 по пустому договору; у покупателя 3 движений в 2026 году нет.
INCOMING = {(C1, D1): -250000, (C1, D3): 80000, (C3, ""): 100000}
CONTRACT_REF = {D3: '{"B","0","0","55","0","0","     9   "}'}


def collapsed(s: int) -> tuple[str, str]:
    return (str(s), "0") if s >= 0 else ("0", str(-s))


def ledger() -> list[dict]:
    return [dict(zip(chk.LEDGER_HEADER, r)) for r in base3.ledger_rows()]


def documents() -> list[dict]:
    return [dict(zip(chk.DOCUMENTS_HEADER, r)) for r in base4.documents_rows()]


def pairs() -> list[tuple[str, str]]:
    found = set(INCOMING)
    for row in ledger() + documents():
        if row["КонтрагентID"]:
            found.add((row["КонтрагентID"], row["ДоговорID"]))
    return sorted(found)


def balances(rows: list[dict]) -> Counter:
    result = Counter(INCOMING)
    for row in rows:
        change = chk.delta(row)
        if row["КонтрагентID"]:
            result[(row["КонтрагентID"], row["ДоговорID"])] += change
    return result


def snapshot(kind, date, doc, position, part, customer, contract, s, status="KNOWN"):
    state = "ALL" if part == "CUSTOMER" else ("VALUE" if contract else "EMPTY")
    dt, kt = collapsed(s) if status == "KNOWN" else ("", "")
    return [kind, date, doc, position, part, customer, contract, state, dt, kt, status]


def snapshot_rows() -> list[list[str]]:
    rows = []
    all_pairs = pairs()
    lines = ledger()
    for date in ("2026-01-01", "2026-04-01"):
        state = balances([r for r in lines if r["ДатаОперации"] < date])
        for customer in sorted({c for c, _ in all_pairs}):
            rows.append(snapshot("DAY_START", date, "", "", "CUSTOMER", customer, "", sum(v for (c, _), v in state.items() if c == customer)))
            for c, contract in all_pairs:
                if c == customer:
                    rows.append(snapshot("DAY_START", date, "", "", "CUSTOMER_CONTRACT", customer, contract, state[(c, contract)]))
    by_doc = chk.documents_in_order(lines)
    done: list[dict] = []
    for doc, doc_rows in by_doc.items():
        if chk.is_money_document(doc_rows):
            state = balances(done)
            date, position = doc_rows[0]["ДатаОперации"], doc_rows[0]["ПозицияДокумента"]
            touched = list(dict.fromkeys((r["КонтрагентID"], r["ДоговорID"]) for r in doc_rows))
            for customer in dict.fromkeys(c for c, _ in touched):
                rows.append(snapshot("BEFORE_DOCUMENT", date, doc, position, "CUSTOMER", customer, "", sum(v for (c, _), v in state.items() if c == customer)))
                own = [p for p in touched if p[0] == customer]
                others = [p for p in sorted(state) if p[0] == customer and p not in own and state[p]]
                for c, contract in own + others:
                    rows.append(snapshot("BEFORE_DOCUMENT", date, doc, position, "CUSTOMER_CONTRACT", c, contract, state[(c, contract)]))
        done += doc_rows
    return rows


def quarter_rows() -> list[list[str]]:
    lines = ledger()
    opening = sum(INCOMING.values())
    closing = opening + sum(chk.delta(r) for r in lines)
    return [
        ["2026-01-01", "2026-03-31", "2026-03-31", "1", *collapsed(opening), *collapsed(closing), "KNOWN", "REFRESHED", RUN_ID, str(len(lines))],
        ["2026-04-01", "2026-06-30", HORIZON, "0", *collapsed(closing), "", "", "UNKNOWN", "REFRESHED", RUN_ID, "0"],
    ]


def build(directory: Path, end: str = "PASS", status: str = "WRITING") -> None:
    base4.build(directory)
    for name in ("manifest.csv", "export_status.csv"):
        path = directory / name
        path.write_bytes(path.read_bytes().replace(base4.RUN_ID.encode(), RUN_ID.encode()).replace(b"2026-02-15", HORIZON.encode()))
    base3.write_csv(directory / "export_status.csv", ["key", "value"], [["run_id", RUN_ID], ["schema_version", "4"], ["status", status]])
    id_map = base4.id_map_rows() + [["CONTRACT", D3, "", "Договор 3", "2025-06-01", "", "", CONTRACT_REF[D3]]]
    base3.write_csv(directory / "id_map.csv", e02.ID_MAP_HEADER, id_map)
    diagnostics = base4.diagnostics_rows()
    diagnostics[0][-1] = "Диагностический набор Э0.5"
    diagnostics.append(["WARNING", "ledger_quarters.csv", "2026-04-01", "closing_debit_kopecks", "COVERAGE_INCOMPLETE", "текущий неполный квартал"])
    base3.write_csv(directory / "diagnostics.csv", e02.DIAG_HEADER, diagnostics)
    base3.write_csv(directory / chk.SNAPSHOTS_FILE, chk.SNAPSHOTS_HEADER, snapshot_rows())
    base3.write_csv(directory / chk.QUARTERS_FILE, chk.QUARTERS_HEADER, quarter_rows())
    log = f"2026-10-12 12:00:00 | INFO | START Э0.5 version=2026.10.12-01 run_id={RUN_ID} source_id=ctoksm-buh окно=2026-01-01..{HORIZON}\r\n" \
          f"2026-10-12 12:00:30 | INFO | Э0.5 контроль 2026-01-01 счёт 62.1: СНД=0 СНК=70000 СКД=0 СКК=1200000 (конец 2026-03-31) коп\r\n" \
          f"2026-10-12 12:01:00 | INFO | END Э0.5 итог={end} run_id={RUN_ID} export_status={status}\r\n"
    (directory / "1c_export.log").write_bytes(log.encode("cp1251"))


class CheckE05PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / RUN_ID
        build(self.run)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def errors(self) -> list[dict]:
        return [item for item in chk.check_package(self.run)[0].items if item["severity"] == "ERROR"]

    def edit(self, name: str, header: list[str], change) -> None:
        path = self.run / name
        records = e02.parse_csv(path.read_bytes(), name, e02.Findings())
        base3.write_csv(path, header, change([list(r) for r in records[1:]]))

    def edit_snapshots(self, change) -> None:
        self.edit(chk.SNAPSHOTS_FILE, chk.SNAPSHOTS_HEADER, change)

    def edit_quarter(self, number: int, **fields) -> None:
        def change(rows):
            for name, value in fields.items():
                rows[number][chk.QUARTERS_HEADER.index(name)] = value
            return rows
        self.edit(chk.QUARTERS_FILE, chk.QUARTERS_HEADER, change)

    def find(self, rows, **fields) -> int:
        return next(i for i, r in enumerate(rows) if all(r[chk.SNAPSHOTS_HEADER.index(k)] == v for k, v in fields.items()))

    def assertFails(self, needle: str) -> None:
        errors = self.errors()
        self.assertTrue(any(needle in item["message"] for item in errors), errors)

    def test_valid_set_passes(self) -> None:
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, [i for i in findings.items if i["severity"] == "ERROR"])
        self.assertEqual(summary["verdict"], "PASS")
        snaps = summary["balance_snapshots"]
        self.assertEqual(list(snaps["day_start"]), ["2026-01-01", "2026-04-01"])
        self.assertEqual(snaps["day_start"]["2026-01-01"]["customers"], 3)
        self.assertEqual(snaps["before_document"], {"documents": 3, "money_documents_in_ledger": 3, "rows": snaps["before_document"]["rows"]})
        self.assertEqual(snaps["incoming_only_pairs"], 2)
        rec = summary["reconciliation"]
        self.assertGreater(rec["day_start_chain"][0], 0)
        self.assertEqual(rec["day_start_chain"][1], 0)
        self.assertGreater(rec["before_document"][0], 0)
        self.assertEqual(rec["before_document"][1:], [0, 0])
        self.assertEqual(rec["customer_vs_contracts_day"][1], 0)
        self.assertEqual(summary["ledger_quarters"]["reconciled"],
                         {"opening=customers: ok": 2, "closing-opening=movements: ok": 1, "closing=next opening: ok": 1})

    def test_document_included_in_its_own_snapshot(self) -> None:
        # Остаток после выписки вместо остатка перед ней - граница взята не на предыдущей позиции.
        def change(rows):
            i = self.find(rows, BoundaryKind="BEFORE_DOCUMENT", BeforeDocumentID=f"DOC|{base3.K['B']}", Slice="CUSTOMER", КонтрагентID=C2)
            rows[i][8:10] = collapsed(-500000 + 200000)
            return rows
        self.edit_snapshots(change)
        self.assertFails("документ включён в снимок")

    def test_day_start_must_follow_movements(self) -> None:
        def change(rows):
            i = self.find(rows, BoundaryKind="DAY_START", BoundaryDate="2026-04-01", Slice="CUSTOMER_CONTRACT", КонтрагентID=C2)
            rows[i][8:10] = ["1", "0"]
            return rows
        self.edit_snapshots(change)
        self.assertFails("+ движения квартала")

    def test_customer_must_equal_sum_of_contracts(self) -> None:
        def change(rows):
            i = self.find(rows, BoundaryKind="DAY_START", BoundaryDate="2026-01-01", Slice="CUSTOMER", КонтрагентID=C1)
            rows[i][8:10] = ["0", "250000"]
            return rows
        self.edit_snapshots(change)
        self.assertFails("не равно сумме договорных")

    def test_pair_of_ledger_must_have_day_start(self) -> None:
        self.edit_snapshots(lambda rows: [r for r in rows if not (r[0] == "DAY_START" and r[6] == D2)])
        self.assertFails("без DAY_START")

    def test_same_key_set_at_every_boundary(self) -> None:
        self.edit_snapshots(lambda rows: [r for r in rows if not (r[0] == "DAY_START" and r[1] == "2026-04-01" and r[5] == C3)])
        self.assertFails("набор ключей отличается")

    def test_money_document_needs_snapshot(self) -> None:
        self.edit_snapshots(lambda rows: [r for r in rows if r[2] != f"DOC|{base3.K['M']}"])
        self.assertFails("нет снимков BEFORE_DOCUMENT перед денежным документом")

    def test_side_of_document_needs_snapshot(self) -> None:
        self.edit_snapshots(lambda rows: [r for r in rows if not (r[2] == f"DOC|{base3.K['B']}" and r[5] == C2 and r[4] == "CUSTOMER_CONTRACT")])
        self.assertFails("нет снимка перед")

    def test_noncash_document_has_no_snapshot(self) -> None:
        def change(rows):
            extra = list(rows[self.find(rows, BoundaryKind="BEFORE_DOCUMENT", Slice="CUSTOMER", КонтрагентID=C2)])
            extra[1:4] = ["2026-02-04", f"DOC|{base3.K['S']}", base3.POS["S"]]
            return rows + [extra]
        self.edit_snapshots(change)
        self.assertFails("без денежной корреспонденции")

    def test_unknown_is_not_zero(self) -> None:
        def change(rows):
            rows[self.find(rows, BoundaryKind="DAY_START", Slice="CUSTOMER", КонтрагентID=C3)][10] = "UNKNOWN"
            return rows
        self.edit_snapshots(change)
        self.assertFails("UNKNOWN с числами")

    def test_unknown_needs_snapshot_unknown(self) -> None:
        def change(rows):
            i = self.find(rows, BoundaryKind="DAY_START", BoundaryDate="2026-04-01", Slice="CUSTOMER", КонтрагентID=C3)
            rows[i][8:11] = ["", "", "UNKNOWN"]
            return rows
        self.edit_snapshots(change)
        self.assertFails("UNKNOWN без SNAPSHOT_UNKNOWN")
        # Неудавшийся запрос всей границы объясняет её строки; набор с ERROR - FAILED.
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [["ERROR", chk.SNAPSHOTS_FILE, chk.record_key("DAY_START", "2026-04-01"), "Status", "SNAPSHOT_UNKNOWN", "запрос не выполнен"]])
        self.assertFalse(any("UNKNOWN без SNAPSHOT_UNKNOWN" in e["message"] for e in self.errors()))
        # Объяснённая неизвестность обязательного снимка сохраняется для разбора, но приёмку не проходит (№1 §27).
        self.assertFails("приёмку полноты снимков набор не проходит")

    def diagnose(self, *rows: list[str]) -> None:
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda existing: existing + [list(r) for r in rows])

    def test_unknown_explained_by_warning_is_not_pass(self) -> None:
        # Все снимки UNKNOWN, причины - WARNING SNAPSHOT_UNKNOWN: ноль сверок не даёт PASS (замечание P2 к -02).
        def change(rows):
            for r in rows:
                r[8:11] = ["", "", "UNKNOWN"]
            return rows
        self.edit_snapshots(change)
        boundaries = {(r[0], r[1]) for r in [list(x) for x in e02.parse_csv((self.run / chk.SNAPSHOTS_FILE).read_bytes(), "s", e02.Findings())[1:]]}
        self.diagnose(*[["WARNING", chk.SNAPSHOTS_FILE, chk.record_key(kind, date), "Status", "SNAPSHOT_UNKNOWN", "запрос не выполнен"] for kind, date in sorted(boundaries)])
        findings, summary = chk.check_package(self.run)
        self.assertEqual(summary["verdict"], "FAIL")
        messages = [i["message"] for i in findings.items if i["severity"] == "ERROR"]
        self.assertTrue(any("уровня ERROR (№1 §27), в diagnostics только WARNING" in m for m in messages), messages)
        self.assertTrue(any("приёмку полноты снимков набор не проходит" in m for m in messages), messages)
        self.assertEqual(summary["reconciliation"]["customer_vs_contracts_day"][0], 0)

    def test_declared_customer_mismatch_is_unconfirmed(self) -> None:
        # 1С сохранила полученные остатки и объявила WARNING SNAPSHOT_MISMATCH: сверка видна и не подтверждена, а не
        # успешна; необъявленное расхождение - ERROR.
        def change(rows):
            rows[self.find(rows, BoundaryKind="DAY_START", BoundaryDate="2026-01-01", Slice="CUSTOMER", КонтрагентID=C1)][8:10] = ["0", "250000"]
            return rows
        self.edit_snapshots(change)
        self.assertFails("выгрузка расхождение не объявила")
        label = chk.record_key("DAY_START", "2026-01-01", "", "CUSTOMER", C1, "ALL", "")
        self.diagnose(["WARNING", chk.SNAPSHOTS_FILE, label, "ОстатокДтКоп", "SNAPSHOT_MISMATCH", "сальдо покупателя не равно сумме договоров"])
        findings, summary = chk.check_package(self.run)
        self.assertFalse(any("не равно сумме договорных" in i["message"] for i in findings.items if i["severity"] == "ERROR"))
        self.assertTrue(any(i["severity"] == "WARNING" and i["key"] == label and "не подтверждена" in i["message"] for i in findings.items))
        self.assertEqual(summary["reconciliation"]["customer_vs_contracts_day"][2], 1)

    def test_pair_of_unread_contract_is_unknown(self) -> None:
        # -03: пары нет в результате, а договор покупателя не прочитан - UNKNOWN с ANALYTICS_UNKNOWN рядом со строкой
        # ContractState=UNKNOWN; это не неизвестный обязательный запрос, итог покупателя остаётся, сверка не подтверждена.
        pair = chk.record_key("DAY_START", "2026-04-01", "", "CUSTOMER_CONTRACT", C1, "VALUE", D3)
        unread = chk.record_key("DAY_START", "2026-04-01", "", "CUSTOMER_CONTRACT", C1, "UNKNOWN", "")

        def change(rows):
            rows[self.find(rows, BoundaryKind="DAY_START", BoundaryDate="2026-04-01", Slice="CUSTOMER_CONTRACT", ДоговорID=D3)][8:11] = ["", "", "UNKNOWN"]
            return rows
        self.edit_snapshots(change)
        self.diagnose(["WARNING", chk.SNAPSHOTS_FILE, pair, "Status", "ANALYTICS_UNKNOWN", "договор покупателя не прочитан"])
        self.assertFails("без строки непрочитанного договора")
        self.edit_snapshots(lambda rows: rows + [["DAY_START", "2026-04-01", "", "", "CUSTOMER_CONTRACT", C1, "", "UNKNOWN", "", "", "UNKNOWN"]])
        self.diagnose(["WARNING", chk.SNAPSHOTS_FILE, unread, "ДоговорID", "ANALYTICS_UNKNOWN", "договор с остатком не прочитан"])
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, [i for i in findings.items if i["severity"] == "ERROR"])
        self.assertEqual(summary["balance_snapshots"]["unread_contract_pairs"], 1)
        self.assertEqual(summary["balance_snapshots"]["unknown_required_rows"], 0)
        self.assertEqual(summary["reconciliation"]["customer_vs_contracts_day"][2], 1)
        self.assertTrue(any(i["severity"] == "WARNING" and i["code"] == "ANALYTICS_UNKNOWN" for i in findings.items))

    def test_duplicate_key_is_error(self) -> None:
        self.edit_snapshots(lambda rows: rows + [rows[0]])
        self.assertFails("повтор ключа снимка")

    def test_day_start_only_at_quarter_starts(self) -> None:
        def change(rows):
            moved = [list(r) for r in rows if r[0] == "DAY_START" and r[1] == "2026-04-01"]
            for r in moved:
                r[1] = "2026-03-31"
            return [r for r in rows if not (r[0] == "DAY_START" and r[1] == "2026-04-01")] + moved
        self.edit_snapshots(change)
        self.assertFails("ожидались начала кварталов охвата")

    def test_current_quarter_has_no_closing(self) -> None:
        self.edit_quarter(1, closing_debit_kopecks="0", closing_credit_kopecks="0", control_status="KNOWN")
        self.assertFails("текущий неполный квартал")

    def test_current_quarter_needs_warning(self) -> None:
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: [r for r in rows if r[1] != chk.QUARTERS_FILE])
        self.assertFails("нет WARNING COVERAGE_INCOMPLETE")

    def test_full_quarter_flag(self) -> None:
        self.edit_quarter(0, full_quarter="0")
        self.assertFails("full_quarter 0 у полного квартала")

    def test_general_opening_equals_customers(self) -> None:
        self.edit_quarter(0, opening_debit_kopecks="1")
        self.assertFails("не равен сумме покупателей")

    def test_closing_equals_movements_and_next_opening(self) -> None:
        self.edit_quarter(0, closing_debit_kopecks="0", closing_credit_kopecks="1")
        errors = self.errors()
        self.assertTrue(any("движения ledger62 квартала" in e["message"] for e in errors), errors)
        self.assertTrue(any("не равны начальным следующего квартала" in e["message"] for e in errors), errors)

    def test_row_count_and_origin(self) -> None:
        self.edit_quarter(0, ledger_row_count="11", ledger_source_run_id="KUDIR4_20261001_001")
        errors = self.errors()
        self.assertTrue(any("ledger_row_count" in e["message"] for e in errors), errors)
        self.assertTrue(any("REFRESHED" in e["message"] for e in errors), errors)

    def test_window_starts_at_history_start(self) -> None:
        log = self.run / "1c_export.log"
        log.write_bytes(log.read_bytes().replace("окно=2026-01-01".encode("cp1251"), "окно=2026-02-01".encode("cp1251")))
        self.assertFails("начало - history_start")

    def test_contract_of_snapshot_resolves_in_id_map(self) -> None:
        self.edit("id_map.csv", e02.ID_MAP_HEADER, lambda rows: [r for r in rows if r[1] != D3])
        self.assertFails("ДоговорID не разрешается в id_map")

    def test_without_before_snapshots_is_not_pass(self) -> None:
        self.edit_snapshots(lambda rows: [r for r in rows if r[0] == "DAY_START"])
        findings, summary = chk.check_package(self.run)
        self.assertNotEqual(summary["verdict"], "PASS")

    def test_compare_identical_and_changed(self) -> None:
        other = self.tmp / "KUDIR4_20261012_003"
        build(other)
        path = other / chk.QUARTERS_FILE
        path.write_bytes(path.read_bytes().replace(RUN_ID.encode(), b"KUDIR4_20261012_003"))
        findings, _ = chk.compare_packages(self.run, other)
        self.assertEqual(findings.errors, 0, findings.items)
        def change(rows):
            rows[0][8:10] = ["1", "0"]
            return rows
        self.edit_snapshots(change)
        findings, summary = chk.compare_packages(self.run, other)
        self.assertGreater(findings.errors, 0)
        self.assertEqual(summary[chk.SNAPSHOTS_FILE]["changed_fields"], {"ОстатокДтКоп": 1, "ОстатокКтКоп": 1})

    def test_sheet_lists_sections(self) -> None:
        text = chk.sheet(self.run)
        for part in ("## 1. Общие квартальные контроли", "## 2. Остатки на начало кварталов", "## 3. Остатки перед денежными документами",
                     "## 4. Автоматические сверки", "входящий остаток по нескольким договорам", "приходный кассовый ордер",
                     "возврат денег покупателю", "ручная операция", "| 2026-01-01..2026-03-31 | 62.1 |", "Итог проверки: **PASS**"):
            self.assertIn(part, text)


class CheckE05RuleTests(unittest.TestCase):
    def test_quarters(self) -> None:
        self.assertEqual(chk.quarter_starts("2026-10-10"), ["2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01"])
        self.assertEqual(chk.quarter_starts("2027-01-31"), ["2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01", "2027-01-01"])
        self.assertEqual(chk.quarter_starts("2026-09-30")[-1], "2026-07-01")
        self.assertEqual(chk.quarter_end("2026-04-01"), "2026-06-30")
        self.assertEqual(chk.quarter_end("2026-10-01"), "2026-12-31")
        self.assertEqual(chk.quarter_start("2026-08-31"), "2026-07-01")

    def test_record_key_like_1c(self) -> None:
        # КУДиР4_КлючЗаписиСнимка: JSON-массив без пробелов, кавычки K экранированы обратной косой чертой.
        k = base3.K["P"]
        self.assertEqual(chk.record_key("BEFORE_DOCUMENT", "2026-02-02", f"DOC|{k}", "CUSTOMER", C1, "ALL", ""),
                         '["BEFORE_DOCUMENT","2026-02-02","DOC|{\\"O\\",\\"0\\",\\"0\\",\\"0\\",\\"0\\",\\"0\\",\\"       276    461700   \\"}","CUSTOMER","C|00001","ALL",""]')

    def test_signed_unknown_is_none(self) -> None:
        row = dict(zip(chk.SNAPSHOTS_HEADER, snapshot("DAY_START", "2026-01-01", "", "", "CUSTOMER", C1, "", 0, "UNKNOWN")))
        self.assertIsNone(chk.signed(row))
        row = dict(zip(chk.SNAPSHOTS_HEADER, snapshot("DAY_START", "2026-01-01", "", "", "CUSTOMER", C1, "", -5)))
        self.assertEqual(chk.signed(row), -5)


if __name__ == "__main__":
    unittest.main()
