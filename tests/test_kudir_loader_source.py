"""Этап 5: контракт загрузчика 1С по ТЗ №1 §24.

1С 7.7 в CI нет. Проверяем исходник `1cv77/kudir_export.txt` и Python-сторону:
сверка run_id, не читать объект после ошибки ID, tax_ready не блокирует matching.
Прототип `kudir_proto.txt` не является загрузчиком этапа 5.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "1cv77" / "kudir_export.txt"
PROTO = ROOT / "1cv77" / "kudir_proto.txt"
BAT = ROOT / "run_kudir.bat"
sys.path.insert(0, str(ROOT / "src"))

from kudir.engine import write_failed  # noqa: E402
from kudir_proto.csv_io import (  # noqa: E402
    KUDIR_RESULT_FIELDS,
    sanitize_kudir_content,
    write_kv,
    write_rows,
)


def _function_body(src: str, name: str) -> str:
    marker = f"Функция {name}("
    start = src.rindex(marker)
    return src[start:].split("КонецФункции", 1)[0]


class LoaderSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.src = EXPORT.read_text(encoding="utf-8")
        cls.proto = PROTO.read_text(encoding="utf-8")
        cls.bat = BAT.read_text(encoding="utf-8")
        cls.load = _function_body(cls.src, "КУДиР_ЗагрузитьИВосстановить")
        cls.proto_load = _function_body(cls.proto, "КУДиР_ЗагрузитьИВосстановить")

    def test_loader_is_not_the_proto(self) -> None:
        self.assertIn("этап5", self.src.splitlines()[0])
        self.assertIn("run_kudir.bat", self.src)
        self.assertNotIn("run_kudir_proto.bat", self.src)
        self.assertIn("ДокОпл.НомерДок", self.proto_load)
        proto_msg = self.proto_load.index("НЕ восстановлен ДокументОплаты")
        proto_num = self.proto_load.index("ДокОпл.НомерДок")
        self.assertGreater(proto_num, proto_msg)
        self.assertNotIn("Продолжить", self.proto_load[proto_msg:proto_num])

    def test_5_1_run_id_checked_before_load(self) -> None:
        self.assertIn("manifest.csv", self.load)
        self.assertIn('КУДиР_ЗначениеKV(маниф, "run_id")', self.load)
        self.assertIn('КУДиР_ЗначениеKV(стат, "run_id")', self.load)
        self.assertIn('КУДиР_ПроверитьКолонку(спЗаг, "run_id")', self.load)
        self.assertIn("не совпал с manifest", self.load)
        self.assertIn("Чужой результат не загружается", self.load)
        self.assertIn("\\exchange\\", self.src)
        self.assertIn("+ run_id", self.src)
        failed_return = self.load.index('Статус = "FAILED"')
        open_result = self.load.index("kudir_result.csv")
        self.assertLess(failed_return, open_result)

    def test_5_1_python_and_bat_drop_stale_kudir_result(self) -> None:
        self.assertIn("kudir_result.csv", self.bat)
        self.assertIn("del /q", self.bat)
        self.assertIn("python -m kudir", self.bat)
        tmp = Path(tempfile.mkdtemp(prefix="kudir_failed_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        write_kv(tmp / "manifest.csv", {"run_id": "KUDIR_TEST_001", "schema_version": "1"})
        write_rows(
            tmp / "kudir_result.csv",
            KUDIR_RESULT_FIELDS,
            [{"run_id": "OLD", "payment_id": "P1", "СодержаниеЗаписи": "старое"}],
        )
        write_failed(tmp, "boom")
        self.assertFalse((tmp / "kudir_result.csv").exists())
        status = (tmp / "run_status.csv").read_text(encoding="cp1251")
        self.assertIn("FAILED", status)
        self.assertIn("KUDIR_TEST_001", status)

    def test_5_2_do_not_read_object_after_id_miss(self) -> None:
        msg_pay = self.load.index("НЕ восстановлен ДокументОплаты")
        num_pay = self.load.index("ДокОпл.НомерДок")
        cont_pay = self.load.index("Продолжить", msg_pay)
        self.assertLess(cont_pay, num_pay)
        self.assertIn("ПустоеЗначение(ДокОпл)", self.load[:num_pay])

        msg_dog = self.load.index("НЕ восстановлен Договор")
        name_dog = self.load.index("Договор.Наименование")
        cont_dog = self.load.index("Продолжить", msg_dog)
        self.assertLess(cont_dog, name_dog)
        self.assertIn("ПустоеЗначение(Договор)", self.load[max(0, msg_dog - 200) : name_dog])
        self.assertIn("объект не читается", self.load)
        self.assertIn("ошибка инварианта", self.load)

    def test_5_3_physical_lines_tax_ready_does_not_block(self) -> None:
        self.assertIn("TS.ReadLine()", self.load)
        self.assertNotIn("логическ", self.load.lower())
        self.assertIn('КУДиР_ЗначениеKV(стат, "tax_ready")', self.load)
        tax_msg = self.load.index("КУДиР не готова")
        open_result = self.load.index("\\kudir_result.csv")
        self.assertLess(tax_msg, open_result)
        self.assertIn("Matching загружается", self.load)
        self.assertIn("цикл 2 не стартовать", self.load)
        self.assertIn("SUCCESS_DEGRADED", self.load)
        failed_block = self.load[
            self.load.index('Статус = "FAILED"') : self.load.index("SUCCESS_DEGRADED")
        ]
        self.assertIn("Возврат таб", failed_block)
        tax_block = self.load[tax_msg:open_result]
        self.assertNotIn("Возврат таб", tax_block)

    def test_5_3_python_content_has_no_crlf(self) -> None:
        self.assertEqual(
            sanitize_kudir_content("аванс\r\nвторая строка"),
            "аванс  вторая строка",
        )
        self.assertNotIn("\n", sanitize_kudir_content("a\nb"))
        self.assertNotIn("\r", sanitize_kudir_content("a\rb"))

    def test_required_columns_validated_before_rows(self) -> None:
        header_end = self.load.index("Ошибок = 0")
        header = self.load[:header_end]
        for name in KUDIR_RESULT_FIELDS:
            self.assertIn(f'КУДиР_ПроверитьКолонку(спЗаг, "{name}")', header)
        self.assertIn("нет обязательной колонки", self.src)
        self.assertIn("строка kudir_result короче заголовка", self.load)

    def test_id_error_discards_partial_table(self) -> None:
        err_block = self.load[self.load.index("Если Ошибок > 0") :]
        self.assertIn("Частичная таблица отброшена", err_block)
        self.assertIn("СформироватьТаблицуДоходовЧистовая()", err_block)
        self.assertNotIn("Возврат таб", err_block.split("КонецЕсли", 1)[0])


if __name__ == "__main__":
    unittest.main()
