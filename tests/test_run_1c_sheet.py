"""reports/run_1c_sheet.py: разбор таблиц отчётов 1С и план команд по запуску.

Таблицы повторяют формат Таблица.Записать(.., "TXT"), полученный 12.10.2026 от штатных «ОСВ по счету» и «Карточки
счета» 62: табуляции, CRLF между записями, многострочная ячейка в кавычках, суммы «70,307.50», пустая сумма - пробел.
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

import run_1c_sheet as sheet  # noqa: E402
import test_check_e05 as base5  # noqa: E402


def tsv(rows: list[list[str]]) -> bytes:
    def cell(value: str) -> str:
        return '"' + value.replace('"', '""') + '"' if "\n" in value else value
    return "".join("\t".join(cell(c) for c in row) + "\r\n" for row in rows).encode("cp1251")


OSV = [["", "", "", "", "", "", ""], ["Оборотно-сальдовая ведомость по счету: 62", "", "", "", "", "", ""],
       ["Субконто", "Сальдо на начало периода", "", "Обороты за период", "", "Сальдо на конец периода", ""],
       ["62.1", "        70,307.50", " ", "     5,475,604.10", "     5,528,581.60", "        17,330.00", " "],
       ["62.2", " ", "       203,394.18", "       221,260.00", "       428,530.00", " ", "       410,664.18"],
       ["Итого", " ", "       133,086.68", "     5,696,864.10", "     5,957,111.60", " ", "       393,334.18"]]
OSVK = [["Субконто", "", "", "", "", "", ""],
        ["ПОКУПАТЕЛЬ 1", " ", "         1,896.00", " ", " ", " ", "         1,896.00"],
        ["   564", " ", "         1,500.00", " ", " ", " ", "         1,500.00"],
        ["   591", " ", "           396.00", " ", " ", " ", "           396.00"],
        ["ПОКУПАТЕЛЬ 2", "           200.00", " ", "         3,400.00", " ", "         3,600.00", " "],
        ["   ", " ", " ", "         3,400.00", " ", "         3,400.00", " "],
        ["   420ф", "           200.00", " ", " ", " ", "           200.00", " "],
        ["Итого развернутое", "           200.00", "         1,896.00", "", "", "         3,600.00", "         1,896.00"]]
OSVK_KEYS = "2\t00000415\t\t\r\n3\t00000415\t000026\t00000415\r\n4\t00000415\t000033\t00000415\r\n5\t15000610\t\t\r\n" \
            "6\t15000610\t\t\r\n7\t15000610\t000005\t15000610\r\n8\t15000610\t000005\t15000610\r\n"
CARD = [["Дата", "Документ", "Операция", "Дебет", "", "Кредит", "", "Текущее сальдо", ""],
        ["Сальдо на 01.01.26", "", "", "", "           100.00", "", " ", "", ""],
        ["05.01.26", "Отгр.товаров Н00004", "Отгрузка\nтоваров", "62.1", "           336.00", "90.1.1", " ", "Д", "           436.00"],
        ["06.01.26", "Выписка 0001", "Оплата", "51", " ", "62.1", "           300.00", "Д", "           136.00"],
        ["06.01.26", "Выписка 0001", "Оплата", "51", " ", "62.1", "           136.00", " ", " "],
        ["06.01.26", "Возврат", "Возврат\nаванса", "62.2", "            50.00", "51", " ", "Д", "            50.00"],
        ["Обороты за период", "", "", "", "           386.00", "", "           436.00", "", ""]]
CARD_KEYS = "3\tРасходнаяНакладная\tН00004\t2026-01-05\t2\r\n4\tВыписка\t0001\t2026-01-06\t3\r\n5\tВыписка\t0001\t2026-01-06\t5\r\n" \
            "6\tВыписка\t0002\t2026-01-06\t1\r\n7\tВыписка\t0002\t2026-01-06\t1\r\n"


class SheetTablesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "protocols").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, command: str, table: list[list[str]], keys: str | None = None):
        (self.tmp / "protocols" / f"{command}-table.txt").write_bytes(tsv(table))
        if keys is not None:
            (self.tmp / "protocols" / f"{command}-keys.txt").write_bytes(keys.encode("cp1251"))
        return sheet.report_rows(self.tmp, command)

    def test_records_keep_multiline_cells(self) -> None:
        table, _ = self.put("card_x", CARD)
        self.assertEqual(len(table), len(CARD))
        self.assertEqual(table[2][2], "Отгрузка\nтоваров")

    def test_kopecks(self) -> None:
        self.assertEqual(sheet.kopecks("        70,307.50"), 7030750)
        self.assertEqual(sheet.kopecks(" "), 0)
        self.assertEqual(sheet.kopecks("     5,475,604.10"), 547560410)

    def test_osv_per_subaccount(self) -> None:
        table, _ = self.put("osv_x", OSV)
        self.assertEqual(sheet.osv_accounts(table), {"62.1": (7030750, 0, 1733000, 0), "62.2": (0, 20339418, 0, 41066418)})

    def test_osv_by_customer_and_contract(self) -> None:
        table, keys = self.put("osvk_x", OSVK, OSVK_KEYS)
        opening, closing = sheet.osv_balances(table, keys)
        self.assertEqual(opening[("CUSTOMER", "C|00000415", "ALL", "")], -189600)
        self.assertEqual(opening[("CUSTOMER_CONTRACT", "C|00000415", "VALUE", "D|C|00000415|000026")], -150000)
        self.assertEqual(closing[("CUSTOMER_CONTRACT", "C|15000610", "EMPTY", "")], 340000)
        # The totals row keeps the drill-down of the last contract and is not taken for a customer or contract row.
        self.assertEqual(opening[("CUSTOMER_CONTRACT", "C|15000610", "VALUE", "D|C|15000610|000005")], 20000)
        self.assertEqual(len(opening), 6)

    def test_repeated_row_key_is_refused(self) -> None:
        table, keys = self.put("osvk_x", OSVK, OSVK_KEYS.replace("4\t00000415\t000033", "4\t00000415\t000026"))
        with self.assertRaises(ValueError):
            sheet.osv_balances(table, keys)

    def test_card_balance_before_and_after_document(self) -> None:
        table, keys = self.put("card_x", CARD, CARD_KEYS)
        # Two postings of statement 0001: before - after the sale, after - after its last posting.
        self.assertEqual(sheet.card_balances(table, keys, "Выписка", "0001", "2026-01-06"), (43600, 0, 2))
        # The first posting of the period: before - the opening row.
        self.assertEqual(sheet.card_balances(table, keys, "РасходнаяНакладная", "Н00004", "2026-01-05"), (10000, 43600, 1))
        self.assertEqual(sheet.card_balances(table, keys, "Выписка", "0003", "2026-01-06"), (None, None, 0))


class SheetPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / base5.RUN_ID
        base5.build(self.run)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plan_reports_of_quarters_and_documents(self) -> None:
        commands = sheet.plan(self.run)
        self.assertEqual(commands[:4], ["osv_20260101_20260331", "osvk_20260101_20260331", "osv_20260401_20260415", "osvk_20260401_20260415"])
        cards = commands[4:]
        self.assertTrue(cards and all(sheet.COMMAND.fullmatch(c) and c.startswith("card_") for c in cards), cards)

    def test_controller_uses_the_drill_down_of_the_configuration(self) -> None:
        text = sheet.CONTROLLER
        self.assertIn("Функция Kudir77(Путь, Команда, Протокол, Метка, Нач, Кон) Экспорт", text)
        self.assertIn("глОбработкаРасшифровки(Р77, 0, Т77);", text)
        self.assertIn('Р77.Установить("Обновить", 1);', text)
        # The modal warning of a period beyond the calculated totals is avoided before the report is called.
        self.assertLess(text.index("КонецРассчитанногоПериодаБИ()"), text.index("глОбработкаРасшифровки"))
        self.assertIn('Т77.Записать(Файл77, "TXT");', text)


if __name__ == "__main__":
    unittest.main()
