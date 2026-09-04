"""Golden 2.1–2.9: matcher, не только схема."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kudir.engine import RunError, run_directory  # noqa: E402
from kudir.legacy_parser_adapter import LegacyParserAdapter  # noqa: E402
from kudir.ledger import Ledger  # noqa: E402
from kudir.scoring import load_scoring  # noqa: E402
from kudir_proto.csv_io import (  # noqa: E402
    KUDIR_RESULT_FIELDS,
    MATCHES_FIELDS,
    RUN_STATUS_KEYS,
    UNRESOLVED_FIELDS,
    read_kv,
    read_rows,
)

GOLDEN = ROOT / "tests" / "golden"
SCORING = ROOT / "config" / "scoring.yaml"
INPUTS = (
    "manifest.csv",
    "payments.csv",
    "ledger62.csv",
    "documents.csv",
    "opening_balances.csv",
)
CASES = [
    "advance_then_later_payment",
    "multi_analytics_split",
    "no_cross_analytics_netting",
    "december_advance_january_shipment",
    "pre_vat_advance_post_vat_shipment",
    "unattributed_62_movement",
    "success_tax_not_ready",
    "ambiguous_equal_shipments",
    "bank_multi_correspondence",
    "partial_advance_split",
    "same_analytics_two_correspondences",
    "posting_numeric_order",
    "advance_other_contract_then_later_payment",
    "historical_unresolved_blocks_tax",
]


class _UnavailableParser(LegacyParserAdapter):
    """Golden hermetic: не ходить на живой :8765."""

    def __init__(self) -> None:
        super().__init__()
        self.available = False
        self._probed = True

    def probe(self) -> bool:
        return False


class LedgerSplitTests(unittest.TestCase):
    def test_debt_before_per_analytics_no_netting(self) -> None:
        led = Ledger()
        a = ("C1", "62.1", "DA", "V1")
        b = ("C1", "62.1", "DB", "V1")
        led.apply_opening(
            {
                "КонтрагентID": "C1",
                "Счет62": "62.1",
                "ДоговорID": "DA",
                "ВидРасчетовID": "V1",
                "ОстатокДтКоп": "1000000",
                "ОстатокКтКоп": "0",
            }
        )
        led.apply_opening(
            {
                "КонтрагентID": "C1",
                "Счет62": "62.1",
                "ДоговорID": "DB",
                "ВидРасчетовID": "V1",
                "ОстатокДтКоп": "0",
                "ОстатокКтКоп": "400000",
            }
        )
        before_a, debt_a, adv_a = led.split_payment_part(a, 1000000)
        before_b, debt_b, adv_b = led.split_payment_part(b, 1000000)
        self.assertEqual(before_a, 1000000)
        self.assertEqual(debt_a, 1000000)
        self.assertEqual(adv_a, 0)
        self.assertEqual(before_b, 0)
        self.assertEqual(debt_b, 0)
        self.assertEqual(adv_b, 1000000)

    def test_scoring_yaml_is_only_weight_source(self) -> None:
        cfg = load_scoring(SCORING)
        self.assertEqual(cfg.w("invoice_base_exact"), 180)
        self.assertEqual(cfg.c("high_min_gap"), 40)

    def test_missing_opening_balances_is_failed(self) -> None:
        src = GOLDEN / "success_tax_not_ready"
        tmp = Path(tempfile.mkdtemp(prefix="kudir_no_open_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for fname in INPUTS:
            if fname == "opening_balances.csv":
                continue
            shutil.copy(src / fname, tmp / fname)
        with self.assertRaises(RunError):
            run_directory(tmp, SCORING)
        self.assertFalse((tmp / "kudir_result.csv").exists())


class GoldenMatcherTests(unittest.TestCase):
    def _run_case(self, name: str) -> Path:
        src = GOLDEN / name
        tmp = Path(tempfile.mkdtemp(prefix=f"kudir_{name}_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for fname in INPUTS:
            shutil.copy(src / fname, tmp / fname)
        status = run_directory(tmp, SCORING, parser=_UnavailableParser())
        self.assertIn(status, ("SUCCESS", "SUCCESS_DEGRADED"))
        return tmp

    def _expected_rows(self, name: str):
        d = GOLDEN / name
        return (
            read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS),
            read_rows(d / "expected_matches.csv", MATCHES_FIELDS),
            read_rows(d / "expected_unresolved.csv", UNRESOLVED_FIELDS),
            read_kv(d / "expected_run_status.csv"),
        )

    def _assert_results(self, got, expected) -> None:
        self.assertEqual(len(got), len(expected), got)
        for g, e in zip(got, expected):
            for col in (
                "payment_id",
                "row_type",
                "matched_document_id",
                "contract_id",
                "amount_kopecks",
                "advance_kopecks",
                "repayment_kopecks",
                "vat_kopecks",
                "match_type",
                "confidence",
                "СодержаниеЗаписи",
            ):
                self.assertEqual(g[col], e[col], f"{col}: {g} vs {e}")
            for token in e["reason"].replace(";", " ").replace("+", " ").split():
                if token:
                    self.assertIn(token, g["reason"])

    def _assert_matches(self, got, expected) -> None:
        self.assertEqual(len(got), len(expected), got)
        for g, e in zip(got, expected):
            for col in (
                "payment_id",
                "document_id",
                "matched_amount_kopecks",
                "match_type",
                "score",
                "confidence",
                "invoice_link_score",
                "amount_score",
                "contract_score",
                "calculation_type_score",
            ):
                self.assertEqual(g[col], e[col], f"{col}: {g} vs {e}")

    def test_all_golden_cases(self) -> None:
        for name in CASES:
            with self.subTest(name=name):
                out = self._run_case(name)
                exp_res, exp_m, exp_u, exp_st = self._expected_rows(name)
                got_res = read_rows(out / "kudir_result.csv", KUDIR_RESULT_FIELDS)
                got_m = read_rows(out / "matches.csv", MATCHES_FIELDS)
                got_u = read_rows(out / "unresolved.csv", UNRESOLVED_FIELDS)
                st = read_kv(out / "run_status.csv")
                self._assert_results(got_res, exp_res)
                self._assert_matches(got_m, exp_m)
                self.assertEqual(got_u, exp_u)
                self.assertEqual(st["status"], exp_st["status"])
                self.assertEqual(st["tax_ready"], exp_st["tax_ready"])
                self.assertEqual(st["unresolved_count"], exp_st["unresolved_count"])
                self.assertEqual(st["unresolved_debt_kopecks"], exp_st["unresolved_debt_kopecks"])
                self.assertEqual(st.get("state_uncertain", "0"), exp_st.get("state_uncertain", "0"))
                self.assertEqual(
                    st.get("historical_unresolved_count", "0"),
                    exp_st.get("historical_unresolved_count", "0"),
                )
                for key in RUN_STATUS_KEYS:
                    self.assertIn(key, st)
                if name == "unattributed_62_movement":
                    log = (out / "analysis.log").read_text(encoding="utf-8")
                    self.assertIn("unattributed_62", log)
                    self.assertIn("remainder_mismatch", log)


if __name__ == "__main__":
    unittest.main()
