"""reports/check_dbf_e05.py на синтетической базе DBF.

База хранит проводки так, как их хранит 1С 7.7 (1SENTRY с субконто покупателя и договора, позиции документов в
1SJOURN, коды счетов по маске): реализация 2025 года (входящий остаток), оплата в январе, реализация без договора в
феврале, аванс выпиской в апреле. Набор Э0.5 с DAY_START на 01.01 и 01.04, снимками перед денежными документами и
квартальными контролями сходится с базой; изменённое сальдо, пропущенный ненулевой ключ и неверный контроль видны.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports"))
sys.path.insert(0, str(ROOT / "tests"))

import check_dbf_e05 as chk  # noqa: E402
import check_e05 as e05  # noqa: E402
from test_check_dbf_e04 import b36, write_dbf  # noqa: E402

C1, C2, K1 = "     1   ", "     2   ", "     3   "
A621, A622, A51, A90 = "     A   ", "     B   ", "     C   ", "     D   "
DOCS = {  # name: (iddoc number, kind id, date, sum, Dt, Kt, customer, contract)
    "OLD": (460001, 294, "20251210", "1000.00", A621, A90, C1, K1),
    "PKO": (460002, 276, "20260115", "400.00", A51, A621, C1, K1),
    "SALE": (460003, 294, "20260201", "300.00", A621, A90, C2, ""),
    "BANK": (460004, 238, "20260402", "500.00", A51, A622, C2, ""),
}


def doc_id(name: str) -> str:
    number, kind = DOCS[name][:2]
    return 'DOC|{"O","0","0","%d","0","0","%10d   "}'.replace("%d", str(kind), 1).replace("%10d", f"{number:>10}")


def position(name: str) -> str:
    number, _, date = DOCS[name][:3]
    return f"#{date}{100000000:>10}{number:>10}   "


def build_base(directory: Path) -> None:
    write_dbf(directory / "SC133.DBF", [("ID", "C", 9), ("CODE", "C", 8)], [{"ID": C1, "CODE": "1548302"}, {"ID": C2, "CODE": "15000610"}])
    write_dbf(directory / "SC112.DBF", [("ID", "C", 9), ("CODE", "C", 6), ("PARENTEXT", "C", 9)], [{"ID": K1, "CODE": "000002", "PARENTEXT": C1}])
    sc = [("SC0", "C", 4), ("SC1", "C", 4), ("SC2", "C", 4)]
    # ACTIVE: 1 - active (62.1), 2 - passive (62.2), as in the chart of accounts of the base.
    write_dbf(directory / "1SACCS.DBF", [("ID", "C", 9), ("SCHKOD", "C", 11), ("ACTIVE", "N", 1)] + sc,
              [{"ID": A621, "SCHKOD": " 62. 1.", "ACTIVE": "1", "SC0": "  EE", "SC1": " 8GG", "SC2": " 90P"},
               {"ID": A622, "SCHKOD": " 62. 2.", "ACTIVE": "2", "SC0": "  EE", "SC1": " 8GG"},
               {"ID": A51, "SCHKOD": " 51."}, {"ID": A90, "SCHKOD": " 90. 1. 1."}])
    write_dbf(directory / "1SJOURN.DBF", [("IDDOC", "C", 9), ("IDDOCDEF", "C", 4), ("DATE", "D", 8), ("TIME", "C", 6)],
              [{"IDDOC": b36(n) + "   ", "IDDOCDEF": b36(kind, 4), "DATE": date, "TIME": b36(100000000)} for n, kind, date, *_ in DOCS.values()])
    entry = [("DOCID", "C", 9), ("NUMBER", "N", 5), ("CORNO", "N", 5), ("DATE", "D", 8), ("ACCDTID", "C", 9), ("ACCKTID", "C", 9), ("SUM", "N", 14)]
    for side in ("DT", "KT"):
        for i in range(3):
            entry += [(f"V{side}SC{i}", "C", 4), (f"{side}SC{i}", "C", 13)]
    rows = []
    for n, _, date, amount, dt, kt, customer, contract in DOCS.values():
        side = "DT" if dt in (A621, A622) else "KT"
        rows.append({"DOCID": b36(n) + "   ", "NUMBER": "1", "CORNO": "0", "DATE": date, "ACCDTID": dt, "ACCKTID": kt, "SUM": amount,
                     f"V{side}SC0": "  EE", f"{side}SC0": customer, f"V{side}SC1": " 8GG", f"{side}SC1": contract})
    write_dbf(directory / "1SENTRY.DBF", entry, list(reversed(rows)))


def snapshot(kind, date, doc, pos, part, customer, contract, s):
    state = "ALL" if part == "CUSTOMER" else ("VALUE" if contract else "EMPTY")
    dt, kt = (s, 0) if s >= 0 else (0, -s)
    return dict(zip(e05.SNAPSHOTS_HEADER, [kind, date, doc, pos, part, customer, contract, state, str(dt), str(kt), "KNOWN"]))


def run_rows() -> tuple[list[dict], list[dict]]:
    c1, c2, k1 = "C|1548302", "C|15000610", "D|C|1548302|000002"
    snaps = []
    for date, s1, s2 in (("2026-01-01", 100000, 0), ("2026-04-01", 60000, 30000)):
        snaps += [snapshot("DAY_START", date, "", "", "CUSTOMER", c1, "", s1), snapshot("DAY_START", date, "", "", "CUSTOMER_CONTRACT", c1, k1, s1),
                  snapshot("DAY_START", date, "", "", "CUSTOMER", c2, "", s2), snapshot("DAY_START", date, "", "", "CUSTOMER_CONTRACT", c2, "", s2)]
    snaps += [snapshot("BEFORE_DOCUMENT", "2026-01-15", doc_id("PKO"), position("PKO"), "CUSTOMER", c1, "", 100000),
              snapshot("BEFORE_DOCUMENT", "2026-01-15", doc_id("PKO"), position("PKO"), "CUSTOMER_CONTRACT", c1, k1, 100000),
              snapshot("BEFORE_DOCUMENT", "2026-04-02", doc_id("BANK"), position("BANK"), "CUSTOMER", c2, "", 30000),
              snapshot("BEFORE_DOCUMENT", "2026-04-02", doc_id("BANK"), position("BANK"), "CUSTOMER_CONTRACT", c2, "", 30000)]
    header = e05.QUARTERS_HEADER
    quarters = [dict(zip(header, ["2026-01-01", "2026-03-31", "2026-03-31", "1", "100000", "0", "90000", "0", "KNOWN", "REFRESHED", "R", "3"])),
                dict(zip(header, ["2026-04-01", "2026-06-30", "2026-04-15", "0", "90000", "0", "", "", "UNKNOWN", "REFRESHED", "R", "1"]))]
    return snaps, quarters


class CheckDbfE05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        build_base(self.tmp)
        self.postings = chk.Postings(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_postings_in_time_order(self) -> None:
        self.assertEqual([s["date"] for s in self.postings.sides], ["20251210", "20260115", "20260201", "20260402"])
        self.assertEqual(self.postings.sides[2]["contract"], "")
        self.assertEqual(self.postings.sides[0]["contract"], "D|C|1548302|000002")
        self.assertEqual(self.postings.money_docs, {doc_id("PKO"), doc_id("BANK")})
        self.assertEqual(self.postings.doc_position[doc_id("PKO")], position("PKO"))

    def test_matching_run_is_clean(self) -> None:
        snaps, quarters = run_rows()
        result = chk.compare(self.postings, snaps, quarters, "2026-04-15")
        self.assertEqual((result["snapshots"]["rows_checked"], result["snapshots"]["signed_differ"], result["snapshots"]["missing_nonzero"]), (12, 0, 0))
        self.assertEqual(result["controls"]["differ"], [])
        self.assertEqual(result["snapshots"]["components"], {"both readings": 12})

    def test_document_included_is_reported(self) -> None:
        snaps, quarters = run_rows()
        snaps[8] = snapshot("BEFORE_DOCUMENT", "2026-01-15", doc_id("PKO"), position("PKO"), "CUSTOMER", "C|1548302", "", 60000)
        result = chk.compare(self.postings, snaps, quarters, "2026-04-15")
        self.assertEqual(result["snapshots"]["signed_differ"], 1)
        self.assertEqual(result["snapshots"]["examples"][0]["base"], 100000)

    def test_missing_nonzero_key_and_control(self) -> None:
        snaps, quarters = run_rows()
        snaps = [s for s in snaps if not (s["BoundaryDate"] == "2026-04-01" and s["КонтрагентID"] == "C|15000610")]
        quarters[0]["closing_debit_kopecks"] = "90001"
        result = chk.compare(self.postings, snaps, quarters, "2026-04-15")
        self.assertEqual(result["snapshots"]["missing_nonzero"], 2)
        self.assertEqual(len(result["controls"]["differ"]), 1)

    def test_matching_run_has_no_differences(self) -> None:
        snaps, quarters = run_rows()
        self.assertEqual(chk.differences(chk.compare(self.postings, snaps, quarters, "2026-04-15")), [])

    def test_components_fitting_neither_reading_fail(self) -> None:
        # Замечание P2 к -02: +100 коп к Дт и Кт одного снимка - сальдо то же, компоненты не подходят ни к одному
        # прочтению 1С; это расхождение, а не успех проверки.
        snaps, quarters = run_rows()
        snaps[0]["ОстатокДтКоп"] = str(int(snaps[0]["ОстатокДтКоп"]) + 100)
        snaps[0]["ОстатокКтКоп"] = str(int(snaps[0]["ОстатокКтКоп"]) + 100)
        result = chk.compare(self.postings, snaps, quarters, "2026-04-15")
        self.assertEqual((result["snapshots"]["signed_differ"], result["snapshots"]["components"].get("neither")), (0, 1))
        self.assertEqual(chk.differences(result), ["components fit neither reading: 1"])
        self.assertEqual(result["snapshots"]["component_examples"][0]["export"], (100100, 100))

    def test_unknown_row_is_not_confirmed(self) -> None:
        snaps, quarters = run_rows()
        snaps[1].update({"ОстатокДтКоп": "", "ОстатокКтКоп": "", "Status": "UNKNOWN"})
        result = chk.compare(self.postings, snaps, quarters, "2026-04-15")
        self.assertEqual((result["snapshots"]["rows_checked"], result["snapshots"]["rows_unknown"]), (11, 1))
        self.assertEqual(chk.differences(result), ["unknown rows: 1"])

    def test_components_by_account_kind(self) -> None:
        # 1С отдаёт сальдо активного 62.1 в Дт (кредитовое - отрицательным), пассивного 62.2 - в Кт.
        self.assertEqual(self.postings.kind["62.1"], "1")
        self.assertEqual(chk.by_kind(-11540000, "1"), (-11540000, 0))
        self.assertEqual(chk.by_kind(-195000, "2"), (0, 195000))
        self.assertEqual(chk.by_kind(-5, "3"), (0, 5))

    def test_forecast(self) -> None:
        out = chk.forecast(self.postings, "2026-04-15")
        self.assertEqual(out["day_start"]["2026-04-01"]["customers_sum_kopecks"], 90000)
        self.assertEqual(out["controls"]["2026-01-01"], (100000, 0, 90000, 0))
        self.assertEqual(out["controls"]["2026-04-01"], (90000, 0, None, None))
        self.assertEqual(out["window"]["money_documents"], 2)


if __name__ == "__main__":
    unittest.main()
