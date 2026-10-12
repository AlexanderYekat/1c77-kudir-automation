"""Э0.2-Э0.5: инварианты исходника блока КУДиР4_ в 1cv77/kudir_export.txt (ТЗ №1 §§5-18, 21-23.4, 27, 30;
ТЗ №4 §§4-9, 13, 19).

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
import check_e04 as chk4  # noqa: E402
import check_e05 as chk5  # noqa: E402


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
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокДокументов").split(";"), chk4.DOCUMENTS_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокСнимков").split(";"), chk5.SNAPSHOTS_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокКварталов").split(";"), chk5.QUARTERS_HEADER)

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
        stages = active(body(self.src, "КУДиР4_ЭтапыЭ05"))
        self.assertEqual(stages.count("Попытка"), stages.count("КУДиР4_Фатально(ОписаниеОшибки())"))
        self.assertLess(stages.index("КУДиР4_ЗакрытьКарту()"), stages.index("КУДиР4_ПроверитьОбратимость()"))
        self.assertLess(stages.index("КУДиР4_ОбойтиПроводки(датанач, датакон, 1"), stages.index("КУДиР4_ОбойтиПроводки(датанач, датакон, 2"))
        # Документы: первое чтение до закрытия общего id_map, повторное - после, до проверки обратимости.
        self.assertLess(stages.index("КУДиР4_ОбойтиПроводки(датанач, датакон, 1"), stages.index("КУДиР4_ОбойтиДокументы(датанач, датакон, 1"))
        self.assertLess(stages.index("КУДиР4_ОбойтиДокументы(датанач, датакон, 1"), stages.index("КУДиР4_ЗакрытьКарту()"))
        self.assertLess(stages.index("КУДиР4_ЗакрытьКарту()"), stages.index("КУДиР4_ОбойтиДокументы(датанач, датакон, 2"))
        self.assertLess(stages.index("КУДиР4_ОбойтиДокументы(датанач, датакон, 2"), stages.index("КУДиР4_ПроверитьОбратимость()"))
        # Снимки и контроли - после первого чтения (им нужна вся аналитика ledger62 и documents) и до закрытия id_map:
        # договоры входящих остатков регистрируются в нём (№1 §23.3).
        self.assertLess(stages.index("КУДиР4_ОбойтиДокументы(датанач, датакон, 1"), stages.index("КУДиР4_СнимкиГраниц();"))
        self.assertLess(stages.index("КУДиР4_СнимкиГраниц();"), stages.index("КУДиР4_СнимкиПередДокументами();"))
        self.assertLess(stages.index("КУДиР4_СнимкиПередДокументами();"), stages.index("КУДиР4_КонтролиКварталов(датакон);"))
        self.assertLess(stages.index("КУДиР4_КонтролиКварталов(датакон);"), stages.index("КУДиР4_ЗакрытьКарту()"))

    def test_empty_window_is_incomplete_not_pass(self) -> None:
        # Без строк ledger62 итог INCOMPLETE; export_status остаётся WRITING. Число строк учитывается и при сбое
        # этапа (замечание журнала КП0.2: «итог: 0» при частично записанном файле).
        stages = active(body(self.src, "КУДиР4_ЭтапыЭ05"))
        self.assertLess(stages.index("КонецПопытки;"), stages.index("гл4ЗаписейДвижений = гл4СтрокДанных;"))
        self.assertIn("гл4ЗаписейДокументов = гл4СтрокДанных;", stages)
        finish = active(body(self.src, "КУДиР4_Завершить"))
        self.assertIn("Если гл4ЗаписейДвижений = 0 Тогда", finish)
        self.assertIn("Если гл4ЗаписейДокументов = 0 Тогда", finish)
        self.assertEqual(finish.count('ИтогНабора = "INCOMPLETE";'), 3)
        self.assertIn("Если гл4СнДокументов = 0 Тогда", finish)
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
        self.assertIn("КУДиР4_НаборЭ05(выбДатаНач, выбДатаКонец)", active(body(self.src, "ВыгрузкаЭ05")))
        self.assertIn("«Набор Э0.5»", legacy)


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
        self.assertIn(f'"START Э0.5 version={version} run_id="', active(body(self.src, "КУДиР4_НачатьЗапуск")))

    def test_run_labels_match_checker(self) -> None:
        # Ревью -04: журнал писал END Э0.3, а check_e04 ищет END Э0.4 - успешный набор считался прерванным (FAIL).
        # С Э0.5 метки запуска - Э0.5, их ищет check_e05.
        start = re.findall(r'"START (\S+) version=', active(body(self.src, "КУДиР4_НачатьЗапуск")))
        end = re.findall(r'"END (\S+) итог=', active(body(self.src, "КУДиР4_Завершить")))
        self.assertEqual(start, [chk5.LABEL])
        self.assertEqual(end, [chk5.LABEL])
        # Ни одна строка запуска набора не называет прежний этап (комментарии не в счёт).
        literals = re.findall(r'"[^"\n]*"', self.v4)
        self.assertEqual([s for s in literals if "Э0.3" in s or "Э0.4" in s], [])

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

    def test_primary_from_statement_row_not_from_statement(self) -> None:
        # №1 §18, BANKDIAG_20261011_001: первичка - ссылка ПервичныйДокумент строки выписки (НомерДок/ДатаДок
        # самого документа-платёжки) или список РеквизитыПлатДокументов; номер и дата выписки не подставляются.
        func = active(body(self.src, "КУДиР4_ПервичкаБанка"))
        self.assertNotIn("NOT_CONNECTED", func)
        self.assertIn("КУДиР4_ПервичкаВыписки(Ссылка, Ключ, Поз);", func)
        self.assertIn("Сост = КУДиР4_ПервичкаПроводки(НомПров, Номер, ДатаПерв, Причина);", func)
        row = active(body(self.src, "КУДиР4_ПервичкаСтроки"))
        self.assertIn('Перв = ТабВып.ПолучитьЗначение(н, "ПервичныйДокумент");', row)
        self.assertIn("Номер = СокрЛП(Перв.НомерДок);", row)
        self.assertIn("ДатаПерв = Перв.ДатаДок;", row)
        self.assertIn("Если Перв = Ссылка Тогда", row)
        primary = "".join(active(body(self.src, name)) for name in (
            "КУДиР4_ПервичкаБанка", "КУДиР4_ПервичкаВыписки", "КУДиР4_ПервичкаСтроки", "КУДиР4_ПервичкаПроводки"))
        for statement in ("Ссылка.НомерДок", "Ссылка.ДатаДок", "ДокВып.НомерДок", "ДокВып.ДатаДок", "гл4ПрНомДок", "гл4ПрДатаДок"):
            self.assertNotIn(statement, primary)
        bank = active(body(self.src, "КУДиР4_РеквизитыБанка"))
        self.assertIn('КодДиаг = "PRIMARY_" + СостПерв;', bank)
        self.assertNotIn("гл4ПрНомДок", bank)
        self.assertNotIn("гл4ПрДатаДок", bank)
        self.assertIn("ОплНомер = гл4ПервНомер;", self.side)

    def test_primary_pairing_in_row_order_with_text_check(self) -> None:
        # Проводки идут в порядке строк: следующая свободная строка с тем же ключом и текстом; без строки или при
        # пропущенной строке с ключом сопоставленной - AMBIGUOUS на всю выписку, позиция другой версии - ERROR.
        pair = active(body(self.src, "КУДиР4_ПервичкаВыписки"))
        self.assertIn("ст = Указ;", pair)
        self.assertIn("КУДиР_СтрокиРавны(Лев(ТекстСтр, СтрДлина(ТекстПров)), ТекстПров) = 1", pair)
        self.assertIn("Если КУДиР_СтрокиРавны(ПозСейчас, Поз) = 0 Тогда", pair)
        self.assertEqual(pair.count('гл4ПервВыпСост = "AMBIGUOUS";'), 2)
        self.assertLess(pair.index('гл4ПервВыпСост = "ERROR";'), pair.index("ДокВып.ВыгрузитьТабличнуюЧасть(ТабВып);"))
        self.assertTrue(pair.rstrip().endswith('гл4ПервВыпСост = "";\n\tгл4ПервВыпПричина = "";'))
        key = active(body(self.src, "КУДиР4_ПервичкаКлючПроводки"))
        self.assertIn("КУДиР_ЭтоДенежныйСчет(Опер.Дебет.Счет) = 1", key)
        self.assertIn("КУДиР_ЭтоДенежныйСчет(Опер.Кредит.Счет) = 1", key)

    def test_primary_partial_with_both_fields_is_diagnosed(self) -> None:
        # Обрезанный список платёжек - PARTIAL с номером и датой: диагностика по номеру всё равно пишется (№1 §18).
        bank = active(body(self.src, "КУДиР4_РеквизитыБанка"))
        guard = bank.index('Если (СостПерв = "PARTIAL") И (СтрДлина(Номер) > 0) И (СтрДлина(ДатаСтр) > 0) Тогда')
        self.assertLess(bank.index('КодДиаг = "PRIMARY_" + СостПерв;'), guard)
        self.assertIn('КУДиР4_Предупреждение("ledger62.csv", ИдОпл, "НомерДокументаОплаты", КодДиаг, Причина);', bank[guard:])

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


class ExportDocumentsSourceTests(unittest.TestCase):
    """Э0.4: documents.csv по №1 §§13-17, 15.1 в исходнике КУДиР4_."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8-sig")
        cls.doc = active(body(cls.src, "КУДиР4_ДокументКандидат"))
        cls.walk = active(body(cls.src, "КУДиР4_ОбойтиДокументы"))
        cls.vat = active(body(cls.src, "КУДиР4_СоставНДС"))

    def test_candidates_are_the_confirmed_kinds(self) -> None:
        # №1 §14: только три вида; иные источники движений кандидатами не становятся.
        kinds = active(body(self.src, "КУДиР4_ТипДокумента"))
        self.assertEqual(re.findall(r'Врег\("(\w+)"\)', kinds), ["Счет", "РасходнаяНакладная", "ОказаниеУслуг"])
        self.assertEqual(re.findall(r'Возврат "(\w*)";', kinds), ["INVOICE", "GOODS_SHIPMENT", "SERVICE_ACT", ""])
        self.assertIn('Роль = "INVOICE";', self.doc)
        self.assertIn('Роль = "RECEIVABLE";', self.doc)

    def test_one_walk_in_time_order_with_current_document(self) -> None:
        # Один обход всех документов окна по шкале времени 1С (№1 §22); K - ТекущийДокумент() (КП0.1), без накопления.
        self.assertIn('ДокОбъект = СоздатьОбъект("Документ");', self.walk)
        self.assertIn("ДокОбъект.ВыбратьДокументы(датанач, датакон);", self.walk)
        self.assertIn("Ссылка = ДокОбъект.ТекущийДокумент();", self.walk)
        self.assertNotIn("ТаблицаЗначений", self.walk + self.doc)
        self.assertIn("Сист = ЗначениеВСтрокуВнутр(Ссылка);", self.doc)
        self.assertIn("Поз = КУДиР_ПозицияДокТочная(Ссылка);", self.doc)

    def test_typed_object_is_checked_to_stand_on_the_same_document(self) -> None:
        # Реквизиты не берутся у другого (первого) документа вида: ссылка типизированного объекта сверяется с K.
        self.assertIn("ДокТип.НайтиДокумент(Ссылка) = 1", self.doc)
        self.assertIn("КУДиР_СтрокиРавны(ЗначениеВСтрокуВнутр(ДокТип.ТекущийДокумент()), Сист) = 1", self.doc)
        self.assertLess(self.doc.index("Встал = 1;"), self.doc.index("Если Встал = 1 Тогда"))

    def test_row_has_all_contract_fields_in_order(self) -> None:
        values = re.findall(r"сп\.ДобавитьЗначение\((.*)\);", self.doc)
        self.assertEqual(len(values), len(chk4.DOCUMENTS_HEADER))
        self.assertEqual(values[:4], ["ИдДок", "Роль", "ТипДок", "ВидДок"])
        self.assertEqual(values[15:21], ["СуммаКоп", "Обл", "Необл", "НДС", "Пров", "Пом"])
        self.assertEqual(values[21:], ["КУДиР4_КомментарийДок(Ссылка)", '""', "СтатусНДС"])

    def test_vat_unknown_is_empty_not_zero(self) -> None:
        # №1 §15.1: UNKNOWN - три пустых агрегата и VAT_COMPOSITION_UNKNOWN; KNOWN - только при согласии агрегатов с
        # суммой документа (пустая табличная часть при ненулевой сумме - частный случай расхождения).
        unknown = self.doc[self.doc.index('Если СтатусНДС <> "KNOWN" Тогда'):]
        self.assertIn('Обл = "";', unknown)
        self.assertIn('НДС = "";', unknown)
        self.assertIn('"VAT_COMPOSITION_UNKNOWN"', unknown)
        self.assertNotRegex(self.doc + self.vat, r'(Обл|Необл|НДС) = "0"')
        for check in ("Если Отриц > 0 Тогда", "Если Несогл > 0 Тогда"):
            self.assertLess(self.vat.index(check), self.vat.index('Возврат "KNOWN";'))
        known = self.doc[self.doc.index('Если СтатусНДС = "KNOWN" Тогда'):]
        self.assertIn("Число(Обл) + Число(Необл) <> Число(СуммаКоп)", known)
        self.assertLess(self.doc.index('ДокТип.Итог("Всего")'), self.doc.index('Если СтатусНДС = "KNOWN" Тогда'))

    def test_vat_in_one_pass_with_gross_column(self) -> None:
        # Запуск KUDIR4_20261011_003: в четырёх актах НДС «сверху» (Дт 62 Кт 90 = Сумма + НДС), бухгалтер подтвердил, что
        # так и должно быть. Сумма строки к оплате - Всего; строка проверяется: Всего равно Сумма или Сумма + НДС.
        # Налога с продаж в базе нет: колонка НП не читается.
        self.assertEqual(self.vat.count("ДокТип.ВыбратьСтроки();"), 1)
        self.assertIn("КопВсего = КУДиР_Копейки(ЗнВсего);", self.vat)
        self.assertIn("(КопВсего <> КопСтр) И (КопВсего <> КопСтр + КопНДС)", self.vat)
        self.assertIn("ВОбл = ВОбл + КопВсего;", self.vat)
        self.assertIn("ВНеобл = ВНеобл + КопВсего;", self.vat)
        self.assertIn('ТипЗначенияСтр(ЗнНДС) <> "Число"', self.vat)
        self.assertNotRegex(self.vat, r"/\s*105|\*\s*5\b")
        self.assertNotIn(".НП", self.vat)
        columns = active(body(self.src, "КУДиР4_КолонкиВида"))
        self.assertEqual(re.findall(r'Нужные\.ДобавитьЗначение\("(\w+)"', columns), ["Сумма", "НДС", "Всего"])
        # Документы с НДС «сверху» перечисляются в журнале для сверки КП0.4 (их разбирает check_e04 sheet).
        self.assertIn('КУДиР_Лог("' + chk5.VAT_ON_TOP_LOG + '" + Описание', self.doc)

    def test_document_amount_from_gross_total_or_rows(self) -> None:
        # У счёта колонка Сумма без итога («Реквизит не имеет итога по колонке!»); если и итог Всего не читается -
        # сумма Всего по прочитанным строкам.
        self.assertNotIn('Итог("Сумма")', self.doc)
        self.assertIn('СуммаКоп = "" + КУДиР_Копейки(ДокТип.Итог("Всего"));', self.doc)
        self.assertIn("СуммаКоп = ВсегоСтрок;", self.doc)
        self.assertIn('Всего = "" + ВВсего;', self.vat)

    def test_settlement_kind_from_attribute_then_postings(self) -> None:
        # Реквизит исполнения - ВыбВидыРасчетовСПокупателями (имени ВидыРасчетовСПокупателями в конфигурации нет,
        # запуск KUDIR4_20261011_003). Пустой у проведённого - вид по проводкам 62.1 (№1 §15); у счёта не читается.
        guard = self.doc.index('Если Роль = "RECEIVABLE" Тогда')
        self.assertLess(guard, self.doc.index("КУДиР4_ВидРасчетовКандидата("))
        kind = active(body(self.src, "КУДиР4_ВидРасчетовКандидата"))
        self.assertNotIn(".ВидыРасчетовСПокупателями", kind)
        self.assertIn("ВидР = ДокТип.ВыбВидыРасчетовСПокупателями;", kind)
        self.assertIn('КУДиР4_ОшибкаВида(ВидДок, "ВидРасчетовID", ИдДок, "READ_ERROR"', kind)
        read = kind.index("ВидР = ДокТип.ВыбВидыРасчетовСПокупателями;")
        empty = kind.index("Если ПустоеЗначение(ВидР) = 1 Тогда")
        posted = kind.index('Если Пров = "1" Тогда')
        self.assertLess(read, empty)
        self.assertLess(empty, posted)
        self.assertLess(posted, kind.index("КУДиР4_ВидРасчетовПроводок("))
        search = active(body(self.src, "КУДиР4_ВидРасчетовПроводок"))
        self.assertIn("Опер.ВыбратьПроводки();", search)
        self.assertIn("Пока Опер.ПолучитьПроводку() = 1 Цикл", search)
        self.assertIn('= "62.1" Тогда', search)
        self.assertIn('ВидР = "";', search[search.index("Если Разных <> 1 Тогда"):])

    def test_unreadable_attributes_are_errors_not_zeros(self) -> None:
        self.assertIn('СуммаКоп = "";', self.doc)
        self.assertIn('"СуммаКоп", ИдДок, "READ_ERROR"', self.doc)
        columns = active(body(self.src, "КУДиР4_КолонкиВида"))
        self.assertIn("ДокТип.ВыгрузитьТабличнуюЧасть(таб);", columns)
        self.assertIn('"READ_ERROR"', columns)
        self.assertNotIn("Метаданные.", columns)

    def test_documents_registered_in_id_map_and_ordered(self) -> None:
        register = active(body(self.src, "КУДиР4_ЗарегистрироватьКандидата"))
        self.assertIn('КУДиР4_ЗапомнитьИд("DOCUMENT", ИдДок, Сист, ВидДок, НомДок, ДатаДок, Поз, "");', register)
        self.assertIn("КУДиР4_ЗапомнитьПозицию(Ключ, Поз, ИдДок);", register)
        self.assertIn('"ORDER_UNKNOWN"', register)
        self.assertIn('"ID_COLLISION"', register)

    def test_contract_id_by_owner(self) -> None:
        # Код договора уникален у владельца: ID по владельцу, чужой договор документа - ANALYTICS_UNKNOWN.
        analytics = active(body(self.src, "КУДиР4_АналитикаКандидата"))
        self.assertIn('КУДиР4_ЗапомнитьИд("CONTRACT", ИдДог, ЗначениеВСтрокуВнутр(Дог)', analytics)
        self.assertIn("ИдВладельца = КУДиР_ИдКонтрагента(Дог.Владелец);", analytics)
        self.assertIn('ИдДог = "D|" + ИдВладельца + "|" + СокрЛП(Дог.Код);', analytics)
        self.assertIn("КУДиР_СтрокиРавны(ИдВладельца, ИдКонтр) = 0", analytics)
        self.assertIn('"ANALYTICS_UNKNOWN", "договор документа "', analytics)


