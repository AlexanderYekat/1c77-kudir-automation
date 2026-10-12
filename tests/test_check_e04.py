"""Э0.4: reports/check_e04.py на синтетических каталогах запуска «Набор Э0.4».

Каталог собирается так, как его пишет 1С (cp1251, CR LF, кавычки по №1 §30): ledger62 из теста Э0.3 и documents.csv —
счёт с НДС 5 %, реализация на его основании с проводками 62, непроведённый акт с неизвестным составом НДС, помеченный
на удаление счёт без покупателя. Проверяется, что верный набор проходит, а неверные роль/тип, агрегаты НДС, три нуля
вместо неизвестности, потеря документа с проводками 62, пустой вид расчётов, нарушение порядка и прочие дефекты дают
ERROR; расхождение суммы исполнения с проводками выручки — только материал ведомости.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports"))
sys.path.insert(0, str(ROOT / "tests"))

import check_e02 as e02  # noqa: E402
import check_e04 as chk  # noqa: E402
import test_check_e03 as base  # noqa: E402  ledger62, id_map and diagnostics of the E0.3 synthetic set

RUN_ID = "KUDIR4_20261012_001"
RAW = {
    "I1": '{"O","0","0","0","0","0","       301    461600   "}',
    "A": '{"O","0","0","0","0","0","       302    462100   "}',
    "I2": '{"O","0","0","0","0","0","       301    462200   "}',
}
K = {name: raw.replace("%", "%25").replace("|", "%7C") for name, raw in RAW.items()}
POS = {
    "I1": "#20260114 100000000    461600   ",
    "A": "#20260211 100000000    462100   ",
    "I2": "#20260212 100000000    462200   ",
}
VR = "VR|ПокупателиОтгрузка"


def document(doc_id, kind, number, date, position, customer="", contract="", vr="", amount="", vat=("", "", ""),
             posted="1", marked="0", comment="", status="KNOWN"):
    role, doc_type = chk.CANDIDATES[kind.upper()]
    name, cdate = base.CONTRACT_NAMES.get(contract, ("", ""))
    values = {
        "document_id": doc_id, "DocumentRole": role, "DocumentType": doc_type, "ВидДокумента": kind,
        "НомерДокумента": number, "ДатаДокумента": date, "ПозицияДокумента": position, "КонтрагентID": customer,
        "Контрагент": {base.C1: "Ромашка", base.C2: "Лютик"}.get(customer, ""), "ДоговорID": contract,
        "Договор": name, "НомерДоговора": name, "ДатаДоговора": cdate, "ВидРасчетовID": vr,
        "ВидРасчетовСПокупателем": vr[3:], "СуммаКоп": str(amount), "СуммаОблагаемаяКоп": vat[0],
        "СуммаНеоблагаемаяКоп": vat[1], "СуммаНДСКоп": vat[2], "Проведен": posted, "ПомеченНаУдаление": marked,
        "Комментарий": comment, "Содержание": "", "VATCompositionStatus": status,
    }
    return [values[name] for name in chk.DOCUMENTS_HEADER]


def documents_rows() -> list[list[str]]:
    vid, number, date, comment = base.DOCS["S"]
    return [
        # Счёт с НДС 5 %: сумма включает НДС, 1 650 000 * 5 / 105 = 78 571 коп.
        document(f"DOC|{K['I1']}", "Счет", "00087", "2026-01-14", POS["I1"], base.C2, base.D2, "", 1650000,
                 ("1650000", "0", "78571"), comment="   "),
        # Реализация на основании счёта, с проводками 62 в ledger62 (Дт 62.1 Кт 90.1.1 1 650 000).
        document(f"DOC|{base.K['S']}", vid, number, date, base.POS["S"], base.C2, base.D2, VR, 1650000,
                 ("1650000", "0", "78571"), comment=comment),
        # Непроведённый акт: вид расчётов пуст штатно, отрицательный НДС строки - состав неизвестен.
        document(f"DOC|{K['A']}", "ОказаниеУслуг", "00310", "2026-02-11", POS["A"], base.C1, base.D1, "", 390000,
                 posted="0", comment="Введен на основании: Счет на оплату № 00095        от 12 Февраля 2026 г.\r\nвторая строка", status="UNKNOWN"),
        # Помеченный на удаление счёт без покупателя, без НДС.
        document(f"DOC|{K['I2']}", "Счет", "00090", "2026-02-12", POS["I2"], amount=120000, vat=("0", "120000", "0"),
                 posted="0", marked="1"),
    ]


def id_map_rows() -> list[list[str]]:
    rows = base.id_map_rows()
    for name, (kind, number, date) in {"I1": ("Счет", "00087", "2026-01-14"), "A": ("ОказаниеУслуг", "00310", "2026-02-11"),
                                       "I2": ("Счет", "00090", "2026-02-12")}.items():
        rows.append(["DOCUMENT", f"DOC|{K[name]}", kind, number, date, POS[name], "", RAW[name]])
    return rows


def diagnostics_rows() -> list[list[str]]:
    rows = base.diagnostics_rows()
    rows[0][-1] = "Диагностический набор Э0.4"
    rows += [
        ["WARNING", "documents.csv", "PACKAGE", "Содержание", "SOURCE_FIELD_UNAVAILABLE", "источник Содержание документа не подтверждён"],
        ["WARNING", "documents.csv", f"DOC|{K['A']}", "VATCompositionStatus", "VAT_COMPOSITION_UNKNOWN", "документ ОказаниеУслуг 00310 от 2026-02-11: отрицательный НДС в строках табличной части: 1; агрегаты НДС пусты"],
        ["WARNING", "documents.csv", f"DOC|{K['I2']}", "КонтрагентID", "ANALYTICS_UNKNOWN", "пустой покупатель"],
    ]
    return rows


def build(directory: Path, end: str = "PASS", status: str = "WRITING") -> None:
    base.build(directory)
    for name in ("manifest.csv", "export_status.csv"):
        path = directory / name
        path.write_bytes(path.read_bytes().replace(base.RUN_ID.encode(), RUN_ID.encode()))
    base.write_csv(directory / "export_status.csv", ["key", "value"], [["run_id", RUN_ID], ["schema_version", "4"], ["status", status]])
    base.write_csv(directory / "diagnostics.csv", e02.DIAG_HEADER, diagnostics_rows())
    base.write_csv(directory / "id_map.csv", e02.ID_MAP_HEADER, id_map_rows())
    base.write_csv(directory / "documents.csv", chk.DOCUMENTS_HEADER, documents_rows())
    log = f"2026-10-12 10:00:00 | INFO | START Э0.4 version=2026.10.11-04 run_id={RUN_ID} source_id=ctoksm-buh окно=2026-01-01..2026-02-15\r\n" \
          f"2026-10-12 10:01:00 | INFO | END Э0.4 итог={end} run_id={RUN_ID} export_status={status}\r\n"
    (directory / "1c_export.log").write_bytes(log.encode("cp1251"))


class CheckE04PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / RUN_ID
        build(self.run)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def findings(self) -> list[dict]:
        return chk.check_package(self.run)[0].items

    def errors(self) -> list[dict]:
        return [item for item in self.findings() if item["severity"] == "ERROR"]

    def edit(self, name: str, header: list[str], change) -> None:
        path = self.run / name
        records = e02.parse_csv(path.read_bytes(), name, e02.Findings())
        base.write_csv(path, header, change([list(r) for r in records[1:]]))

    def edit_doc(self, number: int, **fields) -> None:
        def change(rows):
            for name, value in fields.items():
                rows[number][chk.DOCUMENTS_HEADER.index(name)] = value
            return rows
        self.edit("documents.csv", chk.DOCUMENTS_HEADER, change)

    def drop_diag(self, predicate) -> None:
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: [r for r in rows if not predicate(r)])

    def assertFails(self, needle: str) -> None:
        errors = self.errors()
        self.assertTrue(any(needle in item["message"] for item in errors), errors)

    def test_valid_set_passes(self) -> None:
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["verdict"], "PASS")
        docs = summary["documents"]
        self.assertEqual(docs["rows"], 4)
        self.assertEqual(docs["by_kind"]["Счет"]["VAT KNOWN"], 2)
        self.assertEqual(docs["implied_vat_rates"], {"5%": 2})
        self.assertEqual(docs["comments"], {"empty": 2, "base_link": 2, "multi_line": 1})
        self.assertEqual(summary["documents_vs_ledger"]["candidate_documents_in_ledger"], 1)
        self.assertEqual(summary["documents_vs_ledger"]["missing_in_documents"], 0)
        self.assertEqual(summary["documents_vs_ledger"]["amount_differs"], 0)
        self.assertEqual(summary["documents_vs_ledger"]["settlement_differs"], 0)
        self.assertEqual(summary["id_map"]["DOCUMENT"], 7)
        self.assertEqual(summary["vat_on_top"]["documents"], 0)

    def test_role_and_type_follow_kind(self) -> None:
        self.edit_doc(0, DocumentRole="RECEIVABLE")
        self.assertFails("роль/тип RECEIVABLE/INVOICE")

    def test_kind_outside_candidates_is_error(self) -> None:
        self.edit_doc(3, ВидДокумента="Выписка")
        self.assertFails("вне перечня кандидатов")

    def test_known_aggregates_must_sum_to_amount(self) -> None:
        self.edit_doc(0, СуммаНеоблагаемаяКоп="100")
        self.assertFails("!= СуммаКоп")

    def test_malformed_known_aggregate_is_error_not_exception(self) -> None:
        # Ревью -04: при KNOWN с пустым или нецелым агрегатом проверка падала ValueError вместо отчёта.
        for value in ("", "1.5", "abc"):
            with self.subTest(value=value):
                build(self.tmp / f"bad{len(value)}{value.isalpha()}")
                self.run = self.tmp / f"bad{len(value)}{value.isalpha()}"
                self.edit_doc(0, СуммаНДСКоп=value)
                self.assertFails("KNOWN без целых агрегатов или суммы")
                self.assertIn("## 1. Итоги по видам", chk.sheet(self.run))

    def test_unknown_is_not_three_zeros(self) -> None:
        self.edit_doc(2, СуммаОблагаемаяКоп="0", СуммаНеоблагаемаяКоп="0", СуммаНДСКоп="0")
        self.assertFails("неизвестность не кодируется числами")

    def test_unknown_needs_diagnostic(self) -> None:
        self.drop_diag(lambda r: r[4] == "VAT_COMPOSITION_UNKNOWN")
        self.assertFails("нет диагностики VAT_COMPOSITION_UNKNOWN")

    def test_zero_vat_row_is_exempt(self) -> None:
        self.edit_doc(3, СуммаОблагаемаяКоп="120000", СуммаНеоблагаемаяКоп="0")
        self.assertFails("облагаемая сумма без НДС")

    def test_document_with_62_postings_must_be_present(self) -> None:
        self.edit("documents.csv", chk.DOCUMENTS_HEADER, lambda rows: [r for r in rows if r[0] != f"DOC|{base.K['S']}"])
        self.assertFails("с проводками 62 отсутствует в documents.csv")

    def test_unposted_document_with_postings_is_error(self) -> None:
        self.edit_doc(1, Проведен="0")
        self.assertFails("флаг Проведен прочитан неверно")

    def test_posted_receivable_needs_settlement_kind(self) -> None:
        self.edit_doc(1, ВидРасчетовID="", ВидРасчетовСПокупателем="")
        self.assertFails("пуст вид расчётов без диагностики")

    def test_settlement_kind_with_diagnostic_passes(self) -> None:
        self.edit_doc(1, ВидРасчетовID="", ВидРасчетовСПокупателем="")
        doc = f"DOC|{base.K['S']}"
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [["WARNING", "documents.csv", doc, "ВидРасчетовID", "ANALYTICS_UNKNOWN", "несколько видов"]])
        self.assertEqual(self.errors(), [])

    def test_settlement_kind_differing_from_postings_is_review_not_error(self) -> None:
        # Вид расчётов исполнения - реквизит ВыбВидыРасчетовСПокупателями; отличие от вида его проводок 62 - материал
        # ведомости для владельца, не ошибка выгрузки.
        self.edit_doc(1, ВидРасчетовID="VR|ПокупателиУслуги", ВидРасчетовСПокупателем="ПокупателиУслуги")
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["documents_vs_ledger"]["settlement_differs"], 1)
        self.assertTrue(any(item["code"] == "REVIEW" and "видом расчётов" in item["message"] for item in findings.items))
        self.assertIn("| РасходнаяНакладная № Н00166 от 2026-02-04 | ПокупателиУслуги | ПокупателиОтгрузка |", chk.sheet(self.run))

    def test_invoice_has_no_settlement_kind(self) -> None:
        self.edit_doc(0, ВидРасчетовID=VR, ВидРасчетовСПокупателем=VR[3:])
        self.assertFails("у счёта вид расчётов не читается")

    def test_position_inversion_needs_order_unknown(self) -> None:
        self.edit_doc(3, ПозицияДокумента=POS["I1"])
        self.assertFails("нет диагностики ORDER_UNKNOWN")

    def test_date_outside_window_is_error(self) -> None:
        self.edit_doc(3, ДатаДокумента="2026-02-20")
        self.assertFails("вне окна чтения")

    def test_content_must_stay_empty(self) -> None:
        self.edit_doc(0, Содержание="текст")
        self.assertFails("Содержание непусто")

    def test_content_unavailable_reported_once(self) -> None:
        self.drop_diag(lambda r: r[3] == "Содержание")
        self.assertFails("одна SOURCE_FIELD_UNAVAILABLE по Содержание")

    def test_empty_amount_needs_read_error(self) -> None:
        self.edit_doc(3, СуммаКоп="", СуммаОблагаемаяКоп="", СуммаНеоблагаемаяКоп="", СуммаНДСКоп="", VATCompositionStatus="UNKNOWN")
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [["WARNING", "documents.csv", f"DOC|{K['I2']}", "VATCompositionStatus", "VAT_COMPOSITION_UNKNOWN", "сумма документа не прочитана"]])
        self.assertFails("СуммаКоп '' не целые копейки")

    def test_empty_amount_with_kind_error_is_explained(self) -> None:
        build_failed = self.tmp / "failed"
        build(build_failed, end="FAIL", status="FAILED")
        self.run = build_failed
        self.edit_doc(3, СуммаКоп="", СуммаОблагаемаяКоп="", СуммаНеоблагаемаяКоп="", СуммаНДСКоп="", VATCompositionStatus="UNKNOWN")
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [
            ["ERROR", "documents.csv", f"DOC|{K['I1']}", "СуммаКоп", "READ_ERROR", "вид Счет: итог не читается"],
            ["WARNING", "documents.csv", f"DOC|{K['I2']}", "VATCompositionStatus", "VAT_COMPOSITION_UNKNOWN", "сумма документа не прочитана"]])
        messages = [item["message"] for item in self.errors()]
        self.assertFalse(any("СуммаКоп" in m and "не целые копейки" in m for m in messages), messages)
        self.assertEqual(chk.check_package(self.run)[1]["verdict"], "FAIL")

    def test_amount_differing_from_revenue_is_review_not_error(self) -> None:
        self.edit_doc(1, СуммаКоп="1700000", СуммаОблагаемаяКоп="1700000")
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["documents_vs_ledger"]["amount_differs"], 1)
        self.assertTrue(any(item["code"] == "REVIEW" for item in findings.items))

    def test_document_id_must_resolve_in_id_map(self) -> None:
        self.edit("id_map.csv", e02.ID_MAP_HEADER, lambda rows: [r for r in rows if r[1] != f"DOC|{K['A']}"])
        self.assertFails("document_id не разрешается в id_map")

    def test_contract_of_other_customer_needs_warning(self) -> None:
        # Запуск KUDIR4_20261011_003: счёт с договором другого покупателя. ID - по владельцу договора, связь
        # документа с договором не подтверждена: без ANALYTICS_UNKNOWN это ошибка, с ним - допустимо.
        self.edit_doc(0, ДоговорID=base.D1)
        self.assertFails("договор другого покупателя")
        doc = f"DOC|{K['I1']}"
        self.edit("diagnostics.csv", e02.DIAG_HEADER, lambda rows: rows + [["WARNING", "documents.csv", doc, "ДоговорID", "ANALYTICS_UNKNOWN", "договор другого покупателя"]])
        self.assertEqual(self.errors(), [])

    def test_contract_id_form(self) -> None:
        self.edit_doc(0, ДоговорID="D|0002")
        self.assertFails("ДоговорID не D|C|<код владельца>|<код договора>")

    def test_lost_document_of_ledger_is_fail_not_incomplete(self) -> None:
        self.edit("documents.csv", chk.DOCUMENTS_HEADER, lambda rows: [])
        findings, summary = chk.check_package(self.run)
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertTrue(any("отсутствует в documents.csv" in item["message"] for item in findings.items if item["severity"] == "ERROR"))

    def test_window_without_candidates_is_incomplete(self) -> None:
        s = f"DOC|{base.K['S']}"
        self.edit("documents.csv", chk.DOCUMENTS_HEADER, lambda rows: [])
        self.edit("ledger62.csv", base.chk.LEDGER_HEADER, lambda rows: [r for r in rows if r[1] != s])
        self.edit("id_map.csv", e02.ID_MAP_HEADER, lambda rows: [r for r in rows if r[1] not in {s} | {f"DOC|{K[n]}" for n in K}])
        log = self.run / "1c_export.log"
        log.write_bytes(log.read_bytes().replace("итог=PASS".encode("cp1251"), "итог=INCOMPLETE".encode("cp1251")))
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["verdict"], "INCOMPLETE")

    def test_comment_must_match_ledger(self) -> None:
        self.edit_doc(1, Комментарий=base.DOCS["S"][3] + " ")
        self.assertFails("Комментарий в documents.csv отличается")

    def test_compare_identical_and_changed(self) -> None:
        second = self.tmp / "KUDIR4_20261012_002"
        build(second)
        findings, summary = chk.compare_packages(self.run, second)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["documents.csv"]["common"], 4)
        self.run = second
        self.edit_doc(0, СуммаНДСКоп="78572")
        findings, summary = chk.compare_packages(self.tmp / RUN_ID, second)
        self.assertEqual(summary["documents.csv"]["changed_fields"], {"СуммаНДСКоп": 1})

    def test_sheet_lists_sections(self) -> None:
        text = chk.sheet(self.run)
        for title in ("## 1. Итоги по видам", "## 2. Счета", "## 3. Реализации и акты", "## 4. Состав НДС",
                      "## 5. Проведённые исполнения", "## 6. Документы денежных событий"):
            self.assertIn(title, text)
        self.assertIn("счёт найден — 1", text)
        self.assertIn("Счёт по комментарию: Счет № 00087 от 2026-01-14", text)
        self.assertIn("- 5%: 2 док.", text)
        self.assertIn("В журнале запуска нет документов с НДС «сверху».", text)

    def test_sheet_lists_vat_on_top_from_log(self) -> None:
        # Запуск KUDIR4_20261011_003: акты с НДС «сверху» штатны (бухгалтер); экспорт пишет их в журнал для сверки.
        log = self.run / "1c_export.log"
        line = f"2026-10-12 10:00:30 | INFO | {chk.VAT_ON_TOP_LOG}РасходнаяНакладная Н00166 от 2026-02-04; строк с НДС сверху 1, сумма документа 1650000 коп, НДС 78571 коп\r\n"
        text = log.read_bytes().decode("cp1251")
        log.write_bytes(text.replace("\r\n", "\r\n" + line, 1).encode("cp1251"))
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["vat_on_top"], {"documents": 1, "first": ["РасходнаяНакладная Н00166 2026-02-04"]})
        sheet = chk.sheet(self.run)
        section = sheet[sheet.index("### НДС «сверху»"):sheet.index("## 5.")]
        self.assertIn("### РасходнаяНакладная № Н00166 от 2026-02-04", section)


    def test_sheet_lists_rate_other_than_main(self) -> None:
        # Запуск KUDIR4_20261012_001: три счёта с НДС 5 % от «Всего» (НДС / (облагаемая − НДС) = 5,3 %) - материал ведомости.
        self.edit_doc(1, СуммаНДСКоп="82500")
        sheet = chk.sheet(self.run)
        self.assertIn("- 5.3%: 1 док.", sheet)
        section = sheet[sheet.index("### Ставка не 5%"):sheet.index("## 5.")]
        self.assertIn("### РасходнаяНакладная № Н00166 от 2026-02-04", section)
        self.assertNotIn("Счет № 00087", section)

    def test_sheet_separates_redated_invoice(self) -> None:
        # Запуск KUDIR4_20261012_001: дата счёта в комментарии исполнения отличается от даты счёта (счёт передатирован).
        self.edit_doc(1, Комментарий="Введен на основании: Счет на оплату № 00087 от 20 января 2026 г.")
        sheet = chk.sheet(self.run)
        self.assertIn("счёт с этим номером есть, дата другая — 1", sheet)
        self.assertIn("Счёт по комментарию: по номеру и дате не найден; с тем же номером — Счет № 00087 от 2026-01-14", sheet)

class CheckE04RuleTests(unittest.TestCase):
    def test_candidates(self) -> None:
        self.assertEqual(chk.candidate("СЧЕТ"), ("INVOICE", "INVOICE"))
        self.assertEqual(chk.candidate("ОказаниеУслуг"), ("RECEIVABLE", "SERVICE_ACT"))
        self.assertIsNone(chk.candidate("Выписка"))

    def test_implied_rate(self) -> None:
        self.assertEqual(chk.implied_rate(78571, 1650000), "5%")
        self.assertEqual(chk.implied_rate(20000, 120000), "20%")
        self.assertEqual(chk.implied_rate(5, 5), "?")


if __name__ == "__main__":
    unittest.main()
