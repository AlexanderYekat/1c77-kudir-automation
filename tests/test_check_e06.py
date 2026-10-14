"""Э0.6: reports/check_e06.py и судья серии reports/run_1c_full.py на синтетических каталогах выгрузки FULL.

Опубликованный каталог строится из набора Э0.5 test_check_e05 (ledger62, documents, снимки и контроли, которые
сходятся до копейки): export_status и manifest COMPLETE, в diagnostics нет ERROR диагностического набора, журнал Э0.6
с итогом COMPLETE и замерами памяти. Каждый дефект публикации - COMPLETE при ERROR, охват не COMPLETE, оставленный
временный файл, посторонний или недостающий файл, расхождение итога журнала - даёт ERROR; неопубликованный каталог
не принимается, даже если его файлы верны.
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
import check_e06 as chk  # noqa: E402
import run_1c_full as series  # noqa: E402
import test_check_e03 as base3  # noqa: E402
import test_check_e05 as base5  # noqa: E402

RUN_ID = base5.RUN_ID
MEMORY_LINE = "2026-10-13 10:00:{s:02d} | INFO | Э0.6 память [{point}]: pid=4242 private_kb={p} ws_kb=70000 virtual_kb=300000 " \
              "peak_ws_kb=71000 peak_virtual_kb=301000 peak_private_kb={p}\r\n"


def build(directory: Path, status: str = "COMPLETE", coverage: str | None = None, read_mode: str = "QUIET_WINDOW") -> None:
    base5.build(directory)
    coverage = coverage or ("COMPLETE" if status == "COMPLETE" else "INCOMPLETE")
    manifest = directory / "manifest.csv"
    text = manifest.read_bytes().replace(b"coverage_status;INCOMPLETE", f"coverage_status;{coverage}".encode())
    manifest.write_bytes(text.replace(b"source_read_mode;QUIET_WINDOW", f"source_read_mode;{read_mode}".encode()))
    base3.write_csv(directory / "export_status.csv", ["key", "value"], [["run_id", RUN_ID], ["schema_version", "4"], ["status", status]])
    # Выгрузка FULL не пишет ERROR диагностического набора Э0.2-Э0.5 (первая строка базового набора).
    diagnostics = e02.parse_csv((directory / "diagnostics.csv").read_bytes(), "diagnostics.csv", e02.Findings())
    rows = [row for row in diagnostics[1:] if row[0] != "ERROR"]
    if status == "FAILED":
        rows.append(["ERROR", "ledger62.csv", "PACKAGE", "", "READ_ERROR", "исключение на этапе [ledger62, первое чтение]: Деление на 0"])
    base3.write_csv(directory / "diagnostics.csv", e02.DIAG_HEADER, rows)
    log = f"2026-10-13 10:00:00 | INFO | START Э0.6 version=2026.10.15-01 run_id={RUN_ID} source_id=ctoksm-buh " \
          f"source_read_mode={read_mode} окно=2026-01-01..{base5.HORIZON}\r\n"
    log += MEMORY_LINE.format(s=1, point="старт", p=40000) + MEMORY_LINE.format(s=2, point="ledger62, первое чтение", p=41000)
    log += f"2026-10-13 10:00:30 | INFO | END Э0.6 итог={status} run_id={RUN_ID} export_status={status} ошибок=0\r\n"
    log += MEMORY_LINE.format(s=31, point="после освобождения", p=40500)
    (directory / "1c_export.log").write_bytes(log.encode("cp1251"))


class CheckE06PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / RUN_ID

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def check(self) -> tuple[e02.Findings, dict]:
        return chk.check_package(self.run)

    def codes(self, findings: e02.Findings) -> list[str]:
        return [f"{item['file']}: {item['message']}" for item in findings.items if item["severity"] == "ERROR"]

    def test_published_package_passes(self) -> None:
        build(self.run)
        findings, summary = self.check()
        self.assertEqual(self.codes(findings), [])
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual([p["point"] for p in summary["memory"]], ["старт", "ledger62, первое чтение", "после освобождения"])
        self.assertEqual(summary["memory"][0]["private_kb"], 40000)

    def test_read_mode_of_a_marked_copy_passes(self) -> None:
        build(self.run, read_mode="CONSISTENT_COPY")
        findings, summary = self.check()
        self.assertEqual(self.codes(findings), [])
        self.assertEqual(summary["source_read_mode"], "CONSISTENT_COPY")

    def test_unknown_read_mode_fails(self) -> None:
        build(self.run, read_mode="COPY")
        self.assertTrue(any("'COPY', ожидалось одно из" in m for m in self.codes(self.check()[0])))

    def test_complete_status_needs_complete_coverage(self) -> None:
        build(self.run, coverage="INCOMPLETE")
        findings, summary = self.check()
        self.assertTrue(any("coverage_status" in m for m in self.codes(findings)))
        self.assertEqual(summary["verdict"], "FAIL")

    def test_complete_with_error_in_diagnostics_fails(self) -> None:
        build(self.run)
        with (self.run / "diagnostics.csv").open("ab") as stream:
            stream.write("ERROR;ledger62.csv;PACKAGE;;READ_ERROR;сбой\r\n".encode("cp1251"))
        findings, _ = self.check()
        self.assertTrue(any("COMPLETE при 1 ERROR" in m for m in self.codes(findings)))

    def test_leftover_temporary_file_fails(self) -> None:
        build(self.run)
        (self.run / "export_status.csv.tmp").write_bytes(b"key;value\r\n")
        findings, _ = self.check()
        self.assertTrue(any("временные файлы" in m for m in self.codes(findings)))

    def test_foreign_and_missing_files_fail(self) -> None:
        build(self.run)
        (self.run / "payments.csv").write_bytes(b"x\r\n")
        (self.run / chk.QUARTERS_FILE).unlink()
        messages = self.codes(self.check()[0])
        self.assertTrue(any("посторонние файлы" in m and "payments.csv" in m for m in messages))
        self.assertTrue(any("без обязательных файлов" in m and chk.QUARTERS_FILE in m for m in messages))

    def test_log_end_must_name_the_status(self) -> None:
        build(self.run)
        log = self.run / "1c_export.log"
        log.write_bytes(log.read_bytes().replace("итог=COMPLETE".encode("cp1251"), "итог=FAILED".encode("cp1251")))
        self.assertTrue(any("итог журнала FAILED" in m for m in self.codes(self.check()[0])))

    def test_failed_directory_is_never_accepted(self) -> None:
        build(self.run, status="FAILED")
        findings, summary = self.check()
        # Файлы верны, форма сбоя соблюдена: ошибок формы нет, но пакет не принимается.
        self.assertEqual(self.codes(findings), [])
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertTrue(any(item["code"] == "NOT_PUBLISHED" for item in findings.items))
        self.assertEqual(e02.report(findings, summary, None), 1)

    def test_stopped_run_content_is_unpublished_not_error(self) -> None:
        # Запуск KUDIR4_20261013_023: сеанс закрыт посреди снимков - WRITING без END, файлы частичные. «Нет снимка» -
        # следствие остановки: замечания содержимого видны как UNPUBLISHED, ошибок формы нет, пакет не принимается.
        build(self.run, status="WRITING")
        log = self.run / "1c_export.log"
        text = log.read_bytes().decode("cp1251")
        log.write_bytes(text[:text.index("2026-10-13 10:00:30")].encode("cp1251"))
        path = self.run / chk.SNAPSHOTS_FILE
        rows = e02.parse_csv(path.read_bytes(), path.name, e02.Findings())
        base3.write_csv(path, rows[0], rows[1:4])
        findings, summary = self.check()
        self.assertEqual(self.codes(findings), [])
        self.assertGreater(summary["unpublished_content_findings"], 0)
        self.assertTrue(any(item["severity"] == "UNPUBLISHED" for item in findings.items))
        self.assertEqual(summary["verdict"], "FAIL")

    def test_failed_directory_must_not_claim_coverage(self) -> None:
        build(self.run, status="FAILED", coverage="COMPLETE")
        self.assertTrue(any("объявляет охват" in m for m in self.codes(self.check()[0])))

    def test_failed_needs_a_reason(self) -> None:
        build(self.run, status="FAILED")
        rows = e02.parse_csv((self.run / "diagnostics.csv").read_bytes(), "diagnostics.csv", e02.Findings())
        base3.write_csv(self.run / "diagnostics.csv", e02.DIAG_HEADER, [r for r in rows[1:] if r[0] != "ERROR"])
        self.assertTrue(any("FAILED без ERROR" in m for m in self.codes(self.check()[0])))
        # Сбой после закрытия diagnostics.csv называется только в журнале - этого достаточно.
        with (self.run / "1c_export.log").open("ab") as stream:
            stream.write("2026-10-13 10:00:29 | ERROR | Э0.6: export_status=COMPLETE не записан: сбой\r\n".encode("cp1251"))
        self.assertFalse(any("FAILED без ERROR" in m for m in self.codes(self.check()[0])))

    def test_compare_equal_and_changed(self) -> None:
        build(self.run)
        other = self.tmp / "other" / RUN_ID
        build(other)
        findings, _ = chk.compare_packages(self.run, other)
        self.assertEqual(findings.errors, 0)
        # Копия и её рабочая база сравниваются: режим чтения - не содержимое.
        copy = self.tmp / "copy" / RUN_ID
        build(copy, read_mode="CONSISTENT_COPY")
        findings, summary = chk.compare_packages(self.run, copy)
        self.assertEqual(findings.errors, 0)
        self.assertEqual(summary["manifest"]["source_read_mode"], ("QUIET_WINDOW", "CONSISTENT_COPY"))
        path = other / chk.LEDGER_FILE
        rows = e02.parse_csv(path.read_bytes(), path.name, e02.Findings())
        amount = rows[0].index("СуммаКоп")
        rows[1][amount] = str(int(rows[1][amount]) + 1)
        base3.write_csv(path, rows[0], rows[1:])
        findings, _ = chk.compare_packages(self.run, other)
        self.assertGreater(findings.errors, 0)

    def build_empty(self, opening: str = "0") -> None:
        # Область без покупателей (№1 §4): ни движений, ни документов, ни входящих остатков - DAY_START писать не для кого.
        build(self.run)
        for name, header in ((chk.LEDGER_FILE, chk.LEDGER_HEADER), (chk.DOCUMENTS_FILE, chk.DOCUMENTS_HEADER),
                             (chk.SNAPSHOTS_FILE, chk.SNAPSHOTS_HEADER), ("id_map.csv", e02.ID_MAP_HEADER)):
            base3.write_csv(self.run / name, header, [])
        # Предупреждения, которые модуль пишет при любом содержимом: Содержание documents.csv и неполный текущий квартал.
        base3.write_csv(self.run / "diagnostics.csv", e02.DIAG_HEADER, [
            ["WARNING", chk.DOCUMENTS_FILE, "PACKAGE", "Содержание", "SOURCE_FIELD_UNAVAILABLE", "источник Содержание документа не подтверждён"],
            ["WARNING", chk.QUARTERS_FILE, "2026-04-01", "closing_debit_kopecks", "COVERAGE_INCOMPLETE", "текущий неполный квартал"],
        ])
        base3.write_csv(self.run / chk.QUARTERS_FILE, chk.QUARTERS_HEADER, [
            ["2026-01-01", "2026-03-31", "2026-03-31", "1", opening, "0", opening, "0", "KNOWN", "REFRESHED", RUN_ID, "0"],
            ["2026-04-01", "2026-06-30", base5.HORIZON, "0", opening, "0", "", "", "UNKNOWN", "REFRESHED", RUN_ID, "0"],
        ])

    def test_empty_area_passes(self) -> None:
        self.build_empty()
        findings, summary = self.check()
        self.assertEqual(self.codes(findings), [])
        self.assertEqual(summary["verdict"], "PASS")
        self.assertTrue(any(item["code"] == "EMPTY_AREA" for item in findings.items))

    def test_empty_snapshots_with_nonzero_opening_fail(self) -> None:
        # Без DAY_START общий остаток на начало обязан быть нулевым: ненулевой - потерянные снимки, а не пустая область.
        self.build_empty(opening="70000")
        self.assertTrue(any("не равен сумме покупателей DAY_START 0 коп" in m for m in self.codes(self.check()[0])))

    def test_empty_snapshots_with_customers_fail(self) -> None:
        build(self.run)
        base3.write_csv(self.run / chk.SNAPSHOTS_FILE, chk.SNAPSHOTS_HEADER, [])
        messages = self.codes(self.check()[0])
        self.assertTrue(any("DAY_START на [], ожидались" in m for m in messages))

    def test_memory_table(self) -> None:
        build(self.run)
        table = chk.memory_table([self.run])
        self.assertIn("| старт | 10:00:01 | 39.1 |", table)
        self.assertEqual(len(table.splitlines()), 2 + 3)


class SeriesJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def operation(self, name: str, directories: list[Path], statuses: list[str]) -> dict:
        actual = ["selected:x", "open-return:0", "loaded:2026.10.15-01"]
        for directory, status in zip(directories, statuses):
            actual += [f"catalog:{directory}", f"status:{status}"]
        return {"name": name, "actual": actual, "diagnostics": [], "loaded": True}

    def test_ok_and_fault_steps_judged(self) -> None:
        good, failed = self.tmp / "A" / RUN_ID, self.tmp / "B" / RUN_ID
        build(good)
        build(failed, status="FAILED")
        report = series.judge_series([self.operation("full_ok_ledger62", [good, failed], ["COMPLETE", "FAILED"])],
                                     ["full_ok_ledger62"], {}, {}, self.tmp)
        self.assertEqual(report["problems"], [])
        self.assertEqual([r["step"] for r in report["runs"]], ["ok", "ledger62"])
        self.assertEqual(report["runs"][0]["memory"][0]["point"], "старт")

    def test_fault_that_published_is_a_problem(self) -> None:
        # Серия h77-e06-series-01: сбой quarters в окне из одного квартала не сработал, каталог опубликован.
        published = self.tmp / "A" / RUN_ID
        build(published)
        report = series.judge_series([self.operation("full_quarters", [published], ["COMPLETE"])], ["full_quarters"], {}, {}, self.tmp)
        self.assertTrue(any("ожидался FAILED" in p for p in report["problems"]))

    def test_ok_step_that_failed_is_a_problem(self) -> None:
        failed = self.tmp / "A" / RUN_ID
        build(failed, status="FAILED")
        report = series.judge_series([self.operation("full", [failed], ["FAILED"])], ["full"], {}, {}, self.tmp)
        self.assertTrue(any("ожидался COMPLETE" in p for p in report["problems"]))

    def test_leftovers_missing_steps_and_changed_directories(self) -> None:
        good = self.tmp / "A" / RUN_ID
        build(good)
        (good / "manifest.csv.tmp").write_bytes(b"")
        before = {"KUDIR4_20261012_005": {"ledger62.csv": "1"}, "KUDIR4_20261012_004": {"ledger62.csv": "2"}}
        after = {"KUDIR4_20261012_005": {"ledger62.csv": "1"}, "KUDIR4_20261012_004": {"ledger62.csv": "3"}}
        report = series.judge_series([self.operation("full_ok_ok", [good], ["COMPLETE"])], ["full_ok_ok"], before, after, self.tmp)
        problems = " | ".join(report["problems"])
        self.assertIn("временные файлы", problems)
        self.assertIn("шагов 2, каталогов 1", problems)
        self.assertIn("изменены прежние каталоги: ['KUDIR4_20261012_004']", problems)
        self.assertEqual(report["previous_directories_unchanged"], 1)

    def test_missing_diagnostics_or_run_id_is_a_problem(self) -> None:
        # Замечание ревью Э0.6: без diagnostics.csv судья видел «0 ERROR», без run_id - допустимое None.
        for broken in ("diagnostics", "run_id"):
            good = self.tmp / broken / RUN_ID
            build(good)
            if broken == "diagnostics":
                (good / "diagnostics.csv").unlink()
            else:
                base3.write_csv(good / "export_status.csv", ["key", "value"], [["schema_version", "4"], ["status", "COMPLETE"]])
            report = series.judge_series([self.operation("full", [good], ["COMPLETE"])], ["full"], {}, {}, self.tmp)
            problems = " | ".join(report["problems"])
            self.assertIn("нет diagnostics.csv" if broken == "diagnostics" else "run_id export_status None", problems)
            self.assertIn("check_e06 package FAIL", problems)

    def test_ok_step_with_bad_content_is_a_problem(self) -> None:
        good = self.tmp / "A" / RUN_ID
        build(good)
        (good / chk.QUARTERS_FILE).unlink()
        report = series.judge_series([self.operation("full", [good], ["COMPLETE"])], ["full"], {}, {}, self.tmp)
        self.assertTrue(any("check_e06 package FAIL" in p and chk.QUARTERS_FILE in p for p in report["problems"]))

    def test_run_names(self) -> None:
        self.assertTrue(series.RUN_NAME.fullmatch("full"))
        self.assertTrue(series.RUN_NAME.fullmatch("full_ok_ledger62_publish"))
        self.assertFalse(series.RUN_NAME.fullmatch("full_crash"))
        self.assertEqual(series.steps_of("full"), ["ok"])


if __name__ == "__main__":
    unittest.main()
