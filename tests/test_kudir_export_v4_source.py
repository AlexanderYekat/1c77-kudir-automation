"""Э0.2-Э0.3: инварианты исходника блока КУДиР4_ в 1cv77/kudir_export.txt (ТЗ №1 §§5-12.1, 18, 21-23, 27, 30;
ТЗ №4 §§4-5, 7-8, 13).

1С 7.7 в CI нет: проверяется текст модуля. Компиляцию и запуск выполняет владелец в 1С.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "1cv77" / "kudir_export.txt"
sys.path.insert(0, str(ROOT / "reports"))

import check_e02 as chk  # noqa: E402
import check_e03 as chk3  # noqa: E402


def body(src: str, name: str) -> str:
    for kind, end in (("Процедура", "КонецПроцедуры"), ("Функция", "КонецФункции")):
        match = re.search(rf"^{kind} {re.escape(name)}\(.*\)\s*$", src, re.M)
        if match:
            return src[match.start():].split(end, 1)[0]
    raise ValueError(name)


def active(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def returned_literal(src: str, name: str) -> str:
    # Заголовок может быть суммой литералов на нескольких физических строках 1С.
    expression = re.search(r'Возврат\s+((?:"[^"]*"\s*\+\s*)*"[^"]*")\s*;', body(src, name))[1]
    return "".join(re.findall(r'"([^"]*)"', expression))


class ExportV4SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8-sig")
        cls.v4 = active(cls.src[cls.src.index("Функция КУДиР_СтрокиРавны("):])

    def test_headers_match_contract_and_checker(self) -> None:
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокКарты").split(";"), chk.ID_MAP_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокДиаг").split(";"), chk.DIAG_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокДвижений").split(";"), chk3.LEDGER_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокСамопроверки").split(";"), chk.SELFTEST_HEADER)

    def test_source_has_no_overlong_physical_lines(self) -> None:
        self.assertEqual(chk.source_line_length_issues(self.src), [])

    def test_manifest_keys_in_contract_order(self) -> None:
        keys = re.findall(r'TS\.WriteLine\("(\w+);', body(self.src, "КУДиР4_ЗаписатьМанифест"))
        self.assertEqual(keys, ["key", *chk.MANIFEST_KEYS])
        manifest = body(self.src, "КУДиР4_ЗаписатьМанифест")
        for key, value in chk.MANIFEST_FIXED.items():
            self.assertIn(f'"{key};{value}"', manifest)

    def test_status_writing_first_and_never_complete(self) -> None:
        start = active(body(self.src, "КУДиР4_НачатьЗапуск"))
        self.assertLess(start.index('КУДиР4_ЗаписатьСтатус("WRITING")'), start.index("КУДиР4_ЗаписатьМанифест"))
        self.assertLess(start.index("КУДиР4_СоздатьКаталогЗапуска"), start.index('КУДиР4_ЗаписатьСтатус("WRITING")'))
        self.assertNotRegex(self.v4, r'ЗаписатьСтатус\("COMPLETE"\)')
        status = active(body(self.src, "КУДиР4_ЗаписатьСтатус"))
        self.assertEqual(re.findall(r'WriteLine\("(\w+);', status), ["key", *chk.STATUS_KEYS])

    def test_new_run_directory_never_overwrites(self) -> None:
        make = active(body(self.src, "КУДиР4_СоздатьКаталогЗапуска"))
        self.assertIn("FolderExists(Путь) = 0", make)
        self.assertIn("CreateFolder(Путь)", make)
        for name in ("КУДиР4_ОткрытьДанные", "КУДиР4_ОткрытьКарту", "КУДиР4_ОткрытьДиаг"):
            self.assertRegex(active(body(self.src, name)), r"CreateTextFile\([^)]*, 0, 0\)")

    def test_k_from_current_document_only(self) -> None:
        posting = active(body(self.src, "КУДиР4_ПрочитатьПроводку"))
        self.assertIn("ДокОбъект.НайтиДокумент(Опер.Документ)", posting)
        self.assertIn("гл4ПрСсылка = ДокОбъект.ТекущийДокумент();", posting)
        self.assertIn("гл4ПрСист = ЗначениеВСтрокуВнутр(гл4ПрСсылка);", posting)
        self.assertNotIn("ЗначениеВСтрокуВнутр(Опер", self.v4)
        self.assertNotIn("ЗначениеВСтрокуВнутр(Выборка", self.v4)
        self.assertNotIn("Выборка.ПолучитьПозицию", self.v4)

    def test_k_encoding_order_and_exact_values(self) -> None:
        encode = active(body(self.src, "КУДиР_КодироватьКлюч"))
        self.assertLess(encode.index('"%", "%25"'), encode.index('"|", "%7C"'))
        decode = active(body(self.src, "КУДиР_ДекодироватьКлюч"))
        self.assertLess(decode.index('"%7C", "|"'), decode.index('"%25", "%"'))
        self.assertNotIn("СокрЛП", active(body(self.src, "КУДиР_ПозицияДокТочная")))
        for trimmed in ("СокрЛП(Сист", "СокрЛП(Поз", "СокрЛП(Ключ", "СокрЛП(ЗначениеВСтрокуВнутр"):
            self.assertNotIn(trimmed, self.v4)

    def test_id_shapes(self) -> None:
        pay = active(body(self.src, "КУДиР4_ИдОплаты"))
        self.assertIn('"CASH|" + Ключ', pay)
        self.assertIn('ВидОпл + "|" + Ключ + "|" + НомПров', pay)
        side = active(body(self.src, "КУДиР4_СторонаДвижения"))
        self.assertIn('"LED|" + гл4ПрКлюч + "|" + гл4ПрНомПров + "|" + СокрЛП(КУДиР4_СледНомерСтороны(гл4ПрКлюч, гл4ПрНомПров))', side)
        self.assertIn('"DOC|" + гл4ПрКлюч', side)
        self.assertIn('КУДиР4_ИдОплаты("CASH", гл4ПрКлюч, "")', side)
        self.assertIn("КУДиР4_ИдОплаты(гл4ПрВидОпл, гл4ПрКлюч, гл4ПрНомПров)", side)

    def test_collisions_are_errors_not_first_wins(self) -> None:
        remember = active(body(self.src, "КУДиР4_ЗапомнитьИд"))
        self.assertIn('"ID_COLLISION"', remember)
        self.assertIn("КУДиР_СтрокиРавны(гл4Ссылки.Item(КлючКарты), Сист) = 0", remember)
        self.assertIn('"ID_COLLISION"', active(body(self.src, "КУДиР4_ЗапомнитьПозицию")))

    def test_exact_string_comparison_by_codes(self) -> None:
        equal = active(body(self.src, "КУДиР_СтрокиРавны"))
        self.assertIn("СтрДлина", equal)
        self.assertIn("КодСимв(Сред(СтрА, н, 1)) <> КодСимв(Сред(СтрБ, н, 1))", equal)
        reader = active(body(self.src, "КУДиР4_ПрочитатьЗапись"))
        self.assertNotIn('Сим = ""', reader)
        self.assertIn("СтрДлина(Сим) = 0", reader)

    def test_selftest_cases_match_checker(self) -> None:
        names = re.findall(r'КУДиР4_ДобавитьСлучай\(т, "(\w+)"', body(self.src, "КУДиР4_СлучаиСамопроверки"))
        self.assertEqual(names, [name for name, _ in chk.SELFTEST_CASES])

    def test_streams_closed_and_failed_status_on_error(self) -> None:
        finish = active(body(self.src, "КУДиР4_Завершить"))
        self.assertLess(finish.index("КУДиР4_ЗакрытьДанные()"), finish.index("КУДиР4_ЗакрытьДиаг()"))
        self.assertLess(finish.index("КУДиР4_ЗакрытьДиаг()"), finish.index('КУДиР4_ЗаписатьСтатус("FAILED")'))
        self.assertIn("КУДиР4_ЗакрытьОстаток()", active(body(self.src, "КУДиР4_Сбросить")))
        stages = active(body(self.src, "КУДиР4_ЭтапыЭ03"))
        self.assertEqual(stages.count("Попытка"), stages.count("КУДиР4_Фатально(ОписаниеОшибки())"))
        self.assertLess(stages.index("КУДиР4_ЗакрытьКарту()"), stages.index("КУДиР4_ПроверитьОбратимость()"))
        self.assertLess(stages.index("КУДиР4_ОбойтиПроводки(датанач, датакон, 1"), stages.index("КУДиР4_ОбойтиПроводки(датанач, датакон, 2"))

    def test_empty_window_is_incomplete_not_pass(self) -> None:
        # Без строк ledger62 итог INCOMPLETE; export_status остаётся WRITING. Число строк учитывается и при сбое
        # этапа (замечание журнала КП0.2: «итог: 0» при частично записанном файле).
        stages = active(body(self.src, "КУДиР4_ЭтапыЭ03"))
        self.assertLess(stages.index("КонецПопытки;"), stages.index("гл4ЗаписейДвижений = гл4СтрокДанных;"))
        finish = active(body(self.src, "КУДиР4_Завершить"))
        self.assertIn("Если гл4ЗаписейДвижений = 0 Тогда", finish)
        self.assertEqual(finish.count('ИтогНабора = "INCOMPLETE";'), 1)
        self.assertLess(finish.index('ИтогНабора = "FAIL";'), finish.index('ИтогНабора = "INCOMPLETE";'))
        self.assertIn("гл4ЗаписейДвижений = 0;", active(body(self.src, "КУДиР4_Сбросить")))

    def test_v4_does_not_query_metadata(self) -> None:
        # Запуск KUDIR4_20261010_001: Метаданные.Документ() без параметра вернул количество, Получить() у числа
        # упал. Блок КУДиР4_ определяет наличие вида и реквизита чтением в Попытка, а не метаданными.
        self.assertNotIn("КУДиР_ЕстьВидДокумента", self.v4)
        self.assertNotIn("КУДиР_ЕстьРеквизитДокумента", self.v4)
        self.assertNotIn("Метаданные.", self.v4)
        # Комментарий есть у всех документов (владелец, 10.10.2026): читается напрямую, без проверки наличия.
        comment = active(body(self.src, "КУДиР4_КомментарийДок"))
        self.assertIn('Возврат "" + Ссылка.Комментарий;', comment)
        self.assertNotIn("Попытка", comment)

    def test_table_columns_exist(self) -> None:
        import check_e01 as base
        self.assertEqual(chk.table_column_mismatches(self.src, base.TOKEN), [])

    def test_legacy_v2_button_disabled(self) -> None:
        legacy = active(body(self.src, "Сформировать"))
        self.assertNotIn("КУДиР_Выполнить", legacy)
        self.assertIn("КУДиР4_НаборЭ03(выбДатаНач, выбДатаКонец)", active(body(self.src, "ВыгрузкаЭ03")))
        self.assertIn("«Набор Э0.3»", legacy)


class ExportLedgerSourceTests(unittest.TestCase):
    """Э0.3: ledger62.csv по №1 §§6-12.1, 18 в исходнике КУДиР4_."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8-sig")
        cls.v4 = active(cls.src[cls.src.index("Функция КУДиР_СтрокиРавны("):])
        cls.side = active(body(cls.src, "КУДиР4_СторонаДвижения"))
        cls.role = active(body(cls.src, "КУДиР4_РольДвижения"))

    def test_version_line_matches_start_line(self) -> None:
        version = re.match(r"// version: (\S+) ", self.src)[1]
        self.assertIn(f'"START Э0.3 version={version} run_id="', active(body(self.src, "КУДиР4_НачатьЗапуск")))

    def test_row_has_all_contract_fields_in_order(self) -> None:
        # 37 значений СписокЗначений в порядке заголовка №1 §12; последние - поля оплаты.
        values = re.findall(r"сп\.ДобавитьЗначение\((.*)\);", self.side)
        self.assertEqual(len(values), len(chk3.LEDGER_HEADER))
        self.assertEqual(values[:3], ["ИдСтроки", "ИдДок", "ИдОпл"])
        self.assertEqual(values[5], '""')  # НомерСтрокиВДокументе пусто (№1 §6)
        self.assertEqual(values[10], '"" + гл4ПрКоп')
        self.assertEqual(values[23:28], ["гл4ПрСодПров", "гл4ПрСод", "гл4ПрПредст", "гл4ПрКоммент", "гл4ПрПерв"])
        self.assertEqual(values[28:], ["Роль", "ВидОплПоле", "Гран", "ОплВид", "ОплНомер", "ОплДата", "Назн", "ИстНазн", "ОплКоммент"])

    def test_role_rules_order_and_values(self) -> None:
        # Отрицательная сумма проверяется первой и даёт UNKNOWN; REVERSAL автоматически не назначается.
        self.assertLess(self.role.index("Если Коп < 0 Тогда"), self.role.index("Если Денежная = 0 Тогда"))
        self.assertLess(self.role.index("Если Денежная = 0 Тогда"), self.role.index("Если СтрДлина(ВидОпл) = 0 Тогда"))
        self.assertEqual(set(re.findall(r'Возврат "(\w+)";', self.role)), {"UNKNOWN", "NONCASH", "CASH_IN", "CASH_OUT"})
        self.assertNotIn("REVERSAL", self.v4)
        self.assertIn('В = Врег("РасходныйОрдер")', self.role)
        self.assertIn('В = Врег("ПриходныйОрдер")', self.role)
        # Решение владельца 10.10.2026: денежная проводка ручной «Операции» спорная, оплата MANUAL не назначается.
        manual = self.role.index('Если ВидОпл = "MANUAL" Тогда')
        self.assertLess(self.role.index("Если СтрДлина(ВидОпл) = 0 Тогда"), manual)
        self.assertLess(manual, self.role.index('Если Сторона = "K" Тогда'))
        self.assertIn('Возврат "UNKNOWN";', self.role[manual:self.role.index('Если Сторона = "K" Тогда')])

    def test_payment_id_only_for_money_roles(self) -> None:
        guard = self.side.index('Если (Роль = "CASH_IN") Или (Роль = "CASH_OUT") Тогда')
        self.assertLess(self.side.index("Роль = КУДиР4_РольДвижения("), guard)
        self.assertLess(guard, self.side.index('ИдОпл = КУДиР4_ИдОплаты("CASH"'))
        self.assertEqual(self.side.count("ИдОпл = КУДиР4_ИдОплаты("), 2)
        self.assertIn('"MOVEMENT_ROLE_UNKNOWN"', self.side)
        self.assertIn('"PAYMENT_MEMBERSHIP_UNKNOWN"', self.side)
        self.assertIn('"PAYMENT_MEMBERSHIP_UNKNOWN"', active(body(self.src, "КУДиР4_ЗавершитьДокумент")))

    def test_texts_and_positions_are_not_trimmed(self) -> None:
        for trimmed in ("СокрЛП(гл4ПрСод", "СокрЛП(гл4ПрПредст", "СокрЛП(гл4ПрПерв", "СокрЛП(гл4ПрКоммент", "СокрЛП(гл4ПрПоз", "СокрЛП(гл4ПрСист", "СокрЛП(Опер.Содерж", "СокрЛП(Опер.Первич"):
            self.assertNotIn(trimmed, self.v4)
        text = active(body(self.src, "КУДиР4_ТекстПроводки"))
        for source in ('"" + Опер.СодержаниеПроводки', '"" + Опер.Содержание', '"" + Опер.ПредставлениеПроводки()', '"" + Опер.ПервичныйДокумент'):
            self.assertIn(source, text)

    def test_primary_stub_not_connected_and_not_from_statement(self) -> None:
        stub = active(body(self.src, "КУДиР4_ПервичкаБанка"))
        self.assertIn('Рез.ДобавитьЗначение("NOT_CONNECTED");', stub)
        bank = active(body(self.src, "КУДиР4_РеквизитыБанка"))
        self.assertIn('КодДиаг = "PRIMARY_" + СостПерв;', bank)
        self.assertNotIn("гл4ПрНомДок", bank)
        self.assertNotIn("гл4ПрДатаДок", bank)
        self.assertIn("ОплНомер = гл4ПервНомер;", self.side)

    def test_amount_signed_kopecks_once_per_posting(self) -> None:
        self.assertIn("гл4ПрКоп = КУДиР_Копейки(Опер.Сумма);", active(body(self.src, "КУДиР4_ПрочитатьПроводку")))
        self.assertNotIn("Опер.Сумма", self.side)
        self.assertNotRegex(self.v4, r"\bМакс\(|\bМин\(")

    def test_out_of_scope_account_is_error(self) -> None:
        # Подчинённые счета 62.1/62.2 входят в область (№1 §4; ревью 508cd54), прочие 62 - ERROR.
        scope = active(body(self.src, "КУДиР4_СубсчетОбласти"))
        self.assertIn('(Счет = "62.1") Или (Лев(Счет, 5) = "62.1.")', scope)
        self.assertIn('(Счет = "62.2") Или (Лев(Счет, 5) = "62.2.")', scope)
        self.assertIn("Область = КУДиР4_СубсчетОбласти(Счет62);", self.side)
        self.assertIn("Если СтрДлина(Область) = 0 Тогда", self.side)
        self.assertIn('Если Область <> "62.2" Тогда', self.side)
        self.assertNotIn('Счет62 <> "62.', self.side)
        self.assertIn('"COVERAGE_INCOMPLETE", "движение на счёте "', self.side)

    def test_full_walk_debit_side_first(self) -> None:
        walk = active(body(self.src, "КУДиР4_ОбойтиПроводки"))
        self.assertIn("Опер.ВыбратьОперацииСПроводками(датанач, датакон);", walk)
        self.assertLess(walk.index('КУДиР4_СторонаДвижения(Опер, "D"'), walk.index('КУДиР4_СторонаДвижения(Опер, "K"'))
        self.assertLess(walk.index("КонецЦикла;"), walk.index("КУДиР4_ЗавершитьДокумент();"))


if __name__ == "__main__":
    unittest.main()
