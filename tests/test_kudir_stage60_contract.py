"""Контракт этапов 6.0 и 6.1–6.9: schema v2, поток CSV, L1/L2/L3.

Не ослаблять запреты FIFO / глМаксОплат / пустой Попытка.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "1cv77" / "kudir_export.txt"
sys.path.insert(0, str(ROOT / "src"))

from kudir_proto.csv_io import KUDIR_RESULT_FIELDS  # noqa: E402

VAT_AGGREGATES = (
    "СуммаОблагаемаяКоп",
    "СуммаНеоблагаемаяКоп",
    "СуммаНДСКоп",
)


def _code_body(src: str, name: str) -> str:
    for kind, end in (("Процедура", "КонецПроцедуры"), ("Функция", "КонецФункции")):
        marker = f"{kind} {name}("
        if marker in src:
            start = src.rindex(marker)
            return src[start:].split(end, 1)[0]
    raise ValueError(name)


def _function_body(src: str, name: str) -> str:
    marker = f"Функция {name}("
    start = src.rindex(marker)
    return src[start:].split("КонецФункции", 1)[0]


def _active(src: str) -> str:
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


class Stage60ExportContractTests(unittest.TestCase):
    """6.0.1: выгрузка под schema v2 и ТЗ №4."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8")

    def test_6_0_1_schema_version_2(self) -> None:
        write = _active(_code_body(self.src, "КУДиР_ЗаписатьВыгрузку"))
        self.assertTrue(
            "schema_version;2" in write,
            "manifest должен писать schema_version;2",
        )
        self.assertFalse(
            "schema_version;1" in write,
            "schema_version;1 больше не канон выгрузки",
        )

    def test_6_0_1_documents_vat_aggregates(self) -> None:
        hdr = _active(_code_body(self.src, "КУДиР_ЗаголовокDocuments"))
        add_doc = _active(_code_body(self.src, "КУДиР_ДобавитьДокумент"))
        for col in VAT_AGGREGATES:
            with self.subTest(col=col):
                self.assertTrue(col in hdr, f"нет колонки {col} в заголовке documents.csv")
                self.assertTrue(col in add_doc, f"нет агрегата {col} в обходе документа")
        self.assertTrue("ВыбратьСтроки" in add_doc, "нужен обход табличной части по НДС")
        self.assertTrue("ПолучитьСтроку" in add_doc, "нужен обход табличной части по НДС")

    def test_6_0_1_internal_string_reference(self) -> None:
        remember = _active(_code_body(self.src, "КУДиР_ЗапомнитьID"))
        self.assertTrue("ЗначениеВСтрокуВнутр" in remember, "ссылка_1С = ЗначениеВСтрокуВнутр")
        self.assertFalse(
            "СокрЛП(глТаблID.ссылка)" in remember,
            "СокрЛП системной ссылки в id_map запрещён",
        )
        self.assertFalse("СокрЛП(ЗначениеВСтрокуВнутр" in remember)

    def test_6_0_1_id_map_only_payment_document_contract(self) -> None:
        active = _active(self.src)
        self.assertFalse(
            'КУДиР_ЗапомнитьID("COUNTERPARTY"' in active,
            "COUNTERPARTY не пишется в id_map",
        )
        self.assertFalse(
            'КУДиР_ЗапомнитьID("LEDGER"' in active,
            "LEDGER не пишется в id_map",
        )
        self.assertTrue('КУДиР_ЗапомнитьID("PAYMENT"' in active)
        self.assertTrue('КУДиР_ЗапомнитьID("DOCUMENT"' in active)
        self.assertTrue('КУДиР_ЗапомнитьID("CONTRACT"' in active)

    def test_6_0_1_no_full_csv_text_for_ledger_or_id_map(self) -> None:
        active = _active(self.src)
        self.assertFalse("Текст = Текст" in active, "полный CSV в переменной запрещён")
        write = _active(_code_body(self.src, "КУДиР_ЗаписатьВыгрузку"))
        self.assertTrue("WriteLine" in write, "нужен потоковый TextStream.WriteLine")
        self.assertTrue("WriteLine" in _active(_code_body(self.src, "КУДиР_ПисатьCSVСтроку")))
        self.assertTrue("WriteLine" in _active(_code_body(self.src, "КУДиР_ПисатьIdMapСтроку")))

    def test_6_3_pass_a_before_run_id(self) -> None:
        execute = _active(_code_body(self.src, "КУДиР_Выполнить"))
        self.assertLess(
            execute.index("КУДиР_СобратьКлиентов"),
            execute.index("КУДиР_НовыйRunId"),
        )
        self.assertLess(
            execute.index("КУДиР_ОсвободитьЭкспорт()"),
            execute.index("КУДиР_ЗапуститьPython"),
        )
        self.assertGreater(
            execute.rindex("КУДиР_ОсвободитьЭкспорт()"),
            execute.index("КУДиР_СобратьСальдо"),
        )

    def test_6_0_3_bans_fifo_cap_silent_try_vat_second_pass(self) -> None:
        active = _active(self.src)
        self.assertNotIn("ПогаситьДолгиFIFO", active)
        self.assertNotIn("глМаксОплат", active)
        self.assertNotIn("Попытка", active)
        self.assertNotIn("document_lines.csv", active)
        collect = _active(_code_body(self.src, "КУДиР_СобратьДокументы"))
        self.assertNotIn("за НДС", collect.lower())
        self.assertEqual(collect.count("КУДиР_ОбойтиВидДокумента"), 3)


