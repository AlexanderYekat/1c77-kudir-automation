"""Э0.2: reports/check_e02.py на синтетических каталогах запуска.

Каталог собирается так же, как его пишет кнопка «Набор Э0.2» (cp1251, CR LF, кавычки по №1 §30).
Проверяется, что верный набор проходит, а обрезанная позиция, дубль ID, сырой | в K, BOM, LF без CR,
COMPLETE, незакавыченный разделитель, искажённая самопроверка и прочие дефекты дают ERROR.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports"))

import check_e02 as chk  # noqa: E402

RUN_ID = "KUDIR4_20261010_001"
RAW1 = '{"O","0","0","238","0","0","    464838   "}'
RAW2 = '{"O","0","0","276","0","0","  46|%81   "}'
RAW3 = '{"O","0","0","301","0","0","    470001   "}'
K1, K2, K3 = (chk_raw.replace("%", "%25").replace("|", "%7C") for chk_raw in (RAW1, RAW2, RAW3))
POS1 = "#20260202 679630000    464838   "
POS2 = "#20260203 100000000     46181   "
POS3 = "#20260204 120000000    470001   "
CONTRACT = "D|C|00001|0001"


def field(value: str) -> str:
    value = str(value)
    if any(c in value for c in ';"\r\n') or (value and (value[0] in " \t" or value[-1] in " \t")):
        return '"' + value.replace('"', '""') + '"'
    return value


def write_csv(path: Path, header: list[str], rows: list[list[str]], terminator: str = "\r\n") -> None:
    text = "".join(";".join(field(v) for v in record) + terminator for record in [header, *rows])
    path.write_bytes(text.encode("cp1251"))


def ids_row(led, src, pay, kind, pos, posting, n, side, amount="10000", text="Оплата; по счёту №1\r\nвторая строка"):
    return [led, src, pay, kind, "2026-02-02", pos, posting, n, side, "62.1", "51" if side == "K" else "62.1",
            "62.1" if side == "K" else "62.2", amount, "Выписка", "000035", "2026-02-02", "C|00001", CONTRACT,
            "VR|Покупатели", text, "  содержание с пробелами  ", ""]


def build(directory: Path) -> None:
    directory.mkdir(parents=True)
    write_csv(directory / "manifest.csv", ["key", "value"], [
        ["run_id", RUN_ID], ["schema_version", "4"], ["identity_scheme", "STABLE_DOCUMENT_V1"],
        ["encoding", "windows-1251"], ["source_id", "ctoksm-buh"], ["account_scope", "62.1+62.2:ALL_CUSTOMERS"],
        ["currency", "RUB"], ["history_start", "2026-01-01"], ["match_horizon_end", "2026-02-15"],
        ["source_read_mode", "QUIET_WINDOW"], ["export_mode", "FULL"], ["coverage_status", "INCOMPLETE"],
    ])
    write_csv(directory / "export_status.csv", ["key", "value"], [["run_id", RUN_ID], ["schema_version", "4"], ["status", "WRITING"]])
    write_csv(directory / "diagnostics.csv", chk.DIAG_HEADER, [
        ["ERROR", "manifest.csv", "PACKAGE", "coverage_status", "COVERAGE_INCOMPLETE", "Диагностический набор Э0.2"],
        ["WARNING", "e02_ids.csv", f"LED|{K2}|3|1", "ВидРасчетовID", "ANALYTICS_UNKNOWN", "нет вида на 62.2"],
    ])
    write_csv(directory / "id_map.csv", chk.ID_MAP_HEADER, [
        ["DOCUMENT", f"DOC|{K1}", "ПриходныйОрдер", "000035", "2026-02-02", POS1, "", RAW1],
        ["PAYMENT", f"CASH|{K1}", "ПриходныйОрдер", "000035", "2026-02-02", POS1, "", RAW1],
        ["CONTRACT", CONTRACT, "", "Договор 1", "2026-01-10", "", "", '{"B","0","0","55","0","0","     7   "}'],
        ["DOCUMENT", f"DOC|{K2}", "Выписка", "000012", "2026-02-03", POS2, "", RAW2],
        ["PAYMENT", f"BANK|{K2}|2", "Выписка", "000012", "2026-02-03", POS2, "2", RAW2],
        ["DOCUMENT", f"DOC|{K3}", "Счет", "000101", "2026-02-04", POS3, "", RAW3],
    ])
    write_csv(directory / "e02_ids.csv", chk.E02_IDS_HEADER, [
        ids_row(f"LED|{K1}|1|1", f"DOC|{K1}", f"CASH|{K1}", "CASH", POS1, "1", "1", "K"),
        ids_row(f"LED|{K2}|2|1", f"DOC|{K2}", f"BANK|{K2}|2", "BANK", POS2, "2", "1", "K"),
        ids_row(f"LED|{K2}|3|1", f"DOC|{K2}", "", "", POS2, "3", "1", "D", "-500"),
        ids_row(f"LED|{K2}|3|2", f"DOC|{K2}", "", "", POS2, "3", "2", "K", "-500"),
    ])
    write_csv(directory / "e02_docs.csv", chk.E02_DOCS_HEADER, [[f"DOC|{K3}", "Счет", "000101", "2026-02-04", POS3]])
    write_csv(directory / "e02_csv_selftest.csv", chk.SELFTEST_HEADER, [[n, v, str(len(v))] for n, v in chk.SELFTEST_CASES])
    log = f"2026-10-10 10:00:00 | INFO | START Э0.2 version=2026.10.10-01 run_id={RUN_ID} source_id=ctoksm-buh окно=2026-02-01..2026-02-15\r\n" \
          f"2026-10-10 10:01:00 | INFO | END Э0.2 итог=PASS run_id={RUN_ID} export_status=WRITING\r\n"
    (directory / "1c_export.log").write_bytes(log.encode("cp1251"))


class CheckE02PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / RUN_ID
        build(self.run)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def errors(self, directory: Path | None = None) -> list[dict]:
        findings, _ = chk.check_package(directory or self.run)
        return [item for item in findings.items if item["severity"] == "ERROR"]

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = self.run / name
        text = path.read_bytes().decode("cp1251")
        self.assertIn(old, text)
        path.write_bytes(text.replace(old, new, 1).encode("cp1251"))

    def edit(self, name: str, header: list[str], change) -> None:
        # Правка по логическим записям: поля с K закавычены, кавычки внутри удвоены.
        path = self.run / name
        records = chk.parse_csv(path.read_bytes(), name, chk.Findings())
        write_csv(path, header, change([list(r) for r in records[1:]]))

    def assertFails(self, needle: str) -> None:
        errors = self.errors()
        self.assertTrue(any(needle in item["message"] for item in errors), errors)

    def test_valid_set_passes(self) -> None:
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["e02_ids"]["payment_kinds"], {"CASH": 1, "BANK": 1})
        self.assertEqual(summary["positions"]["with_trailing_spaces"], 3)
        self.assertEqual(summary["e02_ids"]["multi_line_texts"], 4)
        self.assertEqual(summary["log_result"], "PASS")
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(chk.report(findings, summary, None), 0)

    def empty_window(self) -> None:
        # Окно без проводок 62 и документов-кандидатов: ошибок нет, но и проверенных объектов нет.
        self.edit("e02_ids.csv", chk.E02_IDS_HEADER, lambda rows: [])
        self.edit("e02_docs.csv", chk.E02_DOCS_HEADER, lambda rows: [])
        self.edit("id_map.csv", chk.ID_MAP_HEADER, lambda rows: [])
        self.edit("diagnostics.csv", chk.DIAG_HEADER, lambda rows: rows[:1])

    def test_empty_window_is_incomplete_not_pass(self) -> None:
        self.empty_window()
        self.rewrite("1c_export.log", "итог=PASS", "итог=INCOMPLETE")
        findings, summary = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["verdict"], "INCOMPLETE")
        self.assertEqual(len([i for i in findings.items if i["severity"] == "INCOMPLETE"]), 2)
        self.assertEqual(chk.report(findings, summary, None), 2)

    def test_empty_window_reported_as_pass_is_error(self) -> None:
        self.empty_window()
        self.assertFails("ожидался INCOMPLETE")

    def test_trimmed_position_is_error(self) -> None:
        self.rewrite("e02_ids.csv", f'"{POS1}"', POS1.rstrip())
        self.assertFails("позиция строки отличается")

    def test_duplicate_ledger_id_is_error(self) -> None:
        def change(rows):
            rows[3][0] = rows[2][0]
            return rows
        self.edit("e02_ids.csv", chk.E02_IDS_HEADER, change)
        self.assertFails("повтор ledger_event_id")

    def test_k_must_decode_to_reference(self) -> None:
        def change(rows):
            rows[5][7] = RAW3.replace("470001", "470002")
            return rows
        self.edit("id_map.csv", chk.ID_MAP_HEADER, change)
        self.assertFails("K не декодируется")

    def test_raw_pipe_in_k_is_error(self) -> None:
        def change(rows):
            rows[0][0] = f"DOC|{RAW3}|x"
            return rows
        self.edit("e02_docs.csv", chk.E02_DOCS_HEADER, change)
        self.assertFails("форма document_id")

    def test_bom_is_error(self) -> None:
        path = self.run / "id_map.csv"
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        self.assertFails("BOM")

    def test_lf_terminator_is_error(self) -> None:
        path = self.run / "e02_docs.csv"
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
        self.assertFails("конец записи не CR LF")

    def test_complete_is_forbidden(self) -> None:
        self.rewrite("export_status.csv", "status;WRITING", "status;COMPLETE")
        self.assertFails("COMPLETE на Э0.2 запрещён")

    def test_unquoted_separator_breaks_record(self) -> None:
        self.rewrite("e02_docs.csv", ";Счет;", ";Сч;ет;")
        self.assertFails("полей 6, ожидалось 5")

    def test_selftest_value_must_survive(self) -> None:
        self.rewrite("e02_csv_selftest.csv", '"abc   "', "abc")
        self.assertFails("ожидалось 'abc   '")

    def test_error_requires_failed_status(self) -> None:
        self.rewrite("diagnostics.csv", "WARNING;e02_ids.csv", "ERROR;e02_ids.csv")
        self.assertFails("не FAILED")

    def test_failed_run_is_consistent_when_reported(self) -> None:
        self.rewrite("diagnostics.csv", "WARNING;e02_ids.csv", "ERROR;e02_ids.csv")
        self.rewrite("export_status.csv", "status;WRITING", "status;FAILED")
        self.rewrite("1c_export.log", "итог=PASS", "итог=FAIL")
        findings, _ = chk.check_package(self.run)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertTrue(any(item["severity"] == "REPORTED" for item in findings.items))

    def test_side_numbers_must_be_consecutive(self) -> None:
        def change(rows):
            rows[3][0] = f"LED|{K2}|3|3"
            rows[3][7] = "3"
            return rows
        self.edit("e02_ids.csv", chk.E02_IDS_HEADER, change)
        self.assertFails("N стороны не 1..m")

    def test_unreferenced_id_map_row_is_error(self) -> None:
        self.edit("e02_docs.csv", chk.E02_DOCS_HEADER, lambda rows: [])
        self.assertFails("без ссылок из файлов набора")

    def test_missing_end_line_is_reported_as_unfinished(self) -> None:
        self.rewrite("1c_export.log", "END Э0.2", "что-то")
        findings, _ = chk.check_package(self.run)
        self.assertTrue(any(item["code"] == "RUN_NOT_FINISHED" for item in findings.items))

    def test_compare_identical_and_changed(self) -> None:
        other = self.tmp / "KUDIR4_20261010_002"
        shutil.copytree(self.run, other)
        findings, summary = chk.compare_packages(self.run, other)
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["e02_ids.csv"]["common"], 4)
        text = (other / "id_map.csv").read_bytes().decode("cp1251").replace(POS3, POS3.rstrip() + "!  ")
        (other / "id_map.csv").write_bytes(text.encode("cp1251"))
        findings, summary = chk.compare_packages(self.run, other)
        self.assertEqual(summary["id_map.csv"]["changed_fields"], {"позиция": 1})

    def test_compare_requires_same_source(self) -> None:
        # K устойчив только внутри одной области source_id: наборы разных баз не сравниваются.
        other = self.tmp / "KUDIR4_20261010_002"
        shutil.copytree(self.run, other)
        text = (other / "manifest.csv").read_bytes().decode("cp1251").replace("source_id;ctoksm-buh", "source_id;other-base")
        (other / "manifest.csv").write_bytes(text.encode("cp1251"))
        findings, _ = chk.compare_packages(self.run, other)
        self.assertTrue(any(i["key"] == "source_id" and i["severity"] == "ERROR" for i in findings.items), findings.items)


class ParseCsvTests(unittest.TestCase):
    def test_quoted_line_breaks_and_quotes_are_kept(self) -> None:
        findings = chk.Findings()
        records = chk.parse_csv('a;"x\r\ny ""q"" ";" z "\r\n'.encode("cp1251"), "t.csv", findings)
        self.assertEqual(records, [["a", 'x\r\ny "q" ', " z "]])
        self.assertEqual(findings.errors, 0)

    def test_undefined_cp1251_byte_is_error(self) -> None:
        findings = chk.Findings()
        self.assertIsNone(chk.parse_csv(b"a;\x98\r\n", "t.csv", findings))
        self.assertEqual(findings.errors, 1)

    def test_k_encoding_round_trip(self) -> None:
        for raw in ("%|%7C%25|", RAW2, "", "%%7C"):
            encoded = raw.replace("%", "%25").replace("|", "%7C")
            self.assertNotIn("|", encoded)
            self.assertEqual(chk.decode_k(encoded), raw)


class StaticRulesTests(unittest.TestCase):
    """Узкие текстовые правила static: не компилятор 1С, но ловят ошибки, найденные только запуском."""

    @classmethod
    def setUpClass(cls) -> None:
        import check_e01 as base
        cls.base = base

    def test_argument_count_ignores_commas_in_literals(self) -> None:
        text = 'Процедура П(А, Б)\nКонецПроцедуры\nП(1, "x,y", 3);\nП(1, "a,b");\nП(Ф(1, 2), 3);\n'
        self.assertEqual(chk.argument_count_mismatches(text, self.base.TOKEN), [(3, "П", 3, 2)])

    def test_silent_handler_is_reported(self) -> None:
        text = "Попытка\n\tА = 1;\nИсключение\n\tА = 2;\nКонецПопытки;\nПопытка\nИсключение\n\tСообщить(ОписаниеОшибки());\nКонецПопытки;\n"
        self.assertEqual(chk.silent_handlers(text, self.base.code_only), [3])

    def test_mixed_script_identifier(self) -> None:
        # Латинская K в кириллическом имени - типичная ошибка, которую компилятор 1С примет за новую переменную.
        self.assertEqual(chk.mixed_identifiers("Ключ = 1;\nKлюч = 2;\nFSO = 3;", self.base.code_only), {"Kлюч"})

    def test_table_column_typo(self) -> None:
        # Опечатка коммита 5fb7f92: колонка ИмяСлучая создана, а присваивалась т.ИмяСлучаяСлучая.
        text = ('т = СоздатьОбъект("ТаблицаЗначений");\nт.НоваяКолонка("ИмяСлучая");\nт.НоваяСтрока();\n'
                'т.ИмяСлучая = "a";\nт.ИмяСлучаяСлучая = "b";\nт.НомерСтроки = 1;\nА = "т.Нет";\n')
        self.assertEqual(chk.table_column_mismatches(text, self.base.TOKEN), [(5, "т", "ИмяСлучаяСлучая")])


if __name__ == "__main__":
    unittest.main()