class ExportSnapshotsSourceTests(unittest.TestCase):
    """Э0.5: balance_snapshots.csv и ledger_quarters.csv по №1 §§23.1, 23.4, 27 в исходнике КУДиР4_."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8-sig")
        cls.day = active(body(cls.src, "КУДиР4_ОстаткиНаДень"))
        cls.before = active(body(cls.src, "КУДиР4_ОстаткиПередДокументом"))
        cls.general = active(body(cls.src, "КУДиР4_ОбщиеОстатки"))

    def test_window_starts_at_history_start(self) -> None:
        # Снимки и контроли строятся по всей области: окно, начатое позже 01.01.2026, не даёт входящих остатков.
        start = active(body(self.src, "КУДиР4_НачатьЗапуск"))
        self.assertIn("Если датанач <> Дата(2026, 1, 1) Тогда", start)
        self.assertLess(start.index("КУДиР4_ПодготовитьКварталы(датакон);"), start.index("КУДиР4_СоздатьКаталогЗапуска()"))
        quarters = active(body(self.src, "КУДиР4_ПодготовитьКварталы"))
        self.assertIn("НачКв = Дата(2026, 1, 1);", quarters)
        self.assertIn("Пока НачКв <= датакон Цикл", quarters)
        self.assertIn("НачКв = ДобавитьМесяц(НачКв, 3);", quarters)

    def test_day_start_query_form_verified_on_e01(self) -> None:
        # DAY_START: ВыполнитьЗапрос(День, День), СНД/СНК - остаток до всех движений дня (КП0.1); оба разреза одним
        # запросом, счёт-группа не выдаётся, вид расчётов не разворачивается.
        self.assertIn("БухИт.ИспользоватьСубконто(ВидыСубконто.Контрагенты, , 1);", self.day)
        self.assertIn("БухИт.ИспользоватьСубконто(ВидыСубконто.Договоры, , 1);", self.day)
        self.assertNotIn("ВидыРасчетовСПокупателями", self.day)
        self.assertIn("БухИт.ВключатьСубсчета(-1);", self.day)
        self.assertIn('БухИт.ВыполнитьЗапрос(День, День, "62") = 1', self.day)
        self.assertIn("КУДиР_Копейки(БухИт.СНД())", self.day)
        self.assertNotIn("СКД", self.day)

    def test_before_document_query_form_verified_on_e01(self) -> None:
        # BEFORE_DOCUMENT: конец периода на предыдущей позиции документа, СКД/СКК - сам документ не входит (КП0.1).
        self.assertIn("БухИт.ИспользоватьСубконто(ВидыСубконто.Контрагенты, Контр, 2);", self.before)
        self.assertIn('БухИт.ВыполнитьЗапрос(, Пред, "62") = 1', self.before)
        self.assertIn("КУДиР_Копейки(БухИт.СКД())", self.before)
        doc = active(body(self.src, "КУДиР4_СнимкиДокумента"))
        self.assertIn("Пред = СформироватьПозициюДокумента(ДокПоз, -1);", doc)
        # Позиция восстановленного документа сверяется с ledger62: остаток другой версии не выдаётся за снимок.
        self.assertLess(doc.index("КУДиР_СтрокиРавны(ПозСейчас, ДокПоз) = 0"), doc.index("Пред = СформироватьПозициюДокумента"))

    def test_contract_traversal_checks_customer_sum(self) -> None:
        # Внутри запроса сальдо покупателя на счёте сверяется с суммой договоров; расхождение - флаг "B" (WARNING
        # SNAPSHOT_MISMATCH при сохранённых остатках), непрочитанный договор - флаг "U".
        traversal = active(body(self.src, "КУДиР4_ДоговорыИтога"))
        self.assertIn("БухИт.ВыбратьСубконто(2);", traversal)
        self.assertIn("Дог = БухИт.Субконто(ВидыСубконто.Договоры);", traversal)
        self.assertIn("Если СуммаДог <> Дт - Кт Тогда", traversal)
        self.assertIn('КУДиР4_ФлагИтога("B", ИдКонтр)', traversal)
        self.assertIn('КУДиР4_ФлагИтога("U", ИдКонтр)', traversal)
        # Компоненты субсчетов складываются, строки итогов адресуются номером (текущая строка не используется).
        add = active(body(self.src, "КУДиР4_ДобавитьИтог"))
        self.assertIn('Итоги.УстановитьЗначение(Ном, "Дт", Итоги.ПолучитьЗначение(Ном, "Дт") + Дт);', add)

    def test_unknown_is_empty_not_zero(self) -> None:
        # Известный ноль - 0/0 только при выполненном запросе; неизвестный остаток - пусто/пусто UNKNOWN (№1 §§21, 23.1).
        for name in ("КУДиР4_СнимокПокупателя", "КУДиР4_СнимокДоговора"):
            text = active(body(self.src, name))
            self.assertLess(text.index('Дт = "";'), text.index("Если Известно = 1 Тогда"))
            self.assertLess(text.index('Статус = "UNKNOWN";'), text.index("Если Известно = 1 Тогда"))
            self.assertLess(text.index("Если Известно = 1 Тогда"), text.index('Дт = "0";'))
        day = active(body(self.src, "КУДиР4_СнимкиНаДень"))
        self.assertIn('"SNAPSHOT_UNKNOWN", "остатки на начало "', day)
        before = active(body(self.src, "КУДиР4_СнимкиПокупателяДо"))
        self.assertIn('"SNAPSHOT_UNKNOWN", "остаток перед документом "', before)

    def test_customer_mismatch_keeps_received_balances(self) -> None:
        # №1 §27: диагностика не заменяет факт. Сальдо покупателя, не равное сумме договоров, - полученные остатки:
        # строка остаётся KNOWN с числами, WARNING SNAPSHOT_MISMATCH; SNAPSHOT_UNKNOWN - только невыполненный запрос.
        customer = active(body(self.src, "КУДиР4_СнимокПокупателя"))
        self.assertNotIn("SNAPSHOT_UNKNOWN", customer)
        flag = customer.index('Флаг = КУДиР4_ФлагИтога("B", ИдКонтр);')
        self.assertLess(customer.index('Статус = "KNOWN";'), flag)
        self.assertLess(customer.index('Дт = "" + Итоги.ПолучитьЗначение(Ном, "Дт");'), flag)
        self.assertIn('КУДиР4_Предупреждение("balance_snapshots.csv", КУДиР4_КлючЗаписиСнимка(Вид, ДатаГр, ИдДок, "CUSTOMER", ИдКонтр, "ALL", ""), "ОстатокДтКоп", "SNAPSHOT_MISMATCH",', customer)
        self.assertNotIn('ФлагИтога("B"', active(body(self.src, "КУДиР4_СнимокДоговора")))

    def test_absent_pair_is_zero_only_when_every_contract_read(self) -> None:
        # Пары нет в результате: известный ноль, только если нет флага "U"; иначе непрочитанным мог быть именно этот
        # договор - UNKNOWN пусто/пусто с WARNING ANALYTICS_UNKNOWN на ключе строки, не KNOWN 0/0.
        pair = active(body(self.src, "КУДиР4_СнимокДоговора"))
        self.assertIn('Флаг = КУДиР4_ФлагИтога("U", ИдКонтр);', pair)
        found = pair.index("Если Ном > 0 Тогда")
        read_all = pair.index("ИначеЕсли ИндИтогов.Exists(Флаг) = 0 Тогда")
        unread = pair.index("Иначе\n", read_all)
        self.assertLess(found, read_all)
        self.assertLess(read_all, pair.index('Дт = "0";'))
        self.assertLess(pair.index('Дт = "0";'), unread)
        self.assertIn('КУДиР4_КлючЗаписиСнимка(Вид, ДатаГр, ИдДок, "CUSTOMER_CONTRACT", ИдКонтр, Сост, ИдДог), "Status", "ANALYTICS_UNKNOWN",', pair[unread:])
        self.assertNotIn('Статус = "KNOWN";', pair[unread:])

    def test_snapshot_row_in_header_order(self) -> None:
        row = active(body(self.src, "КУДиР4_СтрокаСнимка"))
        values = re.findall(r"сп\.ДобавитьЗначение\((.*)\);", row)
        self.assertEqual(values, ["Вид", "ДатаГр", "ИдДок", "Поз", "Разрез", "ИдКонтр", "ИдДог", "Сост", "Дт", "Кт", "Статус"])
        self.assertEqual(len(values), len(chk5.SNAPSHOTS_HEADER))
        key = active(body(self.src, "КУДиР4_КлючЗаписиСнимка"))
        self.assertEqual(re.findall(r"КУДиР4_СтрокаКлюча\((\w+)\)", key), ["Вид", "ДатаГр", "ИдДок", "Разрез", "ИдКонтр", "Сост", "ИдДог"])

    def test_required_analytics_from_ledger_documents_and_incoming(self) -> None:
        # №1 §23.1: покупатели/договоры входящих остатков, ledger и documents всей области, включая нулевые.
        side = active(body(self.src, "КУДиР4_СторонаДвижения"))
        self.assertIn('КУДиР4_АналитикаСнимков("ledger62.csv", ИдКонтр, ЗначениеВСтрокуВнутр(Контр), ИдДог);', side)
        self.assertIn("КУДиР4_УчестьКвартал(гл4ПрДатаОпер, ИдСтроки);", side)
        doc = active(body(self.src, "КУДиР4_ДокументКандидат"))
        self.assertIn('КУДиР4_АналитикаСнимков("documents.csv", ИдКонтр, "", ИдДог);', doc)
        day = active(body(self.src, "КУДиР4_СнимкиНаДень"))
        incoming = day.index("Если Первая = 1 Тогда")
        self.assertLess(incoming, day.index('КУДиР4_АналитикаСнимков("balance_snapshots.csv", ИдКонтр, "", ИдДог);'))
        self.assertLess(day.index('КУДиР4_АналитикаСнимков("balance_snapshots.csv"'), day.index('гл4СнКлючи.Сортировать("Контр,Дог");'))
        self.assertIn('"COVERAGE_INCOMPLETE",', day)
        self.assertLess(day.index('гл4СнКлючи.Сортировать("Контр,Дог");'), day.index("гл4СнКлючи.ВыбратьСтроки();"))

    def test_money_documents_from_written_ledger(self) -> None:
        # Документ с денежной корреспонденцией 62 (поступление, возврат, неразобранная группа UNKNOWN) - по
        # корреспондирующему счёту стороны в ledger62.csv; один снимок на документ и покупателя.
        walk = active(body(self.src, "КУДиР4_СнимкиПередДокументами"))
        self.assertIn('TS = КУДиР4_ОткрытьЧтение("ledger62.csv", КУДиР4_ЗаголовокДвижений());', walk)
        self.assertIn("Если КУДиР_ЭтоДенежныйСчет(Корр) = 1 Тогда", walk)
        self.assertNotIn("MovementRole", walk)
        self.assertIn("Если (Денежный = 1) И (СтрДлина(ДокИд) > 0) Тогда", walk)
        doc = active(body(self.src, "КУДиР4_СнимкиДокумента"))
        self.assertIn("Если Готовы.Exists(ИдКонтр) = 0 Тогда", doc)

    def test_general_controls_without_analytics(self) -> None:
        # Четыре общих контроля - без разрезов покупателей/договоров/видов расчётов (№1 §23.4).
        self.assertNotIn("ИспользоватьСубконто", self.general)
        self.assertIn("БухИт.ВключатьСубсчета(-1);", self.general)
        self.assertIn('БухИт.ВыполнитьЗапрос(НачКв, КонКв, "62") = 1', self.general)
        controls = active(body(self.src, "КУДиР4_КонтролиКварталов"))
        # Конец текущего квартала не запрашивается и не заменяется остатком на дату выгрузки.
        self.assertIn("Если КонКв <= датакон Тогда", controls)
        self.assertLess(controls.index('ЗакрДт = "";'), controls.index("ЗакрДт = \"\" + ДтК;"))
        guard = controls.index("Если Полный = 1 Тогда\n\t\t\t\tЗакрДт")
        self.assertLess(guard, controls.index("ЗакрДт = \"\" + ДтК;"))
        self.assertIn('"COVERAGE_INCOMPLETE", "текущий неполный квартал', controls)
        values = re.findall(r"сп\.ДобавитьЗначение\((.*)\);", controls)
        self.assertEqual(len(values), len(chk5.QUARTERS_HEADER))
        self.assertEqual(values[9:11], ['"REFRESHED"', "гл4ИдЗапуска"])

    def test_headless_run_only_with_marker(self) -> None:
        # Запуск без формы (харнесс 1С 7.7): только по списку-параметру с меткой; обычное открытие формы не меняется.
        import run_1c_e05 as runner
        opening = active(body(self.src, "ПриОткрытии"))
        self.assertIn('Если ТипЗначенияСтр(Пар) = "СписокЗначений" Тогда', opening)
        self.assertIn(f'Если ("" + Пар.ПолучитьЗначение(1)) = "{runner.MARKER}" Тогда', opening)
        self.assertIn(f'Параметры77.ДобавитьЗначение("{runner.MARKER}");', runner.CONTROLLER)
        headless = active(body(self.src, "КУДиР4_БезФормы"))
        self.assertLess(headless.index("СтатусВозврата(0);"), headless.index("КУДиР4_НаборЭ05("))
        version = re.match(r"// version: (\S+) ", self.src)[1]
        self.assertIn(f'Пар.ДобавитьЗначение("loaded:{version}");', headless)
        self.assertIn('Если ("" + Пар.ПолучитьЗначение(2)) = "e05" Тогда', headless)

    def test_out_of_scope_accounts_not_summed(self) -> None:
        for text in (self.day, self.before, self.general):
            self.assertIn("Если СтрДлина(КУДиР4_СубсчетОбласти(Счет)) = 0 Тогда", text)
            self.assertIn("КУДиР4_СчетВнеОбласти(", text)


if __name__ == "__main__":
    unittest.main()
