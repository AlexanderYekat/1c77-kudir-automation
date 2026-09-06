"""Golden 2.1–2.9: схема CSV v2 и ожидаемые факты. Matcher не вызывается."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kudir_proto.csv_io import (  # noqa: E402
    DOCUMENTS_FIELDS,
    EVENT_SORT_FIELDS,
    KUDIR_RESULT_FIELDS,
    LEDGER_FIELDS,
    MANIFEST_KEYS,
    MATCHES_FIELDS,
    OPENING_FIELDS,
    PAYMENTS_FIELDS,
    PAYMENTS_NON_AUTHORITATIVE,
    RUN_STATUS_KEYS,
    UNRESOLVED_FIELDS,
    read_kv,
    read_rows,
)

GOLDEN = ROOT / "tests" / "golden"

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


class CsvContractTests(unittest.TestCase):
    def test_payments_have_posting_number_for_sort(self) -> None:
        self.assertIn("НомерПроводкиВДокументе", PAYMENTS_FIELDS)
        pos = PAYMENTS_FIELDS.index("ПозицияДокумента")
        posting = PAYMENTS_FIELDS.index("НомерПроводкиВДокументе")
        self.assertEqual(posting, pos + 1)
        self.assertEqual(
            EVENT_SORT_FIELDS,
            ("ДатаОперации", "ПозицияДокумента", "НомерПроводкиВДокументе"),
        )

    def test_payments_contract_fields_are_non_authoritative(self) -> None:
        self.assertIn("ДоговорID", PAYMENTS_NON_AUTHORITATIVE)
        self.assertIn("ВидРасчетовID", PAYMENTS_NON_AUTHORITATIVE)

    def test_manifest_has_horizon_separate_from_quarter(self) -> None:
        self.assertIn("quarter_end", MANIFEST_KEYS)
        self.assertIn("match_horizon_end", MANIFEST_KEYS)
        self.assertNotEqual(
            MANIFEST_KEYS.index("quarter_end"),
            MANIFEST_KEYS.index("match_horizon_end"),
        )

    def test_matches_have_score_breakdown(self) -> None:
        for col in (
            "direct_reference_score",
            "invoice_link_score",
            "amount_score",
            "contract_score",
            "calculation_type_score",
            "semantic_score",
            "chronology_score",
        ):
            self.assertIn(col, MATCHES_FIELDS)

    def test_run_status_separates_success_and_tax_ready(self) -> None:
        self.assertEqual(
            RUN_STATUS_KEYS[:5],
            ("run_id", "status", "tax_ready", "unresolved_debt_kopecks", "unresolved_count"),
        )


class GoldenSchemaTests(unittest.TestCase):
    def _dir(self, name: str) -> Path:
        d = GOLDEN / name
        self.assertTrue(d.is_dir(), f"нет каталога {d}")
        return d

    def test_every_case_matches_schema_v2(self) -> None:
        from kudir_proto.csv_io import SCHEMA_VERSION

        for name in CASES:
            d = self._dir(name)
            with self.subTest(name=name):
                man = read_kv(d / "manifest.csv")
                for key in MANIFEST_KEYS:
                    self.assertTrue((man.get(key) or "").strip(), f"{name}: пустой {key}")
                self.assertEqual(man["schema_version"], SCHEMA_VERSION)
                self.assertNotEqual(man["quarter_end"], man["match_horizon_end"])
                read_rows(d / "payments.csv", PAYMENTS_FIELDS)
                read_rows(d / "ledger62.csv", LEDGER_FIELDS)
                docs = read_rows(d / "documents.csv", DOCUMENTS_FIELDS)
                for row in docs:
                    self.assertIn("СуммаОблагаемаяКоп", row)
                    self.assertIn("СуммаНеоблагаемаяКоп", row)
                    self.assertIn("СуммаНДСКоп", row)
                read_rows(d / "opening_balances.csv", OPENING_FIELDS)
                read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
                read_rows(d / "expected_matches.csv", MATCHES_FIELDS)
                read_rows(d / "expected_unresolved.csv", UNRESOLVED_FIELDS)
                st = read_kv(d / "expected_run_status.csv")
                for key in RUN_STATUS_KEYS:
                    self.assertIn(key, st)
                self.assertEqual(st["run_id"], man["run_id"])
                self.assertEqual(st["schema_version"], SCHEMA_VERSION)
                for row in read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS):
                    self.assertNotRegex(row["СодержаниеЗаписи"], r"[\r\n]")

    def test_2_1_high_advance_then_later_payment_sees_remainder(self) -> None:
        d = self._dir("advance_then_later_payment")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual([r["row_type"] for r in rows], ["ADVANCE", "DEBT"])
        self.assertEqual(rows[0]["confidence"], "HIGH")
        self.assertTrue(rows[0]["matched_document_id"])
        self.assertNotEqual(rows[0]["matched_document_id"], rows[1]["matched_document_id"])
        self.assertNotEqual(rows[1]["row_type"], "DEBT_UNRESOLVED")
        st = read_kv(d / "expected_run_status.csv")
        self.assertEqual(st["status"], "SUCCESS_DEGRADED")
        self.assertEqual(st["tax_ready"], "1")

    def test_2_2_split_by_analytics_not_payment_id(self) -> None:
        d = self._dir("multi_analytics_split")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(len({r["payment_id"] for r in rows}), 1)
        types = {r["row_type"] for r in rows}
        self.assertEqual(types, {"DEBT", "ADVANCE"})
        self.assertNotEqual(rows[0]["contract_id"], rows[1]["contract_id"])
        self.assertEqual(sum(int(r["amount_kopecks"]) for r in rows), 1500000)

    def test_2_3_no_cross_analytics_netting(self) -> None:
        d = self._dir("no_cross_analytics_netting")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["row_type"], "DEBT")
        self.assertEqual(rows[0]["amount_kopecks"], "1000000")
        self.assertIn("NO_NETTING", rows[0]["reason"])

    def test_2_4_extended_match_does_not_recalc_q4_tax(self) -> None:
        d = self._dir("december_advance_january_shipment")
        man = read_kv(d / "manifest.csv")
        self.assertEqual(man["quarter_end"], "2025-12-31")
        self.assertGreater(man["match_horizon_end"], man["quarter_end"])
        docs = read_rows(d / "documents.csv", DOCUMENTS_FIELDS)
        self.assertTrue(any(r["ДатаДокумента"].startswith("2026-01") for r in docs))
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["match_type"], "ADVANCE_EXTENDED_MATCH")
        self.assertEqual(rows[0]["vat_kopecks"], "0")
        self.assertIn("NO_TAX_RECALC_Q4", rows[0]["reason"])

    def test_2_5_no_5_105_on_2025_advance(self) -> None:
        d = self._dir("pre_vat_advance_post_vat_shipment")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["vat_kopecks"], "0")
        self.assertEqual(rows[0]["vat_base_kopecks"], "0")
        self.assertNotEqual(rows[0]["vat_kopecks"], "50000")
        self.assertIn("NO_CALCULATED_5_105", rows[0]["reason"])
        docs = read_rows(d / "documents.csv", DOCUMENTS_FIELDS)
        self.assertTrue(any(r["ДатаДокумента"].startswith("2026-") for r in docs))

    def test_2_6_unattributed_62_is_diagnostic(self) -> None:
        d = self._dir("unattributed_62_movement")
        log = (d / "expected_analysis.log").read_text(encoding="utf-8")
        self.assertIn("unattributed_62", log)
        self.assertIn("remainder_mismatch", log)
        ledger = read_rows(d / "ledger62.csv", LEDGER_FIELDS)
        self.assertTrue(any(r["payment_id"] == "" and r["source_document_id"] == "" for r in ledger))
        self.assertEqual(read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS), [])

    def test_2_7_success_with_tax_ready_zero(self) -> None:
        d = self._dir("success_tax_not_ready")
        st = read_kv(d / "expected_run_status.csv")
        self.assertEqual(st["status"], "SUCCESS_DEGRADED")
        self.assertEqual(st["tax_ready"], "0")
        self.assertGreater(int(st["unresolved_count"]), 0)
        self.assertGreater(int(st["unresolved_debt_kopecks"]), 0)
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["row_type"], "DEBT_UNRESOLVED")
        self.assertEqual(rows[0]["matched_document_id"], "")
        self.assertEqual(read_rows(d / "expected_matches.csv", MATCHES_FIELDS), [])

    def test_2_8_ambiguous_shipments_do_not_close_high(self) -> None:
        d = self._dir("ambiguous_equal_shipments")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["row_type"], "ADVANCE")
        self.assertEqual(rows[0]["matched_document_id"], "")
        self.assertNotEqual(rows[0]["confidence"], "HIGH")
        self.assertEqual(read_rows(d / "expected_matches.csv", MATCHES_FIELDS), [])

    def test_2_9_one_posting_one_payment_id_many_ledger_rows(self) -> None:
        d = self._dir("bank_multi_correspondence")
        pays = read_rows(d / "payments.csv", PAYMENTS_FIELDS)
        ids = [p["payment_id"] for p in pays]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(p["НомерПроводкиВДокументе"] for p in pays))
        ledger = read_rows(d / "ledger62.csv", LEDGER_FIELDS)
        posting4 = [r for r in ledger if r["НомерПроводкиВДокументе"] == "4" and r["payment_id"]]
        self.assertEqual(len({r["payment_id"] for r in posting4}), 1)
        self.assertEqual(len({r["ledger_event_id"] for r in posting4}), 2)
        results = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        same_pay = [r for r in results if r["payment_id"] == posting4[0]["payment_id"]]
        self.assertEqual({r["row_type"] for r in same_pay}, {"DEBT", "ADVANCE"})

    def test_4_5_partial_advance_splits_result_rows(self) -> None:
        d = self._dir("partial_advance_split")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["row_type"] for r in rows], ["ADVANCE", "ADVANCE", "ADVANCE"])
        self.assertEqual([r["amount_kopecks"] for r in rows], ["4200000", "2100000", "4200000"])
        self.assertTrue(rows[0]["matched_document_id"])
        self.assertTrue(rows[1]["matched_document_id"])
        self.assertEqual(rows[2]["matched_document_id"], "")
        self.assertEqual(sum(int(r["amount_kopecks"]) for r in rows), 10500000)

    def test_4_5_same_analytics_does_not_reuse_debt_before(self) -> None:
        d = self._dir("same_analytics_two_correspondences")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        debt = sum(int(r["amount_kopecks"]) for r in rows if r["row_type"] in ("DEBT", "DEBT_UNRESOLVED"))
        adv = sum(int(r["amount_kopecks"]) for r in rows if r["row_type"] == "ADVANCE")
        self.assertEqual(debt, 1000000)
        self.assertEqual(adv, 200000)
        self.assertLess(debt, 1200000)

    def test_4_5_posting_sorted_as_int(self) -> None:
        d = self._dir("posting_numeric_order")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["row_type"], "DEBT")
        self.assertNotEqual(rows[0]["row_type"], "ADVANCE")

    def test_4_6_act_does_not_high_match_goods_shipment(self) -> None:
        d = self._dir("direct_ref_act_not_goods")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["row_type"], "DEBT_UNRESOLVED")
        self.assertEqual(rows[0]["matched_document_id"], "")
        self.assertEqual(read_rows(d / "expected_matches.csv", MATCHES_FIELDS), [])

    def test_4_6_other_contract_still_closes_high_advance(self) -> None:
        d = self._dir("advance_other_contract_then_later_payment")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["row_type"], "ADVANCE")
        self.assertEqual(rows[0]["confidence"], "HIGH")
        self.assertTrue(rows[0]["matched_document_id"])
        self.assertEqual(rows[1]["row_type"], "DEBT_UNRESOLVED")
        self.assertNotEqual(rows[0]["contract_id"], "D|00001|A")

    def test_4_6_historical_unresolved_sets_state_uncertain(self) -> None:
        d = self._dir("historical_unresolved_blocks_tax")
        st = read_kv(d / "expected_run_status.csv")
        self.assertEqual(st["state_uncertain"], "1")
        self.assertEqual(st["tax_ready"], "0")
        self.assertGreater(int(st["historical_unresolved_count"]), 0)
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertTrue(all(r["payment_id"] != "BANK|0000000050|2" for r in rows))

    def test_4_6_other_contract_still_closes_high_advance(self) -> None:
        d = self._dir("advance_other_contract_then_later_payment")
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertEqual(rows[0]["row_type"], "ADVANCE")
        self.assertEqual(rows[0]["confidence"], "HIGH")
        self.assertTrue(rows[0]["matched_document_id"])
        self.assertNotEqual(rows[0]["contract_id"], "D|00001|A")
        self.assertNotEqual(rows[1]["matched_document_id"], rows[0]["matched_document_id"])

    def test_4_6_historical_unresolved_sets_uncertain(self) -> None:
        d = self._dir("historical_unresolved_blocks_tax")
        st = read_kv(d / "expected_run_status.csv")
        self.assertEqual(st["tax_ready"], "0")
        self.assertEqual(st["state_uncertain"], "1")
        self.assertGreater(int(st["historical_unresolved_count"]), 0)
        pays = read_rows(d / "payments.csv", PAYMENTS_FIELDS)
        self.assertTrue(any(p["is_target"] == "0" for p in pays))
        rows = read_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS)
        self.assertTrue(all(r["payment_id"] for r in rows))
        self.assertFalse(any(r["payment_id"] == next(p["payment_id"] for p in pays if p["is_target"] == "0") for r in rows))


if __name__ == "__main__":
    unittest.main()
