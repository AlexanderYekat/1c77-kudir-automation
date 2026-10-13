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


def build(directory: Path, status: str = "COMPLETE", coverage: str | None = None) -> None:
    base5.build(directory)
    coverage = coverage or ("COMPLETE" if status == "COMPLETE" else "INCOMPLETE")
    manifest = directory / "manifest.csv"
    manifest.write_bytes(manifest.read_bytes().replace(b"coverage_status;INCOMPLETE", f"coverage_status;{coverage}".encode()))
    base3.write_csv(directory / "export_status.csv", ["key", "value"], [["run_id", RUN_ID], ["schema_version", "4"], ["status", status]])
    # Выгрузка FULL не пишет ERROR диагностического набора Э0.2-Э0.5 (первая строка базового набора).
    diagnostics = e02.parse_csv((directory / "diagnostics.csv").read_bytes(), "diagnostics.csv", e02.Findings())
    rows = [row for row in diagnostics[1:] if row[0] != "ERROR"]
    if status == "FAILED":
        rows.append(["ERROR", "ledger62.csv", "PACKAGE", "", "READ_ERROR", "исключение на этапе [ledger62, первое чтение]: Деление на 0"])
    base3.write_csv(directory / "diagnostics.csv", e02.DIAG_HEADER, rows)
    log = f"2026-10-13 10:00:00 | INFO | START Э0.6 version=2026.10.13-01 run_id={RUN_ID} source_id=ctoksm-buh окно=2026-01-01..{base5.HORIZON}\r\n"
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
        path = other / chk.LEDGER_FILE
        rows = e02.parse_csv(path.read_bytes(), path.name, e02.Findings())
        amount = rows[0].index("СуммаКоп")
        rows[1][amount] = str(int(rows[1][amount]) + 1)
        base3.write_csv(path, rows[0], rows[1:])
        findings, _ = chk.compare_packages(self.run, other)
        self.assertGreater(findings.errors, 0)

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
        actual = ["selected:x", "open-return:0", "loaded:2026.10.13-01"]
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

    def test_run_names(self) -> None:
        self.assertTrue(series.RUN_NAME.fullmatch("full"))
        self.assertTrue(series.RUN_NAME.fullmatch("full_ok_ledger62_publish"))
        self.assertFalse(series.RUN_NAME.fullmatch("full_crash"))
        self.assertEqual(series.steps_of("full"), ["ok"])


if __name__ == "__main__":
    unittest.main()
