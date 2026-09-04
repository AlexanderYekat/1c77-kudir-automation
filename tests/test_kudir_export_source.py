"""Этап 3: контракт выгрузки 1С по ТЗ №1. Matcher не вызывается.

1С 7.7 в CI нет. Проверяем исходник `1cv77/kudir_export.txt`:
настоящее сальдо, периоды, два обхода, все 62, ID по типам, каталог запуска.
Прототип `kudir_proto.txt` не является выгрузкой этапа 3.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "1cv77" / "kudir_export.txt"
PROTO = ROOT / "1cv77" / "kudir_proto.txt"
sys.path.insert(0, str(ROOT / "src"))

from kudir_proto.csv_io import (  # noqa: E402
    DOCUMENTS_FIELDS,
    ID_MAP_FIELDS,
    LEDGER_FIELDS,
    MANIFEST_KEYS,
    OPENING_FIELDS,
    PAYMENTS_FIELDS,
)


def _code_body(src: str, name: str) -> str:
    for kind, end in (("Процедура", "КонецПроцедуры"), ("Функция", "КонецФункции")):
        marker = f"{kind} {name}("
        if marker in src:
            start = src.rindex(marker)
            return src[start:].split(end, 1)[0]
    raise ValueError(name)


class ExportSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8")
        cls.proto = PROTO.read_text(encoding="utf-8")

    def test_export_module_exists_and_is_not_the_proto(self) -> None:
        self.assertTrue(EXPORT.is_file())
        self.assertIn("kudir_export.txt", self.proto)
        self.assertNotIn("глМаксОплат", self.src)
        self.assertNotIn("ПогаситьДолгиFIFO", self.src)

    def test_3_1_real_opening_balances_no_dummy_62_1(self) -> None:
        self.assertIn("БухгалтерскиеИтоги", self.src)
        self.assertIn("СНД()", self.src)
        self.assertIn("СНК()", self.src)
        self.assertIn("ВидыСубконто.Контрагенты", self.src)
        self.assertIn("ВидыСубконто.Договоры", self.src)
        self.assertNotIn('таблСальдо.Счет62 = "62.1"', self.src)
        self.assertNotIn("чтобы файл был", self.src)

    def test_3_2_periods_history_target_horizon_no_payment_cap(self) -> None:
        self.assertIn("МесяцевИстории", self.src)
        self.assertIn("МесяцевГоризонта", self.src)
        self.assertIn("НачалоГода", self.src)
        self.assertIn("is_target", self.src)
        self.assertIn("match_horizon_end", self.src)
        self.assertIn("quarter_end", self.src)
        self.assertIn("MatchHorizonEnd", self.src)
        self.assertNotIn("глМаксОплат = 20", self.src)
        for key in MANIFEST_KEYS:
            self.assertIn(key, self.src)

    def test_3_3_documents_walked_independently_of_62_90(self) -> None:
        self.assertIn("ВыбратьДокументы", self.src)
        self.assertIn('"Документ."', self.src)
        self.assertIn('"Счет"', self.src)
        self.assertIn('"ОтгрузкаТоваров"', self.src)
        self.assertIn('"ОказаниеУслуг"', self.src)
        self.assertIn("ЖурналДокументов", self.src)
        collect_docs = _code_body(self.src, "КУДиР_СобратьДокументы")
        self.assertNotIn("62,90", collect_docs)
        self.assertNotIn("ВыбратьОперацииСПроводками", collect_docs)

    def test_3_4_all_62_movements_never_deduped_by_document_id(self) -> None:
        self.assertIn("62,*;*,62;", self.src)
        ledger = _code_body(self.src, "КУДиР_СобратьLedger")
        self.assertNotIn("ЕстьИдВТаблице(таблДокументы", ledger)
        self.assertNotIn("document_id уже есть", ledger)
        add_row = _code_body(self.src, "КУДиР_ДобавитьСтрокуLedger")
        self.assertNotIn("Продолжить", add_row)

    def test_3_5_ids_by_entity_and_run_directory(self) -> None:
        self.assertIn("DOC|", self.src)
        self.assertIn("BANK|", self.src)
        self.assertIn("CASH|", self.src)
        self.assertIn("LED|", self.src)
        self.assertIn("ПолучитьПозицию()", self.src)
        self.assertIn("НомерПроводки()", self.src)
        self.assertIn("\\exchange\\", self.src)
        self.assertIn("+ run_id", self.src)
        self.assertIn("id_map.csv", self.src)
        self.assertIn("Коллизия ID", self.src)
        self.assertNotIn("двусмысленный ID", self.src)
        self.assertNotIn("C:\\Users\\Enduro", self.src)
        remember = _code_body(self.src, "КУДиР_ЗапомнитьID")
        self.assertIn("НайтиСсылку", remember)
        self.assertNotIn("НайтиИдПоСсылке", remember)
        self.assertIn("глОшибка", self.src)
        self.assertIn(";".join(ID_MAP_FIELDS), self.src)
        self.assertNotIn('Возврат преф + "999"', self.src)
        self.assertIn("нет свободного run_id", self.src)

    def test_csv_headers_match_schema_v1(self) -> None:
        self.assertIn(";".join(PAYMENTS_FIELDS), self.src)
        self.assertIn(";".join(LEDGER_FIELDS), self.src)
        self.assertIn(";".join(DOCUMENTS_FIELDS), self.src)
        self.assertIn(";".join(OPENING_FIELDS), self.src)
        self.assertIn('НоваяКолонка("НомерПроводкиВДокументе")', self.src)

    def test_documents_vid_raschetov_not_hardcoded_empty(self) -> None:
        add_doc = _code_body(self.src, "КУДиР_ДобавитьДокумент")
        self.assertNotIn('ВидРасчетовID = ""', add_doc)
        self.assertIn("КУДиР_ВидРасчетовРеквизитДок", add_doc)
        self.assertIn("КУДиР_ВидРасчетовИзПроводок62", add_doc)
        self.assertIn("проведённой реализации", add_doc)
        self.assertIn("НайтиОперацию", self.src)
        self.assertIn("КоличествоПроводок", self.src)

    def test_no_silent_try_except(self) -> None:
        code = "\n".join(
            line for line in self.src.splitlines() if not line.lstrip().startswith("//")
        )
        self.assertNotIn("Попытка", code)
        self.assertNotIn("Исключение", code)
        self.assertNotIn("КонецПопытки", code)
        walk = _code_body(self.src, "КУДиР_ОбойтиВидДокумента")
        self.assertIn("в конфигурации отсутствует", walk)
        amount = _code_body(self.src, "КУДиР_СуммаДокументаКоп")
        self.assertIn("нет реквизита суммы", amount)
        self.assertNotIn('Возврат "0"', amount)
        conducted = _code_body(self.src, "КУДиР_ПроведенДок")
        self.assertIn("Док.Проведен()", conducted)
        self.assertNotIn("Исключение", conducted)

    def test_optional_comment_is_not_failed(self) -> None:
        comment = _code_body(self.src, "КУДиР_КомментарийДок")
        self.assertIn('Возврат ""', comment)
        self.assertNotIn("КУДиР_Сбой", comment)

    def test_missing_proto_root_is_failed(self) -> None:
        init_paths = _code_body(self.src, "КУДиР_ИнициализироватьПути")
        self.assertIn("KUDIR_PROTO_ROOT", init_paths)
        self.assertIn("КУДиР_Сбой", init_paths)
        self.assertNotIn("C:\\Users\\Enduro", init_paths)
        execute = _code_body(self.src, "КУДиР_Выполнить")
        self.assertLess(execute.index("глОшибка = 0"), execute.index("КУДиР_ИнициализироватьПути"))


if __name__ == "__main__":
    unittest.main()
