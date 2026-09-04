"""Этап 4.6: аудитные исправления. Архитектуру не переписывает."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kudir.base_document_links import parse_base_link  # noqa: E402
from kudir.engine import RunError, run_directory  # noqa: E402
from kudir.kudir import is_goods, is_service  # noqa: E402
from kudir.legacy_parser_adapter import LegacyParserAdapter  # noqa: E402
from kudir.loaders import LoadError, load_exchange  # noqa: E402
from kudir_proto.csv_io import (  # noqa: E402
    DOCUMENTS_FIELDS,
    PAYMENTS_FIELDS,
    read_rows,
    write_rows,
)

SCORING = ROOT / "config" / "scoring.yaml"
GOLDEN = ROOT / "tests" / "golden"


class _UnavailableParser(LegacyParserAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.available = False
        self._probed = True

    def probe(self) -> bool:
        return False


def _copy_golden(name: str) -> Path:
    src = GOLDEN / name
    tmp = Path(tempfile.mkdtemp(prefix=f"kudir_{name}_"))
    for fname in (
        "manifest.csv",
        "payments.csv",
        "ledger62.csv",
        "documents.csv",
        "opening_balances.csv",
    ):
        shutil.copy(src / fname, tmp / fname)
    return tmp


class DirectRefTypingTests(unittest.TestCase):
    def test_service_ref_does_not_match_goods_number(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        pays = read_rows(tmp / "payments.csv", PAYMENTS_FIELDS)
        docs = read_rows(tmp / "documents.csv", DOCUMENTS_FIELDS)
        self.assertTrue(pays and docs)
        pays[0]["НазначениеПлатежа"] = "Оплата по акту №25 от 10.08.2026"
        docs[0]["НомерДокумента"] = "25"
        docs[0]["ДатаДокумента"] = "2026-01-12"
        docs[0]["DocumentType"] = "GOODS_SHIPMENT"
        docs[0]["ВидДокумента"] = "Отгрузка товаров"
        write_rows(tmp / "payments.csv", PAYMENTS_FIELDS, pays)
        extra = dict(docs[0])
        extra["document_id"] = "DOC|0000000999"
        extra["НомерДокумента"] = "99"
        extra["ПозицияДокумента"] = "0000000999"
        write_rows(tmp / "documents.csv", DOCUMENTS_FIELDS, [docs[0], extra])
        status = run_directory(tmp, SCORING, parser=_UnavailableParser())
        self.assertIn(status, ("SUCCESS", "SUCCESS_DEGRADED"))
        rows = read_rows(tmp / "kudir_result.csv")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["row_type"], "DEBT_UNRESOLVED")
        self.assertNotEqual(rows[0]["match_type"], "DIRECT_DOCUMENT_EXACT")

    def test_cash_invoice_base_is_not_direct_document(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        pays = read_rows(tmp / "payments.csv", PAYMENTS_FIELDS)
        docs = read_rows(tmp / "documents.csv", DOCUMENTS_FIELDS)
        pays[0]["PaymentKind"] = "CASH"
        pays[0]["КомментарийДокументаОплаты"] = "Введен на основании: Счет на оплату №123 от 01.01.2026"
        pays[0]["НазначениеПлатежа"] = ""
        docs[0]["НомерДокумента"] = "123"
        docs[0]["ДатаДокумента"] = "2026-01-12"
        docs[0]["Комментарий"] = ""
        extra = dict(docs[0])
        extra["document_id"] = "DOC|0000000999"
        extra["НомерДокумента"] = "999"
        extra["ПозицияДокумента"] = "0000000999"
        write_rows(tmp / "payments.csv", PAYMENTS_FIELDS, pays)
        write_rows(tmp / "documents.csv", DOCUMENTS_FIELDS, [docs[0], extra])
        run_directory(tmp, SCORING, parser=_UnavailableParser())
        rows = read_rows(tmp / "kudir_result.csv")
        self.assertNotIn(rows[0]["match_type"], ("CASH_BASE_EXACT", "DIRECT_DOCUMENT_EXACT"))

    def test_cash_goods_base_uses_type_number_date(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        pays = read_rows(tmp / "payments.csv", PAYMENTS_FIELDS)
        docs = read_rows(tmp / "documents.csv", DOCUMENTS_FIELDS)
        pays[0]["PaymentKind"] = "CASH"
        pays[0]["КомментарийДокументаОплаты"] = (
            "Введен на основании: Отгрузка товаров №Н00570 от 12 Января 2026 г."
        )
        docs[0]["НомерДокумента"] = "Н00570"
        docs[0]["ДатаДокумента"] = "2026-01-12"
        docs[0]["DocumentType"] = "GOODS_SHIPMENT"
        docs[0]["ВидДокумента"] = "Отгрузка товаров"
        write_rows(tmp / "payments.csv", PAYMENTS_FIELDS, pays)
        write_rows(tmp / "documents.csv", DOCUMENTS_FIELDS, docs)
        run_directory(tmp, SCORING, parser=_UnavailableParser())
        rows = read_rows(tmp / "kudir_result.csv")
        self.assertEqual(rows[0]["row_type"], "DEBT")
        self.assertEqual(rows[0]["match_type"], "CASH_BASE_EXACT")

    def test_invoice_base_kind_is_none(self) -> None:
        link = parse_base_link("Введен на основании: Счет на оплату №123 от 01.01.2026")
        self.assertIsNotNone(link)
        assert link is not None
        self.assertTrue(link.is_invoice())
        self.assertIsNone(link.receivable_kind())

    def test_goods_and_service_helpers(self) -> None:
        self.assertTrue(is_goods({"DocumentType": "GOODS_SHIPMENT", "ВидДокумента": "Отгрузка товаров"}))
        self.assertFalse(is_service({"DocumentType": "GOODS_SHIPMENT", "ВидДокумента": "Отгрузка товаров"}))
        self.assertTrue(is_service({"DocumentType": "SERVICE", "ВидДокумента": "Акт"}))
        self.assertFalse(is_goods({"DocumentType": "SERVICE", "ВидДокумента": "Акт"}))


class CsvInvariantTests(unittest.TestCase):
    def test_duplicate_payment_id_is_failed(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        pays = read_rows(tmp / "payments.csv", PAYMENTS_FIELDS)
        pays.append(dict(pays[0]))
        write_rows(tmp / "payments.csv", PAYMENTS_FIELDS, pays)
        with self.assertRaises(RunError):
            run_directory(tmp, SCORING, parser=_UnavailableParser())
        self.assertFalse((tmp / "kudir_result.csv").exists())

    def test_payment_ledger_sum_mismatch_is_failed(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        pays = read_rows(tmp / "payments.csv", PAYMENTS_FIELDS)
        pays[0]["СуммаКоп"] = "10000000"
        write_rows(tmp / "payments.csv", PAYMENTS_FIELDS, pays)
        with self.assertRaises(RunError):
            run_directory(tmp, SCORING, parser=_UnavailableParser())

    def test_document_after_horizon_is_failed(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        docs = read_rows(tmp / "documents.csv", DOCUMENTS_FIELDS)
        docs[0]["ДатаДокумента"] = "2027-01-01"
        write_rows(tmp / "documents.csv", DOCUMENTS_FIELDS, docs)
        with self.assertRaises(RunError):
            run_directory(tmp, SCORING, parser=_UnavailableParser())

    def test_load_exchange_rejects_bad_flag(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        pays = read_rows(tmp / "payments.csv", PAYMENTS_FIELDS)
        pays[0]["is_target"] = "yes"
        write_rows(tmp / "payments.csv", PAYMENTS_FIELDS, pays)
        with self.assertRaises(LoadError):
            load_exchange(tmp)


class SearchCapTests(unittest.TestCase):
    def test_capped_search_is_unresolved_not_heuristic_high(self) -> None:
        tmp = _copy_golden("posting_numeric_order")
        self.addCleanup(shutil.rmtree, tmp, True)
        docs = read_rows(tmp / "documents.csv", DOCUMENTS_FIELDS)
        base = dict(docs[0])
        extra = []
        for i in range(9):
            row = dict(base)
            row["document_id"] = f"DOC|{i:010d}"
            row["НомерДокумента"] = f"X-{i}"
            row["ПозицияДокумента"] = f"{i:010d}"
            extra.append(row)
        write_rows(tmp / "documents.csv", DOCUMENTS_FIELDS, extra)
        status = run_directory(tmp, SCORING, parser=_UnavailableParser())
        self.assertIn(status, ("SUCCESS", "SUCCESS_DEGRADED"))
        rows = read_rows(tmp / "kudir_result.csv")
        self.assertEqual(rows[0]["row_type"], "DEBT_UNRESOLVED")
        log = (tmp / "analysis.log").read_text(encoding="utf-8")
        self.assertIn("SEARCH_CAP", log)


class TimeoutDeadlineTests(unittest.TestCase):
    def test_deadline_is_reused_per_counterparty(self) -> None:
        from kudir.diagnostics import AnalysisLog
        from kudir.loaders import ExchangeBundle
        from kudir.matcher import Engine
        from kudir.scoring import load_scoring

        cfg = load_scoring(SCORING)
        bundle = ExchangeBundle(
            directory=Path("."),
            manifest={},
            payments=[],
            ledger=[],
            documents=[],
            openings=[],
        )
        engine = Engine(bundle=bundle, cfg=cfg, log=AnalysisLog())
        first = engine._timeout_deadline("C1")
        second = engine._timeout_deadline("C1")
        other = engine._timeout_deadline("C2")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)


if __name__ == "__main__":
    unittest.main()
