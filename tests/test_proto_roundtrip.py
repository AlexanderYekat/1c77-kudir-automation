"""Круговой обмен ID: то, что 1С положила в выгрузку, Python обязана вернуть без искажения."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kudir_proto.csv_io import (  # noqa: E402
    DOCUMENTS_FIELDS,
    KUDIR_RESULT_FIELDS,
    LEDGER_FIELDS,
    OPENING_FIELDS,
    PAYMENTS_FIELDS,
    read_kv,
    read_rows,
    write_kv,
    write_rows,
)
from kudir_proto.engine import ProtoError, run_directory  # noqa: E402


def _pay(**kwargs: str) -> dict[str, str]:
    row = {k: "" for k in PAYMENTS_FIELDS}
    row.update(kwargs)
    return row


def _doc(**kwargs: str) -> dict[str, str]:
    row = {k: "" for k in DOCUMENTS_FIELDS}
    row.update(kwargs)
    return row


def _led(**kwargs: str) -> dict[str, str]:
    row = {k: "" for k in LEDGER_FIELDS}
    row.update(kwargs)
    return row


def _open_row(**kwargs: str) -> dict[str, str]:
    row = {k: "" for k in OPENING_FIELDS}
    row.update(kwargs)
    return row


class RoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.scoring = ROOT / "config" / "scoring.yaml"
        write_kv(
            self.dir / "manifest.csv",
            {
                "run_id": "KUDIR_PROTO_TEST_001",
                "schema_version": "1",
                "encoding": "windows-1251",
                "history_start": "2025-10-01",
                "date_start": "2026-01-01",
                "date_end": "2026-01-31",
            },
        )
        write_rows(
            self.dir / "payments.csv",
            PAYMENTS_FIELDS,
            [
                _pay(
                    payment_id="BANK|Выписка-0296-2025-12-30|2",
                    is_target="1",
                    PaymentKind="BANK",
                    PaymentGranularity="ROW",
                    ДатаОперации="2026-01-06",
                    НомерСтрокиВДокументе="2",
                    ВидДокументаОплаты="Выписка",
                    НомерДокументаОплаты="0001",
                    ДатаДокументаОплаты="2026-01-06",
                    СуммаКоп="100000",
                    КонтрагентID="C|00001",
                    Контрагент="МЫЛОВА АГ",
                    ДоговорID="D|00001|243",
                    Договор="243",
                    НомерДоговора="243",
                )
            ],
        )
        write_rows(
            self.dir / "documents.csv",
            DOCUMENTS_FIELDS,
            [
                _doc(
                    document_id="DOC|Оказание услуг|Я-002374|2025-12-30",
                    DocumentRole="RECEIVABLE",
                    DocumentType="SERVICE_ACT",
                    ВидДокумента="Оказание услуг",
                    НомерДокумента="Я-002374",
                    ДатаДокумента="2025-12-30",
                    КонтрагентID="C|00001",
                    Контрагент="МЫЛОВА АГ",
                    ДоговорID="D|00001|243",
                    Договор="243",
                    СуммаКоп="550000",
                    Проведен="1",
                    ПомеченНаУдаление="0",
                )
            ],
        )
        write_rows(
            self.dir / "ledger62.csv",
            LEDGER_FIELDS,
            [
                _led(
                    ledger_event_id="L1",
                    source_document_id="DOC|Оказание услуг|Я-002374|2025-12-30",
                    ДатаОперации="2025-12-30",
                    СуммаКоп="550000",
                    Сторона62="D",
                    КонтрагентID="C|00001",
                    ДоговорID="D|00001|243",
                ),
                _led(
                    ledger_event_id="L2",
                    source_document_id="BANK|Выписка-0296-2025-12-30|2",
                    payment_id="BANK|Выписка-0296-2025-12-30|2",
                    ДатаОперации="2026-01-06",
                    СуммаКоп="100000",
                    Сторона62="K",
                    КонтрагентID="C|00001",
                    ДоговорID="D|00001|243",
                ),
            ],
        )
        write_rows(
            self.dir / "opening_balances.csv",
            OPENING_FIELDS,
            [
                _open_row(
                    КонтрагентID="C|00001",
                    Счет62="62.1",
                    ДоговорID="D|00001|243",
                    ДатаСальдо="2025-10-01",
                    ОстатокДтКоп="0",
                    ОстатокКтКоп="0",
                )
            ],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_result_ids_are_verbatim_from_export(self) -> None:
        status = run_directory(self.dir, self.scoring)
        self.assertEqual(status, "SUCCESS")
        rows = read_rows(self.dir / "kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["run_id"], "KUDIR_PROTO_TEST_001")
        self.assertEqual(row["payment_id"], "BANK|Выписка-0296-2025-12-30|2")
        self.assertEqual(row["contract_id"], "D|00001|243")
        self.assertEqual(row["matched_document_id"], "DOC|Оказание услуг|Я-002374|2025-12-30")
        self.assertEqual(row["amount_kopecks"], "100000")
        meta = read_kv(self.dir / "run_status.csv")
        self.assertEqual(meta["status"], "SUCCESS")

    def test_unknown_schema_is_failed_without_result(self) -> None:
        write_kv(
            self.dir / "manifest.csv",
            {"run_id": "X", "schema_version": "999", "encoding": "windows-1251"},
        )
        with self.assertRaises(ProtoError):
            run_directory(self.dir, self.scoring)
        self.assertFalse((self.dir / "kudir_result.csv").exists())


if __name__ == "__main__":
    unittest.main()