class Stage60LoaderContractTests(unittest.TestCase):
    """6.0.2 + 5.9.5 A5 + 5.9.6 A6. Должны быть зелёными после 2026.09.05-02."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8")
        cls.load = _function_body(cls.src, "КУДиР_ЗагрузитьИВосстановить")
        cls.load_active = _active(cls.load)

    def test_6_0_2_restore_via_internal_string_not_id_table(self) -> None:
        self.assertTrue(
            "ЗначениеИзСтрокиВнутр" in self.load_active,
            "L3 восстанавливает ЗначениеИзСтрокиВнутр",
        )
        self.assertFalse(
            "КУДиР_НайтиСсылку" in self.load_active,
            "глТаблID не способ восстановить объект после Python",
        )

    def test_6_0_2_two_passes_result_one_pass_id_map(self) -> None:
        self.assertTrue("restore" in self.load_active, "узкий restore после Python")
        self.assertTrue("id_map.csv" in self.load_active, "L2 читает id_map.csv")
        self.assertFalse(
            "цикл 2 не стартовать" in self.load_active,
            "сообщение про цикл 2 устарело",
        )

    def test_6_0_2_tax_ready_zero_still_loads(self) -> None:
        tax_msg = self.load.index("КУДиР не готова")
        open_result = self.load.index("\\kudir_result.csv")
        tax_block = self.load[tax_msg:open_result]
        self.assertNotIn("Возврат таб", tax_block)

    def test_5_9_5_numeric_error_discards_whole_table(self) -> None:
        self.assertTrue(
            "глОшибка" in self.load_active,
            "загрузчик обязан смотреть глОшибка до возврата таблицы",
        )
        kop = _active(_function_body(self.src, "КУДиР_КопейкиВРубли"))
        self.assertFalse("Возврат 0" in kop, "пустое число ≠ подстановка 0")

    def test_5_9_6_logical_csv_record_and_type_check(self) -> None:
        active = _active(self.src)
        lowered = active.lower()
        self.assertTrue(
            ("логическ" in lowered) or ("прочитатьлогическ" in lowered),
            "L2 должен собирать логическую CSV-запись, не один ReadLine",
        )
        self.assertTrue(
            "ЗначениеИзСтрокиВнутр" in active,
            "после десериализации нужна проверка типа объекта",
        )

    def test_6_0_3_required_result_columns_still_validated(self) -> None:
        header_end = self.load.index("Ошибок = 0")
        header = self.load[:header_end]
        for name in KUDIR_RESULT_FIELDS:
            self.assertIn(f'КУДиР_ПроверитьКолонку(спЗаг, "{name}")', header)


if __name__ == "__main__":
    unittest.main()
