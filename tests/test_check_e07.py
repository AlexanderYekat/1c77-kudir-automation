"""Э0.7: reports/check_e07.py (проверка файлов по №1 §28 и перечень контрольных примеров) и разбор журналов любого этапа.

Пакет FULL строится как в test_check_e06. Проверка файлов раскладывает замечания check_e06 по семи группам №1 §28:
коллизия ID - группа 2, расхождение с принятым пакетом - группа 7, и только чистый пакет без различий получает PASS.
Ведомости и сверки DBF читают пакет любого этапа: окно, версия, НДС «сверху» и контроли по субсчетам берутся из журнала
под меткой его этапа (у пакета Э0.6 - «Э0.6 …»), а не только под меткой этапа, для которого ведомость писалась.
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
import check_e04 as e04  # noqa: E402
import check_e05 as e05  # noqa: E402
import check_e07 as chk  # noqa: E402
import test_check_e03 as base3  # noqa: E402
import test_check_e05 as base5  # noqa: E402
import test_check_e06 as base6  # noqa: E402

RUN_ID = base6.RUN_ID


class StageLogTests(unittest.TestCase):
    def test_run_start_of_any_stage(self) -> None:
        e03_line = "2026-10-10 21:30:50 | INFO | START Э0.3 version=2026.10.10-07 run_id=R source_id=ctoksm-buh окно=2026-01-01..2026-10-10 каталог=C:\\x\r\n"
        e06_line = ("2026-10-15 13:50:51 | INFO | START Э0.6 version=2026.10.15-01 run_id=R source_id=ctoksm-buh "
                    "source_read_mode=CONSISTENT_COPY окно=2026-01-01..2026-10-10 каталог=C:\\x\r\n")
        self.assertEqual(e02.run_start(e03_line), ("2026.10.10-07", ("2026-01-01", "2026-10-10")))
        self.assertEqual(e02.run_start(e06_line), ("2026.10.15-01", ("2026-01-01", "2026-10-10")))
        self.assertIsNone(e02.run_start("2026-10-15 | INFO | END Э0.6 итог=COMPLETE\r\n"))
        self.assertIsNone(e02.run_start(None))

    def test_vat_on_top_and_controls_under_the_e06_label(self) -> None:
        log = ("2026-10-15 13:51:00 | INFO | Э0.6 НДС сверху: ОказаниеУслуг 00586 от 2026-04-01; строк с НДС сверху 3\r\n"
               "2026-10-12 11:35:00 | INFO | Э0.4 НДС сверху: Счет 00599 от 2026-04-01; строк с НДС сверху 3\r\n"
               "2026-10-15 13:57:46 | INFO | Э0.6 контроль 2026-01-01 счёт 62.1: СНД=7030750 СНК=0 СКД=1733000 СКК=0 (конец 2026-03-31) коп\r\n")
        self.assertEqual(e04.vat_on_top(log), [("ОказаниеУслуг", "00586", "2026-04-01"), ("Счет", "00599", "2026-04-01")])
        self.assertEqual(e05.CONTROL_LOG.findall(log), [("2026-01-01", "62.1", "7030750", "0", "1733000", "0", "2026-03-31")])


class CheckE07FilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / RUN_ID
        base6.build(self.run)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def errors_of(self, summary: dict) -> dict[str, int]:
        return {number: group["errors"] for number, group in summary["groups"].items() if group["errors"]}

    def test_published_package_passes_every_group(self) -> None:
        accepted = self.tmp / "accepted" / RUN_ID
        base6.build(accepted)
        findings, summary = chk.check_files(self.run, [accepted])
        self.assertEqual(findings.errors, 0, findings.items)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(self.errors_of(summary), {})
        facts = summary["facts"]
        self.assertTrue(all(k["duplicates"] == 0 for k in facts["keys"].values()))
        self.assertEqual(facts["references"]["resolved"], facts["references"]["values"])
        self.assertEqual(facts["posting_numbers"]["not_positive_integers"], 0)
        self.assertEqual(facts["ledger_rows_by_quarter_sum"], facts["records"][chk.LEDGER_FILE])
        self.assertEqual(summary["comparisons"][0]["differences"], 0)
        text = chk.render(self.run, findings, summary)
        for number, title, _ in chk.GROUPS:
            self.assertIn(f"| {number} | {title} |", text)
        self.assertIn("**PASS**", text)

    def test_id_collision_fails_group_2(self) -> None:
        path = self.run / "id_map.csv"
        rows = e02.parse_csv(path.read_bytes(), path.name, e02.Findings())
        twin = list(rows[1])
        twin[-1] = twin[-1] + "X"
        base3.write_csv(path, rows[0], rows[1:] + [twin])
        findings, summary = chk.check_files(self.run, [])
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertGreater(self.errors_of(summary).get("2", 0), 0)
        self.assertEqual(summary["facts"]["keys"]["id_map.csv"]["duplicates"], 1)

    def test_difference_from_accepted_fails_group_7(self) -> None:
        accepted = self.tmp / "accepted" / RUN_ID
        base6.build(accepted)
        path = accepted / chk.LEDGER_FILE
        rows = e02.parse_csv(path.read_bytes(), path.name, e02.Findings())
        amount = rows[0].index("СуммаКоп")
        rows[1][amount] = str(int(rows[1][amount]) + 1)
        base3.write_csv(path, rows[0], rows[1:])
        findings, summary = chk.check_files(self.run, [accepted])
        self.assertEqual(summary["package_verdict"], "PASS")
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(set(self.errors_of(summary)), {"7"})
        self.assertGreater(summary["comparisons"][0]["differences"], 0)

    def test_unpublished_package_fails(self) -> None:
        shutil.rmtree(self.run)
        base6.build(self.run, status="FAILED")
        _, summary = chk.check_files(self.run, [])
        self.assertEqual(summary["verdict"], "FAIL")


class CheckE07SheetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / RUN_ID
        base6.build(self.run)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def test_sheet_has_parts_and_reads_the_e06_log(self) -> None:
        text = chk.sheet(self.run, None)
        self.assertTrue(text.startswith(f"# Контрольные примеры КП0.7 — {RUN_ID}\n"))
        self.assertEqual([line for line in text.splitlines() if line.startswith("# ")], [text.splitlines()[0]])
        for part in ("## Часть А.", "## Часть Б.", "## Часть В."):
            self.assertIn(part, text)
        self.assertNotIn("## Часть Г.", text)
        self.assertIn(f"окно чтения 2026-01-01..{base5.HORIZON}", text)
        self.assertNotIn("окно чтения ?", text)

    def test_sheet_reports_the_check_of_the_full_package(self) -> None:
        # Проверка диагностического набора Э0.5 запрещает COMPLETE: в ведомости опубликованного FULL - проверка Э0.6.
        text = chk.sheet(self.run, None)
        self.assertIn("### 4. Автоматические сверки (check_e06 package)", text)
        self.assertIn("Итог проверки: **PASS**, ошибок 0.", text)
        self.assertNotIn("- ERROR ", text)
        self.assertIn("Итог проверки: **FAIL**", e05.sheet(self.run))

    def test_sheet_with_names_stays_out_of_git(self) -> None:
        with self.assertRaises(SystemExit):
            chk.main(["sheet", str(self.run), "--out", str(ROOT / "sverka-test-e07.md")])
        self.assertFalse((ROOT / "sverka-test-e07.md").exists())
        out = self.tmp / "sheet.md"
        self.assertEqual(chk.main(["sheet", str(self.run), "--out", str(out)]), 0)
        self.assertIn("Часть В", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
