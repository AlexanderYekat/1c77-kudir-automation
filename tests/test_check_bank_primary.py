"""Эталон функции банковской первички (ТЗ №1 §18): сопоставление строк выписки и проводок, разбор списка платёжек.

Случаи взяты из BANKDIAG_20261011_001: «Сдача наличных в банк» без проводки, проводки «ПС», одинаковые строки
с разными платёжками, обрезанный список бюджетника, расхождение двух источников.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reports"))

import check_bank_primary as bp  # noqa: E402


def row(corr: str, income: str, outgo: str, sub1: str, text: str, reference: str = "", details: str = "") -> dict:
    return {"КоррСчет": corr, "Приход": income, "Расход": outgo, "Субконто1": sub1, "Субконто2": "", "Субконто3": "",
            "НазначениеПлатежа": text.ljust(100), "ПервичныйДокумент": reference, "РеквизитыПлатДокументов": details.ljust(100)}


def posting(number: int, debit: str, credit: str, kopecks: int, sub1: str, text: str) -> dict:
    debit_sub = sub1 if credit[:2] == "51" else ""
    credit_sub = sub1 if debit[:2] == "51" else ""
    return {"НомерПроводки": str(number), "СчетДт": debit, "СчетКт": credit, "СуммаКоп": str(kopecks),
            "Дт1": "Основной р/с" if debit == "51" else debit_sub, "Дт2": "", "Дт3": "",
            "Кт1": "Основной р/с" if credit == "51" else credit_sub, "Кт2": "", "Кт3": "",
            "СодержаниеПроводки": text[:75].ljust(75)}


class PairingTests(unittest.TestCase):
    def test_cash_deposit_row_without_posting_is_skipped(self) -> None:
        rows = [row("60.1", "0", "40", "Банк", "Комиссия"),
                row("50.1", "680", "0", "Сдача наличных в банк", "Сдача наличных в банк", "Операция по р/с 73 (27.02.26)"),
                row("62.1", "3500", "0", "Покупатель А", "Оплата по счету 1", "Операция по р/с 12 (26.02.26)")]
        postings = [posting(1, "60.1", "51", 4000, "Банк", "Комиссия"),
                    posting(2, "51", "62.1", 350000, "Покупатель А", "Оплата по счету 1"),
                    {**posting(3, "ПС", "", 120000, "", "Покупная стоимость"), "Дт1": "Покупатель А"}]
        state, _, result = bp.pair_statement(rows, postings, 100)
        self.assertEqual(state, "")
        self.assertEqual(result["2"][:4], (3, "FOUND", "12", "2026-02-26"))
        self.assertNotIn("3", result)

    def test_identical_rows_are_told_apart_by_text_in_order(self) -> None:
        rows = [row("62.1", "2640", "0", "Покупатель Б", "Оплата по сч 235 за февраль", "Операция по р/с 3134 (18.02.26)"),
                row("62.1", "2640", "0", "Покупатель Б", "Оплата по сч Я-02070 за январь", "Операция по р/с 3133 (18.02.26)")]
        postings = [posting(1, "51", "62.1", 264000, "Покупатель Б", "Оплата по сч 235 за февраль"),
                    posting(2, "51", "62.1", 264000, "Покупатель Б", "Оплата по сч Я-02070 за январь")]
        state, _, result = bp.pair_statement(rows, postings, 100)
        self.assertEqual(state, "")
        self.assertEqual((result["1"][2], result["2"][2]), ("3134", "3133"))

    def test_skipped_row_with_key_of_paired_row_is_ambiguous(self) -> None:
        rows = [row("62.1", "100", "0", "А", "Оплата"), row("62.1", "100", "0", "А", "Оплата")]
        state, reason, result = bp.pair_statement(rows, [posting(1, "51", "62.1", 10000, "А", "Оплата")], 100)
        self.assertEqual((state, result), ("AMBIGUOUS", {}))
        self.assertIn("строка 2 без проводки", reason)

    def test_money_posting_without_row_is_ambiguous(self) -> None:
        rows = [row("62.1", "100", "0", "А", "Оплата")]
        state, _, _ = bp.pair_statement(rows, [posting(1, "51", "62.1", 20000, "А", "Оплата")], 100)
        self.assertEqual(state, "AMBIGUOUS")


class PrimaryTests(unittest.TestCase):
    def test_empty_row_is_not_found(self) -> None:
        self.assertEqual(bp.row_primary(row("62.1", "100", "0", "А", "эквайринг"), 100)[0], "NOT_FOUND")

    def test_list_of_payment_orders_one_date_is_found(self) -> None:
        state, number, date, _ = bp.row_primary(row("62.1", "59", "0", "А", "x", details="№ 68 от 02.02.26; № 65 от 02.02.26"), 100)
        self.assertEqual((state, number, date), ("FOUND", "68; 65", "2026-02-02"))

    def test_list_with_different_dates_keeps_numbers_only(self) -> None:
        state, number, date, _ = bp.parse_list("№ 1 от 02.02.26; № 2 от 03.02.26", 100)
        self.assertEqual((state, number, date), ("PARTIAL", "1; 2", ""))

    def test_cut_list_is_partial_with_whole_numbers(self) -> None:
        # Выписка 0022: обрыв после «от » - 99 символов после правой обрезки в поле 100.
        text = "№ 101049 от 04.02.26; № 101047 от 04.02.26; № 100918 от 04.02.26; № 100916 от 04.02.26; № 101051 от"
        self.assertEqual(len(text), 99)
        state, number, date, reason = bp.parse_list(text, 100)
        self.assertEqual((state, number, date), ("PARTIAL", "101049; 101047; 100918; 100916; 101051", "2026-02-04"))
        self.assertIn("мог обрезаться", reason)
        # Обрыв внутри номера на сотом символе: номер не берётся.
        inside = text[:88] + "№ 1010512345"
        self.assertEqual(len(inside), 100)
        self.assertEqual(bp.parse_list(inside, 100)[:2], ("PARTIAL", "101049; 101047; 100918; 100916"))

    def test_short_list_with_broken_item_is_error(self) -> None:
        self.assertEqual(bp.parse_list("№ 68 от 2.2.26", 100)[0], "ERROR")
        self.assertEqual(bp.parse_list("68 от 02.02.26", 100)[0], "ERROR")
        self.assertEqual(bp.parse_list("№ 68 от 31.04.26", 100)[0], "ERROR")

    def test_reference_and_list_must_agree(self) -> None:
        agree = row("62.1", "1", "0", "А", "x", "Операция по р/с 68 (02.02.26)", "№ 68 от 02.02.26")
        self.assertEqual(bp.row_primary(agree, 100)[:3], ("FOUND", "68", "2026-02-02"))
        differ = row("62.1", "1", "0", "А", "x", "Операция по р/с 68 (02.02.26)", "№ 65 от 02.02.26")
        self.assertEqual(bp.row_primary(differ, 100)[0], "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
