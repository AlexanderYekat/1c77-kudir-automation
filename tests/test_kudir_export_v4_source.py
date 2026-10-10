"""Э0.2: инварианты исходника блока КУДиР4_ в 1cv77/kudir_export.txt (ТЗ №1 §§5-7, 23, 27, 30; ТЗ №4 §§5, 8, 13).

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


def body(src: str, name: str) -> str:
    for kind, end in (("Процедура", "КонецПроцедуры"), ("Функция", "КонецФункции")):
        match = re.search(rf"^{kind} {re.escape(name)}\(.*\)\s*$", src, re.M)
        if match:
            return src[match.start():].split(end, 1)[0]
    raise ValueError(name)


def active(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def returned_literal(src: str, name: str) -> str:
    return re.search(r'Возврат "([^"]*)";', body(src, name))[1]


class ExportV4SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8-sig")
        cls.v4 = active(cls.src[cls.src.index("Функция КУДиР_СтрокиРавны("):])

    def test_headers_match_contract_and_checker(self) -> None:
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокКарты").split(";"), chk.ID_MAP_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокДиаг").split(";"), chk.DIAG_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокИд").split(";"), chk.E02_IDS_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокДок").split(";"), chk.E02_DOCS_HEADER)
        self.assertEqual(returned_literal(self.src, "КУДиР4_ЗаголовокСамопроверки").split(";"), chk.SELFTEST_HEADER)

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
        walk = active(body(self.src, "КУДиР4_ОбойтиПроводки"))
        self.assertIn("ДокОбъект.НайтиДокумент(Опер.Документ)", walk)
        self.assertIn("Ссылка = ДокОбъект.ТекущийДокумент();", walk)
        self.assertNotIn("ЗначениеВСтрокуВнутр(Опер", self.v4)
        kind = active(body(self.src, "КУДиР4_ОбойтиВид"))
        self.assertIn("Ссылка = Выборка.ТекущийДокумент();", kind)
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
        side = active(body(self.src, "КУДиР4_СторонаПроводки"))
        self.assertIn('"LED|" + Ключ + "|" + НомПров + "|" + НомерСтороны', side)
        self.assertIn('"DOC|" + Ключ', side)

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
        stages = active(body(self.src, "КУДиР4_ЭтапыЭ02"))
        self.assertEqual(stages.count("Попытка"), stages.count("КУДиР4_Фатально(ОписаниеОшибки())"))
        self.assertLess(stages.index("КУДиР4_ЗакрытьКарту()"), stages.index("КУДиР4_ПроверитьОбратимость()"))

    def test_legacy_v2_button_disabled(self) -> None:
        legacy = active(body(self.src, "Сформировать"))
        self.assertNotIn("КУДиР_Выполнить", legacy)
        self.assertIn("КУДиР4_НаборЭ02(выбДатаНач, выбДатаКонец)", active(body(self.src, "ВыгрузкаЭ02")))


if __name__ == "__main__":
    unittest.main()
