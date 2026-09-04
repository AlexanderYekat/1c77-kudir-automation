"""Этап 4.6: аудитные исправления. Архитектуру matcher не переписывает."""

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
from kudir.legacy_parser_adapter import LegacyParserAdapter  # noqa: E402
from kudir.scoring import ScoreBreakdown, ScoringConfig, load_scoring, pick_high  # noqa: E402
from kudir_proto.csv_io import read_kv  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix=f"kudir_46_{name}_"))
    for fname in (
        "manifest.csv",
        "payments.csv",
        "ledger62.csv",
        "documents.csv",
        "opening_balances.csv",
    ):
        shutil.copy(src / fname, tmp / fname)
    return tmp


class ChronologyTieBreakTests(unittest.TestCase):
    def test_chronology_does_not_create_high(self) -> None:
        cfg = load_scoring(SCORING)
        a = ScoreBreakdown(amount=100, direct_reference=30)
        b = ScoreBreakdown(amount=100)
        self.assertEqual(a.selection_total, 130)
        self.assertLess(a.selection_total, cfg.c("high_min_score"))
        picked = pick_high([("a", a), ("b", b)], [0, 10], cfg)
        self.assertIsNone(picked)
        self.assertEqual(a.chronology, 0)

    def test_tied_group_separated_from_rest_uses_nearest(self) -> None:
        cfg = load_scoring(SCORING)
        a = ScoreBreakdown(invoice_link=180)
        b = ScoreBreakdown(invoice_link=180)
        c = ScoreBreakdown(amount=100)
        picked = pick_high([("a", a), ("b", b), ("c", c)], [10, 0, 50], cfg)
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked[0], "b")
        self.assertGreater(b.chronology, a.chronology)

    def test_two_equal_without_rest_are_not_high(self) -> None:
        cfg = load_scoring(SCORING)
        a = ScoreBreakdown(amount=120, contract=40, calculation_type=35)
        b = ScoreBreakdown(amount=120, contract=40, calculation_type=35)
        picked = pick_high([("a", a), ("b", b)], [0, 10], cfg)
        self.assertIsNone(picked)


class CashBaseAndDirectRefTests(unittest.TestCase):
    def test_invoice_base_is_not_direct_receivable(self) -> None:
        link = parse_base_link("Введен на основании: Счет №123 от 01.02.2026")
        self.assertIsNotNone(link)
        assert link is not None
        self.assertTrue(link.is_invoice())
        self.assertIsNone(link.receivable_kind())

    def test_shipment_base_is_goods(self) -> None:
        link = parse_base_link(
            "Введен на основании: Отгрузка товаров № Н00570 от 30 Июня 2026 г."
        )
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(link.receivable_kind(), "GOODS")
        self.assertEqual(link.base_date, "2026-06-30")


class InputInvariantTests(unittest.TestCase):
    def test_duplicate_payment_id_is_failed(self) -> None:
        tmp = _copy_golden("success_tax_not_ready")
        self.addCleanup(shutil.rmtree, tmp, True)
        pay = tmp / "payments.csv"
        text = pay.read_bytes()
        pay.write_bytes(text + text.split(b"\n")[1] + b"\n")
        with self.assertRaises(RunError) as ctx:
            run_directory(tmp, SCORING, parser=_UnavailableParser())
        self.assertIn("дубликат", str(ctx.exception))
        self.assertFalse((tmp / "kudir_result.csv").exists())

    def test_payment_ledger_sum_mismatch_is_failed(self) -> None:
        tmp = _copy_golden("success_tax_not_ready")
        self.addCleanup(shutil.rmtree, tmp, True)
        pay = tmp / "payments.csv"
        raw = pay.read_text(encoding="cp1251")
        pay.write_text(raw.replace(";1000000;", ";900000;", 1), encoding="cp1251")
        with self.assertRaises(RunError) as ctx:
            run_directory(tmp, SCORING, parser=_UnavailableParser())
        self.assertIn("ledger62", str(ctx.exception))


class CapSearchTests(unittest.TestCase):
    def test_capped_search_does_not_invent_high(self) -> None:
        cfg = load_scoring(SCORING)
        tiny = ScoringConfig(
            weights=cfg.weights,
            confidence=cfg.confidence,
            search={**cfg.search, "max_candidates": 1},
            schema_version=cfg.schema_version,
        )
        best = ScoreBreakdown(amount=120, contract=40)
        second = ScoreBreakdown(amount=120, calculation_type=35)
        from kudir.combinations import cap_candidates

        scored = [("a", best), ("b", second)]
        capped_list, capped = cap_candidates(scored, tiny)
        self.assertTrue(capped)
        self.assertEqual(len(capped_list), 1)
        distances = [0]
        picked = pick_high(capped_list, distances, tiny)
        self.assertIsNotNone(picked)
        full = pick_high(scored, [0, 1], cfg)
        self.assertIsNone(full)


class HistoricalStatusTests(unittest.TestCase):
    def test_historical_unresolved_writes_state_uncertain(self) -> None:
        src = GOLDEN / "historical_unresolved_blocks_tax"
        if not src.is_dir():
            self.skipTest("golden ещё не сгенерирован")
        tmp = _copy_golden("historical_unresolved_blocks_tax")
        self.addCleanup(shutil.rmtree, tmp, True)
        status = run_directory(tmp, SCORING, parser=_UnavailableParser())
        self.assertIn(status, ("SUCCESS", "SUCCESS_DEGRADED"))
        st = read_kv(tmp / "run_status.csv")
        self.assertEqual(st["state_uncertain"], "1")
        self.assertEqual(st["tax_ready"], "0")
        log = (tmp / "analysis.log").read_text(encoding="utf-8")
        self.assertIn("HISTORICAL_UNRESOLVED", log)


if __name__ == "__main__":
    unittest.main()
