"""Этап 4.5: правки matcher по ревью. Не переписывает архитектуру."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kudir.combinations import cap_candidates, search_combination  # noqa: E402
from kudir.engine import run_directory  # noqa: E402
from kudir.legacy_parser_adapter import (  # noqa: E402
    LegacyParserAdapter,
    ParseResult,
    ParsedDoc,
    parse_response,
    refs_from_parse,
)
from kudir.matcher import posting_key, sort_tuple  # noqa: E402
from kudir.scoring import ScoreBreakdown, apply_chronology, load_scoring, pick_high  # noqa: E402
from kudir_proto.csv_io import (  # noqa: E402
    MATCHES_FIELDS,
    PAYMENTS_FIELDS,
    RUN_STATUS_KEYS,
    read_kv,
    write_kv,
)

SCORING = ROOT / "config" / "scoring.yaml"
GOLDEN = ROOT / "tests" / "golden"


class _BD:
    def __init__(self, total: int) -> None:
        self.total = total


class StubParser(LegacyParserAdapter):
    def __init__(self, *, available: bool = True, fail: bool = False, parsed: ParseResult | None = None) -> None:
        super().__init__()
        self.available = available
        self._probed = True
        self._fail = fail
        self._parsed = parsed if parsed is not None else ParseResult(sense="ОПЛАТА", docs=[])

    def probe(self) -> bool:
        return self.available

    def parse(self, text: str) -> ParseResult | None:
        if not self.available or self._fail:
            return None
        return self._parsed


class PostingSortTests(unittest.TestCase):
    def test_posting_numbers_sort_numerically(self) -> None:
        items = ["1", "10", "11", "2"]
        ordered = sorted(items, key=posting_key)
        self.assertEqual(ordered, ["1", "2", "10", "11"])
        ordered = sorted(items, key=lambda p: sort_tuple("2026-01-15", "0001", p))
        self.assertEqual(ordered, ["1", "2", "10", "11"])


class CombinationSearchTests(unittest.TestCase):
    def test_cap_sorts_before_truncating(self) -> None:
        cfg = load_scoring(SCORING)
        cands = [(i, _BD(40)) for i in range(8)]
        cands.append((8, _BD(300)))
        cands.extend((i, _BD(10)) for i in range(9, 15))
        out, capped = cap_candidates(cands, cfg)
        self.assertTrue(capped)
        self.assertEqual(len(out), cfg.s("max_candidates"))
        self.assertEqual(out[0][1].total, 300)

    def test_combination_unique_cover_and_timeout(self) -> None:
        items = [("a", 4200), ("b", 2100)]
        chosen, timed = search_combination(
            items, 10500, deadline=time.monotonic() + 2, exact=False, min_parts=2,
        )
        self.assertFalse(timed)
        self.assertEqual(set(chosen or []), {"a", "b"})
        chosen_eq, _ = search_combination(
            [("x", 1000), ("y", 1000)], 1000, deadline=time.monotonic() + 2, exact=False, min_parts=2,
        )
        self.assertIsNone(chosen_eq)
        _, timed_out = search_combination(
            [("a", 1), ("b", 1), ("c", 1)], 3, deadline=time.monotonic() - 1, exact=True, min_parts=2,
        )
        self.assertTrue(timed_out)


class ParserAdapterTests(unittest.TestCase):
    def test_parse_response_ok_docs(self) -> None:
        body = "OK\tОПЛАТА\nDOC\tСЧЕТ\t00002\t10.08.2026\nDOC\tАКТ\t25\t"
        parsed = parse_response(body)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.sense, "ОПЛАТА")
        self.assertEqual(parsed.docs[0].number, "2")
        self.assertEqual(parsed.docs[0].date, "2026-08-10")
        refs = refs_from_parse(parsed)
        self.assertEqual(refs[0].kind, "INVOICE")
        self.assertEqual(refs[1].kind, "SERVICE")

    def test_parse_response_error_is_none(self) -> None:
        self.assertIsNone(parse_response("ERROR\tboom"))
        self.assertIsNone(parse_response(""))

    def test_regression_saved_answers(self) -> None:
        samples = {
            "Оплата по счету №00002": "OK\tОПЛАТА\nDOC\tСЧЕТ\t00002\t\n",
            "Оплата по сч №1": "OK\tОПЛАТА\nDOC\tСЧЕТ\t1\t\n",
            "Оплата по акту №25": "OK\tОПЛАТА\nDOC\tАКТ\t25\t10.08.2026\n",
            "Оплата по документу Счет 00002": "OK\tОПЛАТА\nDOC\tСЧЕТ\t00002\t\n",
        }
        for text, body in samples.items():
            parsed = parse_response(body)
            self.assertIsNotNone(parsed, text)
            assert parsed is not None
            self.assertTrue(parsed.docs, text)

    def test_parse_posts_plain_text(self) -> None:
        captured: dict[str, object] = {}

        class _Resp:
            status = 200

            def read(self) -> bytes:
                return "OK\tОПЛАТА\nDOC\tСЧЕТ\t00002\t".encode("cp1251")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=0):
            captured["data"] = req.data
            captured["content_type"] = req.get_header("Content-type") or req.get_header("Content-Type")
            return _Resp()

        adapter = LegacyParserAdapter()
        adapter.available = True
        adapter._probed = True
        with patch("kudir.legacy_parser_adapter.urlopen", fake_urlopen):
            parsed = adapter.parse("Оплата по счету №00002")
        self.assertIsNotNone(parsed)
        self.assertIn(b"\xf1\xf7\xe5\xf2\xf3", captured["data"])  # «счету» в cp1251
        self.assertIn("text/plain", str(captured["content_type"]))

    def test_live_parser_skips_if_down(self) -> None:
        adapter = LegacyParserAdapter(timeout=0.2)
        if not adapter.probe():
            self.skipTest("http://127.0.0.1:8765 недоступен")
        parsed = adapter.parse("Оплата по счету №00002")
        self.assertIsNotNone(parsed)


class ScoringYamlTests(unittest.TestCase):
    def test_chronology_is_tie_break_not_high(self) -> None:
        a, b = ScoreBreakdown(amount=120, contract=40), ScoreBreakdown(amount=120, contract=40)
        apply_chronology([a, b], [0, 10], 20)
        self.assertEqual(a.chronology, 20)
        self.assertEqual(b.chronology, 0)
        self.assertGreater(a.total, b.total)
        self.assertEqual(a.selection_total, b.selection_total)
        self.assertLess(a.total - b.total, 40)

    def test_chronology_cannot_create_high(self) -> None:
        cfg = load_scoring(SCORING)
        a = ScoreBreakdown(amount=100, direct_reference=30)
        b = ScoreBreakdown(amount=100)
        self.assertEqual(a.selection_total, 130)
        picked = pick_high([("A", a), ("B", b)], [0, 10], cfg)
        self.assertIsNone(picked)

    def test_chronology_picks_nearest_in_tied_high_group(self) -> None:
        cfg = load_scoring(SCORING)
        a = ScoreBreakdown(amount=120, invoice_link=60)
        b = ScoreBreakdown(amount=120, invoice_link=60)
        c = ScoreBreakdown(amount=100)
        picked = pick_high([("A", a), ("B", b), ("C", c)], [10, 0, 5], cfg)
        self.assertIsNotNone(picked)
        self.assertEqual(picked[0], "B")

    def test_two_equal_without_rest_are_not_high(self) -> None:
        cfg = load_scoring(SCORING)
        a = ScoreBreakdown(amount=120, contract=40, calculation_type=35)
        b = ScoreBreakdown(amount=120, contract=40, calculation_type=35)
        picked = pick_high([("A", a), ("B", b)], [0, 1], cfg)
        self.assertIsNone(picked)

    def test_csv_io_has_review_contract(self) -> None:
        self.assertIn("НомерПроводкиВДокументе", PAYMENTS_FIELDS)
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
        tmp = Path(tempfile.mkdtemp(prefix="kudir_kv_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        write_kv(tmp / "run_status.csv", {"run_id": "X", "status": "SUCCESS"}, keys=RUN_STATUS_KEYS)
        data = read_kv(tmp / "run_status.csv")
        self.assertEqual(data["run_id"], "X")
        self.assertEqual(data["tax_ready"], "")


class ParserStatusTests(unittest.TestCase):
    def test_unavailable_parser_is_degraded_not_hidden_success(self) -> None:
        src = GOLDEN / "success_tax_not_ready"
        tmp = Path(tempfile.mkdtemp(prefix="kudir_degraded_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for fname in (
            "manifest.csv",
            "payments.csv",
            "ledger62.csv",
            "documents.csv",
            "opening_balances.csv",
        ):
            shutil.copy(src / fname, tmp / fname)
        status = run_directory(tmp, SCORING, parser=StubParser(available=False))
        self.assertEqual(status, "SUCCESS_DEGRADED")
        st = read_kv(tmp / "run_status.csv")
        self.assertEqual(st["status"], "SUCCESS_DEGRADED")
        self.assertEqual(st["tax_ready"], "0")
        self.assertEqual(st["parser_available"], "0")

    def test_available_parser_keeps_success_with_tax_not_ready(self) -> None:
        src = GOLDEN / "success_tax_not_ready"
        tmp = Path(tempfile.mkdtemp(prefix="kudir_ok_parser_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for fname in (
            "manifest.csv",
            "payments.csv",
            "ledger62.csv",
            "documents.csv",
            "opening_balances.csv",
        ):
            shutil.copy(src / fname, tmp / fname)
        status = run_directory(tmp, SCORING, parser=StubParser(available=True))
        self.assertEqual(status, "SUCCESS")
        st = read_kv(tmp / "run_status.csv")
        self.assertEqual(st["tax_ready"], "0")
        self.assertEqual(st["parser_available"], "1")

    def test_parser_line_failure_is_degraded(self) -> None:
        src = GOLDEN / "advance_then_later_payment"
        tmp = Path(tempfile.mkdtemp(prefix="kudir_fail_parser_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for fname in (
            "manifest.csv",
            "payments.csv",
            "ledger62.csv",
            "documents.csv",
            "opening_balances.csv",
        ):
            shutil.copy(src / fname, tmp / fname)
        status = run_directory(tmp, SCORING, parser=StubParser(available=True, fail=True))
        self.assertEqual(status, "SUCCESS_DEGRADED")


if __name__ == "__main__":
    unittest.main()
