"""Э0.3: reports/check_e03.py на синтетических каталогах запуска «Набор Э0.3».

Каталог собирается так, как его пишет 1С (cp1251, CR LF, кавычки по №1 §30). Состав повторяет случаи окна
КП0.2: ПКО с переносом внутри документа, выписка с несколькими оплатами и возвратом Дт 62.2 Кт 51, зачёт
аванса Дт 62.2 Кт 62.1 в реализации, ручная операция с отрицательной суммой. Проверяется, что верный набор
проходит, а неверная роль, payment_id у неденежной строки, потерянная сторона 62 -> 62, недиагностированная
первичка, подмена первички выпиской, нарушение порядка и прочие дефекты дают ERROR.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports"))

import check_e02 as e02  # noqa: E402
import check_e03 as chk  # noqa: E402

RUN_ID = "KUDIR4_20261010_005"
RAW = {
    "P": '{"O","0","0","0","0","0","       276    461700   "}',
    "B": '{"O","0","0","0","0","0","       238    461|%8   "}',
    "S": '{"O","0","0","0","0","0","       294    461939   "}',
    "M": '{"O","0","0","0","0","0","       300    462000   "}',
}
K = {name: raw.replace("%", "%25").replace("|", "%7C") for name, raw in RAW.items()}
POS = {
    "P": "#20260202 100000000    461700   ",
    "B": "#20260203 100000000    461718   ",
    "S": "#20260204 100000000    461939   ",
    "M": "#20260205 100000000    462000   ",
}
DOCS = {
    "P": ("ПриходныйОрдер", "000035", "2026-02-02", "Принято от Ромашки"),
    "B": ("Выписка", "0012", "2026-02-03", ""),
    "S": ("РасходнаяНакладная", "Н00166", "2026-02-04", "Введен на основании: Счет на оплату № 00087 от 14 января 2026 г."),
    "M": ("Операция", "0005", "2026-02-05", "ручная"),
}
C1, C2 = "C|00001", "C|00002"
D1, D2 = "D|C|00001|0001", "D|C|00002|0002"
CONTRACT_NAMES = {D1: ("Договор 1", "2026-01-10"), D2: ("Договор 2", "2026-01-11")}
TEXT = "Оплата по счёту 00250 (15.01.26)                                          "


def field(value: str) -> str:
    value = str(value)
    if any(c in value for c in ';"\r\n') or (value and (value[0] in " \t" or value[-1] in " \t")):
        return '"' + value.replace('"', '""') + '"'
    return value


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    text = "".join(";".join(field(v) for v in record) + "\r\n" for record in [header, *rows])
    path.write_bytes(text.encode("cp1251"))


def row(doc, posting, n, dt, kt, side, amount, customer, contract, vr, role, kind=""):
    """One ledger62 line as КУДиР4_СторонаДвижения writes it; payment fields filled for CASH_IN/CASH_OUT."""
    vid, number, date, comment = DOCS[doc]
    k = K[doc]
    account = dt if side == "D" else kt
    name, cdate = CONTRACT_NAMES.get(contract, ("", ""))
    payment = granularity = pay_kind = pay_number = pay_date = purpose = source = pay_comment = ""
    if role in {"CASH_IN", "CASH_OUT"}:
        payment = f"CASH|{k}" if kind == "CASH" else f"{kind}|{k}|{posting}"
        granularity = "DOCUMENT" if kind == "CASH" else "POSTING"
        if kind == "CASH":
            pay_kind, pay_number, pay_date = vid, number, date
        purpose, source, pay_comment = TEXT, "POSTING_CONTENT", comment
    values = {
        "ledger_event_id": f"LED|{k}|{posting}|{n}", "source_document_id": f"DOC|{k}", "payment_id": payment,
        "ДатаОперации": date, "ПозицияДокумента": POS[doc], "НомерСтрокиВДокументе": "",
        "НомерПроводкиВДокументе": str(posting), "ВидДокумента": vid, "НомерДокумента": number, "ДатаДокумента": date,
        "СуммаКоп": str(amount), "СчетДебета": dt, "СчетКредита": kt, "Счет62": account, "Сторона62": side,
        "КонтрагентID": customer, "Контрагент": "Ромашка" if customer == C1 else "Лютик", "ДоговорID": contract,
        "Договор": name, "НомерДоговора": name, "ДатаДоговора": cdate, "ВидРасчетовID": vr,
        "ВидРасчетовСПокупателем": vr[3:], "СодержаниеПроводки": TEXT, "Содержание": "  содержание  ",
        "ПредставлениеПроводки": f"{dt} {kt} {amount}", "КомментарийДокумента": comment, "ПервичныйДокумент": "",
        "MovementRole": role, "PaymentKind": kind if payment else "", "PaymentGranularity": granularity,
        "ВидДокументаОплаты": pay_kind, "НомерДокументаОплаты": pay_number, "ДатаДокументаОплаты": pay_date,
        "НазначениеПлатежа": purpose, "ИсточникНазначения": source, "КомментарийДокументаОплаты": pay_comment,
    }
    return [values[name] for name in chk.LEDGER_HEADER]


def ledger_rows() -> list[list[str]]:
    vr = "VR|ПокупателиОтгрузка"
    return [
        row("P", 1, 1, "50.1", "62.1", "K", 600000, C1, D1, vr, "CASH_IN", "CASH"),
        row("P", 2, 1, "50.1", "62.2", "K", 400000, C1, D1, "", "CASH_IN", "CASH"),
        row("P", 3, 1, "62.2", "62.1", "D", 300000, C1, D1, "", "NONCASH"),
        row("P", 3, 2, "62.2", "62.1", "K", 300000, C1, D1, vr, "NONCASH"),
        row("B", 2, 1, "51", "62.1", "K", 1000000, C1, D1, vr, "CASH_IN", "BANK"),
        row("B", 10, 1, "51", "62.1", "K", 500000, C2, D2, vr, "CASH_IN", "BANK"),
        row("B", 11, 1, "62.2", "51", "D", 200000, C2, D2, "", "CASH_OUT", "BANK"),
        row("S", 1, 1, "62.1", "90.1.1", "D", 1650000, C2, D2, vr, "NONCASH"),
        row("S", 2, 1, "62.2", "62.1", "D", 1650000, C2, D2, "", "NONCASH"),
        row("S", 2, 2, "62.2", "62.1", "K", 1650000, C2, D2, vr, "NONCASH"),
        row("M", 7, 1, "51", "62.1", "K", -50000, C1, D1, vr, "UNKNOWN"),
        # Ручная операция: денежная проводка спорная, оплата MANUAL не назначается (решение владельца 10.10.2026).
        row("M", 8, 1, "51", "62.1", "K", 70000, C1, D1, vr, "UNKNOWN"),
    ]


def id_map_rows() -> list[list[str]]:
    rows = []
    for doc in ("P", "B", "S", "M"):
        vid, number, date, _ = DOCS[doc]
        rows.append(["DOCUMENT", f"DOC|{K[doc]}", vid, number, date, POS[doc], "", RAW[doc]])
        if doc == "P":
            rows.append(["PAYMENT", f"CASH|{K[doc]}", vid, number, date, POS[doc], "", RAW[doc]])
        for posting in {"B": (2, 10, 11)}.get(doc, ()):
            rows.append(["PAYMENT", f"BANK|{K[doc]}|{posting}", vid, number, date, POS[doc], str(posting), RAW[doc]])
        if doc == "P":
            rows.append(["CONTRACT", D1, "", "Договор 1", "2026-01-10", "", "", '{"B","0","0","55","0","0","     7   "}'])
        if doc == "B":
            rows.append(["CONTRACT", D2, "", "Договор 2", "2026-01-11", "", "", '{"B","0","0","55","0","0","     8   "}'])
    return rows


def diagnostics_rows() -> list[list[str]]:
    rows = [["ERROR", "manifest.csv", "PACKAGE", "coverage_status", "COVERAGE_INCOMPLETE", "Диагностический набор Э0.3"],
            ["WARNING", "ledger62.csv", f"DOC|{K['P']}", "payment_id", "PAYMENT_MEMBERSHIP_UNKNOWN", "неденежные стороны в ПКО"]]
    for posting in (2, 10, 11):
        for name in ("НомерДокументаОплаты", "ДатаДокументаОплаты"):
            rows.append(["WARNING", "ledger62.csv", f"BANK|{K['B']}|{posting}", name, "PRIMARY_NOT_CONNECTED", "функция не подключена"])
    for posting in (7, 8):
        rows += [["WARNING", "ledger62.csv", f"LED|{K['M']}|{posting}|1", "MovementRole", "MOVEMENT_ROLE_UNKNOWN", "сторно или ручная операция"],
                 ["WARNING", "ledger62.csv", f"LED|{K['M']}|{posting}|1", "payment_id", "PAYMENT_MEMBERSHIP_UNKNOWN", "роль не установлена"]]
    return rows


def build(directory: Path) -> None:
    directory.mkdir(parents=True)
    write_csv(directory / "manifest.csv", ["key", "value"], [
        ["run_id", RUN_ID], ["schema_version", "4"], ["identity_scheme", "STABLE_DOCUMENT_V1"],
        ["encoding", "windows-1251"], ["source_id", "ctoksm-buh"], ["account_scope", "62.1+62.2:ALL_CUSTOMERS"],
        ["currency", "RUB"], ["history_start", "2026-01-01"], ["match_horizon_end", "2026-02-15"],
        ["source_read_mode", "QUIET_WINDOW"], ["export_mode", "FULL"], ["coverage_status", "INCOMPLETE"],
    ])
    write_csv(directory / "export_status.csv", ["key", "value"], [["run_id", RUN_ID], ["schema_version", "4"], ["status", "WRITING"]])
    write_csv(directory / "diagnostics.csv", e02.DIAG_HEADER, diagnostics_rows())
    write_csv(directory / "id_map.csv", e02.ID_MAP_HEADER, id_map_rows())
    write_csv(directory / "ledger62.csv", chk.LEDGER_HEADER, ledger_rows())
    write_csv(directory / "csv_selftest.csv", e02.SELFTEST_HEADER, [[n, v, str(len(v))] for n, v in e02.SELFTEST_CASES])
    log = f"2026-10-10 10:00:00 | INFO | START Э0.3 version=2026.10.10-04 run_id={RUN_ID} source_id=ctoksm-buh окно=2026-02-01..2026-02-15\r\n" \
          f"2026-10-10 10:01:00 | INFO | END Э0.3 итог=PASS run_id={RUN_ID} export_status=WRITING\r\n"
    (directory / "1c_export.log").write_bytes(log.encode("cp1251"))


class CheckE03PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / RUN_ID
        build(self.run)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def errors(self) -> list[dict]:
        findings, _ = chk.check_package(self.run)
        return [item for item in findings.items if item["severity"] == "ERROR"]

    def edit(self, name: str, header: list[str], change) -> None:
        path = self.run / name
        records = e02.parse_csv(path.read_bytes(), name, e02.Findings())
        write_csv(path, header, change([list(r) for r in records[1:]]))

    def edit_ledger(self, number: int, **fields) -> None:
        def change(rows):
            for name, value in fields.items():
                rows[number][chk.LEDGER_HEADER.index(name)] = value
            return rows
        self.edit("ledger62.csv", chk.LEDGER_HEADER, change)

    def drop_diag(self, predicate) -> None:
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: [r for r in rows if not predicate(r)])

    def assertFails(self, needle: str) -> None:
        errors = self.errors()
        self.assertTrue(any(needle in item["message"] for item in errors), errors)

    def test_valid_set_passes(self) -> None:
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        ledger = summary["ledger62"]
        self.assertEqual(ledger["payments"], {"CASH": 1, "BANK": 3})
        # Деньги: 6000+4000 (ПКО, перенос не входит) + 10000+5000 (выписка) = 25000 руб, возврат 2000;
        # ручная операция в деньги не входит (UNKNOWN).
        self.assertEqual(ledger["money_kopecks"]["cash_in"], 2500000)
        self.assertEqual(ledger["roles"]["UNKNOWN"], {"rows": 2, "kopecks": 20000})
        self.assertEqual(ledger["money_kopecks"]["cash_out"], 200000)
        self.assertEqual(ledger["transfers_62_62"], {"postings": 2, "same_customer": 2, "between_customers": 0})
        self.assertEqual(ledger["multi_payment_statements"], 1)
        self.assertEqual(ledger["turnovers_kopecks"]["62.1"], {"D": 1650000, "K": 600000 + 300000 + 1000000 + 500000 + 1650000 - 50000 + 70000})
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(e02.report(findings, summary, None), 0)

    def test_role_must_match_accounts_and_sign(self) -> None:
        self.edit_ledger(7, MovementRole="CASH_IN")
        self.assertFails("ожидалась NONCASH")

    def test_negative_receipt_is_not_cash_in(self) -> None:
        # Отрицательная денежная корреспонденция - не CASH_IN по модулю и не CASH_OUT по знаку (№1 §12, №2 §3.2).
        self.edit_ledger(4, СуммаКоп="-1000000")
        self.assertFails("ожидалась UNKNOWN")

    def test_payment_fields_only_for_money_roles(self) -> None:
        self.edit_ledger(7, payment_id=f"DOC|{K['S']}")
        self.assertFails("поля оплаты заполнены у строки с ролью NONCASH")

    def test_cash_payment_id_is_per_document(self) -> None:
        self.edit_ledger(1, payment_id=f"CASH|{K['P']}|2")
        self.assertFails("ожидался")

    def test_unknown_requires_diagnostics(self) -> None:
        self.drop_diag(lambda r: r[4] == "MOVEMENT_ROLE_UNKNOWN")
        self.assertFails("нет диагностики MOVEMENT_ROLE_UNKNOWN")

    def test_unknown_money_requires_membership_diagnostic(self) -> None:
        self.drop_diag(lambda r: r[4] == "PAYMENT_MEMBERSHIP_UNKNOWN" and r[2].startswith("LED|"))
        self.assertFails("денежная корреспонденция без payment_id")

    def test_noncash_sides_in_payment_document_need_warning(self) -> None:
        self.drop_diag(lambda r: r[4] == "PAYMENT_MEMBERSHIP_UNKNOWN" and r[2].startswith("DOC|"))
        self.assertFails("неденежные стороны в документе с денежными")

    def test_bank_primary_must_be_diagnosed(self) -> None:
        self.drop_diag(lambda r: r[2] == f"BANK|{K['B']}|10" and r[3] == "ДатаДокументаОплаты")
        self.assertFails("пустое ДатаДокументаОплаты BANK-оплаты без диагностики")

    def test_primary_equal_to_statement_is_not_an_error(self) -> None:
        # Ревью 508cd54: платёжный документ может иметь тот же номер и дату, что и выписка; FOUND не должен
        # давать FAIL. Совпадение показывается для ручной сверки.
        self.edit_ledger(4, НомерДокументаОплаты="0012", ДатаДокументаОплаты="2026-02-03")
        self.drop_diag(lambda r: r[2] == f"BANK|{K['B']}|2")
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertTrue(any(i["code"] == "PRIMARY_EQUALS_SOURCE" and i["severity"] == "INFO" for i in findings.items))
        self.assertEqual(summary["verdict"], "PASS")

    def complex_posting(self, *extra) -> None:
        # Сложная проводка: несколько корреспонденций под одним номером, N подряд (№1 §§7, 12.1).
        def change(rows):
            return rows[:7] + list(extra) + rows[7:]
        self.edit("ledger62.csv", chk.LEDGER_HEADER, change)
        mapped = ["PAYMENT", f"BANK|{K['B']}|12", "Выписка", "0012", "2026-02-03", POS["B"], "12", RAW["B"]]
        self.edit("id_map.csv", e02.ID_MAP_HEADER, lambda rows: rows + [mapped])
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [
            ["WARNING", "ledger62.csv", f"BANK|{K['B']}|12", name, "PRIMARY_NOT_CONNECTED", "функция не подключена"]
            for name in ("НомерДокументаОплаты", "ДатаДокументаОплаты")])

    def test_complex_posting_with_several_payments_passes(self) -> None:
        vr = "VR|ПокупателиОтгрузка"
        self.complex_posting(row("B", 12, 1, "51", "62.1", "K", 300000, C1, D1, vr, "CASH_IN", "BANK"),
                             row("B", 12, 2, "51", "62.2", "K", 200000, C2, D2, "", "CASH_IN", "BANK"))
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        ledger = summary["ledger62"]
        self.assertEqual(ledger["payments"]["BANK"], 4)
        self.assertEqual(ledger["money_kopecks"]["cash_in"], 2500000 + 500000)
        self.assertEqual(ledger["complex_postings"], 1)

    def test_complex_posting_with_transfer_passes(self) -> None:
        vr = "VR|ПокупателиОтгрузка"
        self.complex_posting(row("B", 12, 1, "51", "62.1", "K", 300000, C1, D1, vr, "CASH_IN", "BANK"),
                             row("B", 12, 2, "62.2", "62.1", "D", 100000, C1, D1, "", "NONCASH"),
                             row("B", 12, 3, "62.2", "62.1", "K", 100000, C1, D1, vr, "NONCASH"))
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [["WARNING", "ledger62.csv", f"DOC|{K['B']}", "payment_id", "PAYMENT_MEMBERSHIP_UNKNOWN", "неденежные стороны в выписке"]])
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["ledger62"]["transfers_62_62"]["postings"], 3)

    def test_complex_posting_lost_transfer_side_is_error(self) -> None:
        vr = "VR|ПокупателиОтгрузка"
        self.complex_posting(row("B", 12, 1, "62.2", "62.1", "D", 100000, C1, D1, "", "NONCASH"),
                             row("B", 12, 2, "51", "62.1", "K", 300000, C1, D1, vr, "CASH_IN", "BANK"))
        self.assertFails("нет пары сторон D, K подряд")

    def test_subaccount_of_62_1_is_in_scope(self) -> None:
        # Ревью 508cd54: подчинённые счета 62.1/62.2 входят в область (№1 §4), это не COVERAGE_INCOMPLETE.
        self.edit_ledger(7, СчетДебета="62.1.1", Счет62="62.1.1")
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertIn("62.1.1", summary["ledger62"]["turnovers_kopecks"])

    def test_cash_primary_is_the_cash_document(self) -> None:
        self.edit_ledger(0, НомерДокументаОплаты="")
        self.assertFails("у CASH первичка")

    def test_lost_side_of_transfer_is_error(self) -> None:
        self.edit("ledger62.csv", chk.LEDGER_HEADER, lambda rows: rows[:9] + rows[10:])
        self.assertFails("сторона потеряна или задвоена")

    def test_purpose_must_be_posting_content(self) -> None:
        self.edit_ledger(4, НазначениеПлатежа=TEXT.rstrip())
        self.assertFails("НазначениеПлатежа не равно СодержаниеПроводки")

    def test_position_inversion_needs_order_unknown(self) -> None:
        def change(rows):
            return rows[:4] + rows[7:10] + rows[4:7] + rows[10:]
        self.edit("ledger62.csv", chk.LEDGER_HEADER, change)
        self.assertFails("позиция не больше позиции предыдущего документа")

    def test_position_inversion_with_order_unknown_passes(self) -> None:
        self.edit("ledger62.csv", chk.LEDGER_HEADER, lambda rows: rows[:4] + rows[7:10] + rows[4:7] + rows[10:])
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [["WARNING", "ledger62.csv", f"DOC|{K['B']}", "ПозицияДокумента", "ORDER_UNKNOWN", "порядок"]])
        findings, _ = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)

    def test_document_rows_must_be_contiguous(self) -> None:
        self.edit("ledger62.csv", chk.LEDGER_HEADER, lambda rows: rows[:5] + rows[7:8] + rows[5:7] + rows[8:])
        self.assertFails("строки документа идут не подряд")

    def test_trimmed_position_is_error(self) -> None:
        self.edit_ledger(4, ПозицияДокумента=POS["B"].rstrip())
        self.assertFails("отличаются от DOCUMENT в id_map")

    def test_out_of_scope_account_needs_error(self) -> None:
        self.edit_ledger(7, СчетДебета="62.3", Счет62="62.3")
        self.assertFails("вне области 62.1+62.2")

    def test_contract_of_other_customer_is_error(self) -> None:
        self.edit_ledger(4, ДоговорID=D2)
        self.assertFails("ДоговорID не D|<КонтрагентID стороны>")

    def test_old_e02_header_is_rejected(self) -> None:
        path = self.run / "ledger62.csv"
        write_csv(path, e02.E02_IDS_HEADER, [])
        self.assertFails("заголовок")

    def test_empty_window_is_incomplete(self) -> None:
        self.edit("ledger62.csv", chk.LEDGER_HEADER, lambda rows: [])
        self.edit("id_map.csv", e02.ID_MAP_HEADER, lambda rows: [])
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows[:1])
        path = self.run / "1c_export.log"
        path.write_bytes(path.read_bytes().replace("итог=PASS".encode("cp1251"), "итог=INCOMPLETE".encode("cp1251")))
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["verdict"], "INCOMPLETE")
        self.assertEqual(e02.report(findings, summary, None), 2)

    def test_failed_run_is_fail(self) -> None:
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [["ERROR", "ledger62.csv", "PACKAGE", "", "READ_ERROR", "исключение"]])
        for name, old, new in (("export_status.csv", "status;WRITING", "status;FAILED"), ("1c_export.log", "итог=PASS", "итог=FAIL")):
            path = self.run / name
            path.write_bytes(path.read_bytes().replace(old.encode("cp1251"), new.encode("cp1251")))
        (self.run / "csv_selftest.csv").unlink()
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(e02.report(findings, summary, None), 1)

    def test_compare_identical_and_changed(self) -> None:
        other = self.tmp / "KUDIR4_20261010_006"
        shutil.copytree(self.run, other)
        findings, summary = chk.compare_packages(self.run, other)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["ledger62.csv"]["common"], 12)
        self.run, keep = other, self.run
        self.edit_ledger(5, MovementRole="UNKNOWN")
        self.run = keep
        findings, summary = chk.compare_packages(self.run, other)
        self.assertEqual(summary["ledger62.csv"]["changed_fields"], {"MovementRole": 1})

    def test_sheet_lists_sections_and_turnovers(self) -> None:
        text = chk.sheet(self.run)
        self.assertIn("| 62.1 | 16 500,00 | 40 700,00 |", text)
        for title in ("Кассовые документы", "Банковские выписки", "Ручные операции", "Денежные возвраты", "Неустановленная роль", "Проводки 62 → 62", "Отгрузки и акты"):
            self.assertIn(title, text)
        self.assertIn("Выписка № 0012 от 2026-02-03", text)
        self.assertIn("Поступило по строкам CASH_IN: 15 000,00 руб; возвращено CASH_OUT: 2 000,00 руб.", text)


class RoleRuleTests(unittest.TestCase):
    def test_expected_roles(self) -> None:
        cases = [
            (("K", "51", "Выписка", 100), "CASH_IN"),
            (("K", "50.1", "ПриходныйОрдер", 0), "CASH_IN"),
            (("D", "51", "Выписка", 100), "CASH_OUT"),
            (("D", "50.1", "РасходныйОрдер", 100), "CASH_OUT"),
            (("K", "50.1", "РасходныйОрдер", 100), "UNKNOWN"),
            (("D", "50.1", "ПриходныйОрдер", 100), "UNKNOWN"),
            (("K", "51", "Выписка", -100), "UNKNOWN"),
            (("K", "51", "АвансовыйОтчет", 100), "UNKNOWN"),
            (("D", "90.1.1", "РасходнаяНакладная", 100), "NONCASH"),
            (("K", "62.2", "РасходнаяНакладная", 100), "NONCASH"),
            (("D", "90.1.1", "РасходнаяНакладная", -100), "UNKNOWN"),
            (("K", "57.3", "Операция", 100), "NONCASH"),
            (("K", "51", "операция", 100), "UNKNOWN"),
            (("D", "51", "Операция", 100), "UNKNOWN"),
            (("D", "62.1", "Операция", 100), "NONCASH"),
        ]
        for args, role in cases:
            self.assertEqual(chk.expected_role(*args)[0], role, args)

    def test_scope_accounts(self) -> None:
        for account, base in (("62.1", "62.1"), ("62.1.1", "62.1"), ("62.2.3", "62.2"), ("62", ""), ("62.3", ""), ("62.10", ""), ("62.21", "")):
            self.assertEqual(chk.scope_account(account), base, account)

    def test_rubles(self) -> None:
        self.assertEqual(chk.rub(-1), "-0,01")
        self.assertEqual(chk.rub(123456789), "1 234 567,89")


if __name__ == "__main__":
    unittest.main()
