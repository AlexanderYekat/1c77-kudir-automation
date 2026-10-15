"""Independent check of an E0.5 run against the 1C 7.7 DBF tables of the same base (read-only).

    python reports/check_dbf_e05.py <base directory> exchange/KUDIR4_20261012_002 [--out report.md] [--json out.json]
    python reports/check_dbf_e05.py <base directory> --forecast 2026-10-10

The export takes balances from БухгалтерскиеИтоги. This script recomputes them from the postings themselves: every
62.1/62.2 side of 1SENTRY over the whole history of the base (customer and contract subconto resolved in SC133/SC112,
contract ID by its owner as in the export), ordered by the position of its document in 1SJOURN. Then:

- DAY_START d - sides dated before d; BEFORE_DOCUMENT - sides of documents with a smaller position (the document
  itself excluded); each snapshot row of balance_snapshots.csv is compared by its signed balance Дт - Кт, and its
  components are matched against two readings (per subaccount by its kind and summed, or collapsed in total);
- customers and contracts with a non-zero balance at a quarter start that the run did not write;
- the four general controls of ledger_quarters.csv: balance of each subaccount by its kind (1SACCS.ACTIVE: active 62.1
  in Дт, passive 62.2 in Кт, a balance of the other sign negative) summed over 62.1/62.2, at the start of the first
  day and at the end of the last day of the quarter.

--forecast prints what a run with that match_horizon_end should give (counts, sums, controls) without a run.
The base must not be held by 1C in exclusive mode: run it on a copy of the DBF files.

Exit codes: 0 - everything matched; 1 - differences (signed balance, Дт/Кт fitting neither reading, UNKNOWN rows,
missing non-zero balances, general controls); 2 - the base or the run could not be read.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_e05 as e05  # noqa: E402
from check_dbf_e04 import account_code, b36, kopecks, read_dbf  # noqa: E402
import check_e02 as e02  # noqa: E402  run_start: the window of a run of any stage
from check_e02 import Findings, read_table  # noqa: E402
from check_e03 import rub  # noqa: E402

SCOPE = ("62.1", "62.2")
MONEY = ("50", "51", "55")
EXAMPLES = 10


def collapsed(s: int) -> tuple[int, int]:
    return (s, 0) if s >= 0 else (0, -s)


def by_kind(s: int, kind: str) -> tuple[int, int]:
    """Дт/Кт of a balance as 1C reports it by the kind of the account (1SACCS.ACTIVE): an active account (1, 62.1)
    always in Дт - a credit balance is a negative Дт; a passive one (2, 62.2) always in Кт; active-passive collapsed.
    Run KUDIR4_20261012_002 on base-for-test: a customer's advance on 62.1 came as Дт -115 400,00, Кт 0."""
    if kind == "1":
        return s, 0
    if kind == "2":
        return 0, -s
    return collapsed(s)


class Postings:
    """62.1/62.2 sides of all postings of the base, in the order of the 1C time scale."""

    def __init__(self, directory: Path) -> None:
        accounts = read_dbf(directory / "1SACCS.DBF", {"ID", "SCHKOD", "SC0", "SC1", "SC2", "ACTIVE"})
        self.kind = {account_code(r["SCHKOD"]): r["ACTIVE"].strip() for r in accounts}
        self.accounts = {r["ID"]: account_code(r["SCHKOD"]) for r in accounts}
        self.subconto = {account_code(r["SCHKOD"]): (r["SC0"], r["SC1"]) for r in accounts}
        customers = {r["ID"]: r["CODE"].strip() for r in read_dbf(directory / "SC133.DBF", {"ID", "CODE"})}
        contracts = {r["ID"]: (r["CODE"].strip(), r["PARENTEXT"]) for r in read_dbf(directory / "SC112.DBF", {"ID", "CODE", "PARENTEXT"})}
        journal = {r["IDDOC"]: r for r in read_dbf(directory / "1SJOURN.DBF", {"IDDOC", "IDDOCDEF", "DATE", "TIME"})}
        self.sides: list[dict] = []
        self.money_docs: set[str] = set()
        self.doc_position: dict[str, str] = {}
        for e in read_dbf(directory / "1SENTRY.DBF"):
            dt, kt = self.accounts.get(e["ACCDTID"], ""), self.accounts.get(e["ACCKTID"], "")
            j = journal.get(e["DOCID"])
            position = f"#{j['DATE']}{b36(j['TIME']):>10}{b36(e['DOCID'][:6]):>10}{e['DOCID'][6:]}" if j else ""
            key = f'{{"O","0","0","{b36(j["IDDOCDEF"])}","0","0","{b36(e["DOCID"][:6]):>10}{e["DOCID"][6:]}"}}' if j else ""
            doc = "DOC|" + key.replace("%", "%25").replace("|", "%7C") if key else ""
            for side, account, other, prefix, sign in (("D", dt, kt, "DT", 1), ("K", kt, dt, "KT", -1)):
                if account not in SCOPE:
                    continue
                kinds = self.subconto[account]
                customer_ref = e[f"{prefix}SC0"][:9] if e[f"V{prefix}SC0"] == kinds[0] else ""
                contract_ref = e[f"{prefix}SC1"][:9] if e[f"V{prefix}SC1"] == kinds[1] else ""
                customer = f"C|{customers[customer_ref]}" if customer_ref in customers else ""
                contract = ""
                if contract_ref in contracts:
                    code, owner = contracts[contract_ref]
                    contract = f"D|C|{customers.get(owner, customer[2:])}|{code}"
                self.sides.append({"date": e["DATE"], "position": position, "doc": doc, "account": account, "customer": customer,
                                   "contract": contract, "delta": sign * kopecks(e["SUM"])})
                if other[:2] in MONEY and doc:
                    self.money_docs.add(doc)
                if doc:
                    self.doc_position[doc] = position
        self.sides.sort(key=lambda s: s["position"])
        self.positions = [s["position"] for s in self.sides]

    def balances(self, upto: int) -> tuple[Counter, Counter, Counter]:
        """(per customer, per (customer, contract), per account) of the first `upto` sides in time order."""
        customers, pairs, accounts = Counter(), Counter(), Counter()
        for s in self.sides[:upto]:
            customers[s["customer"]] += s["delta"]
            pairs[(s["customer"], s["contract"])] += s["delta"]
            accounts[s["account"]] += s["delta"]
        return customers, pairs, accounts

    def before_date(self, date: str) -> int:
        """Number of sides dated before `date` (YYYY-MM-DD): positions start with #YYYYMMDD."""
        return bisect.bisect_left(self.positions, "#" + date.replace("-", ""))

    def through_date(self, date: str) -> int:
        return bisect.bisect_left(self.positions, "#" + date.replace("-", "") + "~")


def general_controls(postings: Postings, start: str, end: str | None) -> tuple[int, int, int | None, int | None]:
    """Дт/Кт of 62.1/62.2 by the kind of each subaccount, summed, at the start of `start` and the end of `end`."""
    _, _, opening = postings.balances(postings.before_date(start))
    od = sum(by_kind(opening[a], postings.kind.get(a, ""))[0] for a in SCOPE)
    oc = sum(by_kind(opening[a], postings.kind.get(a, ""))[1] for a in SCOPE)
    if end is None:
        return od, oc, None, None
    _, _, closing = postings.balances(postings.through_date(end))
    return (od, oc, sum(by_kind(closing[a], postings.kind.get(a, ""))[0] for a in SCOPE),
            sum(by_kind(closing[a], postings.kind.get(a, ""))[1] for a in SCOPE))


def compare(postings: Postings, snapshots: list[dict], quarters: list[dict], horizon: str) -> dict:
    result: dict = {"snapshots": {}, "controls": {}}
    r = result["snapshots"]
    rows_by_boundary: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in snapshots:
        boundary = ("DAY_START", row["BoundaryDate"]) if row["BoundaryKind"] == "DAY_START" else ("BEFORE_DOCUMENT", row["BeforeDocumentID"])
        rows_by_boundary[boundary].append(row)
    # One pass over the sides in time order: at the cut of every boundary the running balances are its snapshot.
    cuts = []
    for (kind, ref), rows in rows_by_boundary.items():
        cut = postings.before_date(ref) if kind == "DAY_START" else bisect.bisect_left(postings.positions, rows[0]["ПозицияДокумента"])
        cuts.append((cut, rows))
    cuts.sort(key=lambda item: item[0])
    checked = signed_differ = unknown = 0
    components = Counter()
    examples: list = []
    component_examples: list = []
    # Running balances by (account, customer) and (account, customer, contract); the account "" holds the total.
    running: Counter = Counter()
    done = 0
    for cut, rows in cuts:
        for s in postings.sides[done:cut]:
            for account in (s["account"], ""):
                running[(account, s["customer"], None)] += s["delta"]
                running[(account, s["customer"], s["contract"])] += s["delta"]
        done = cut
        for row in rows:
            s = e05.signed(row)
            if s is None:
                unknown += 1
                continue
            checked += 1
            contract = None if row["Slice"] == "CUSTOMER" else row["ДоговорID"]
            expected = running[("", row["КонтрагентID"], contract)]
            # Components per subaccount by its kind, summed (as БухгалтерскиеИтоги reports them), or collapsed in total.
            per_account = [by_kind(running[(a, row["КонтрагентID"], contract)], postings.kind.get(a, "")) for a in SCOPE]
            if s != expected:
                signed_differ += 1
                if len(examples) < EXAMPLES:
                    examples.append({"key": [row[f] for f in e05.SNAPSHOT_KEY], "export": s, "base": expected})
            got = (int(row["ОстатокДтКоп"]), int(row["ОстатокКтКоп"]))
            summed = tuple(sum(v[i] for v in per_account) for i in (0, 1))
            if got == summed and got == collapsed(expected):
                components["both readings"] += 1
            elif got == summed:
                components["by subaccount kind, summed"] += 1
            elif got == collapsed(expected):
                components["collapsed in total"] += 1
            else:
                components["neither"] += 1
                if len(component_examples) < EXAMPLES:
                    component_examples.append({"key": [row[f] for f in e05.SNAPSHOT_KEY], "export": got, "by_kind": summed, "collapsed": collapsed(expected)})
    r["rows_checked"], r["signed_differ"], r["examples"], r["components"] = checked, signed_differ, examples, dict(components)
    r["component_examples"], r["rows_unknown"] = component_examples, unknown

    # Non-zero balances at quarter starts that the run did not write.
    written = defaultdict(set)
    for row in snapshots:
        if row["BoundaryKind"] == "DAY_START":
            written[row["BoundaryDate"]].add((row["Slice"], row["КонтрагентID"], row["ДоговорID"]))
    missing: list = []
    for date in e05.quarter_starts(horizon):
        customers, pairs, _ = postings.balances(postings.before_date(date))
        for customer, s in customers.items():
            if s and customer and ("CUSTOMER", customer, "") not in written[date]:
                missing.append(("CUSTOMER", date, customer, s))
        for (customer, contract), s in pairs.items():
            if s and customer and ("CUSTOMER_CONTRACT", customer, contract) not in written[date]:
                missing.append(("CUSTOMER_CONTRACT", date, customer, contract, s))
        without = customers.get("", 0)
        if without:
            missing.append(("NO_CUSTOMER", date, without))
    r["missing_nonzero"] = len(missing)
    r["missing_examples"] = missing[:EXAMPLES]

    c = result["controls"]
    differ = []
    for q in quarters:
        start, end = q["quarter_start"], q["quarter_end"]
        full = end <= horizon
        od, oc, cd, cc = general_controls(postings, start, end if full else None)
        expected = [str(od), str(oc), str(cd) if full else "", str(cc) if full else ""]
        got = [q["opening_debit_kopecks"], q["opening_credit_kopecks"], q["closing_debit_kopecks"], q["closing_credit_kopecks"]]
        if got != expected:
            differ.append({"quarter": start, "export": got, "base": expected})
    c["quarters"], c["differ"] = len(quarters), differ
    return result


def forecast(postings: Postings, horizon: str) -> dict:
    out: dict = {"day_start": {}, "controls": {}}
    for date in e05.quarter_starts(horizon):
        customers, pairs, _ = postings.balances(postings.before_date(date))
        out["day_start"][date] = {
            "customers_nonzero": sum(1 for c, s in customers.items() if c and s),
            "pairs_nonzero": sum(1 for (c, _), s in pairs.items() if c and s),
            "customers_sum_kopecks": sum(s for c, s in customers.items() if c),
            "without_customer_kopecks": customers.get("", 0),
        }
        end = e05.quarter_end(date)
        out["controls"][date] = general_controls(postings, date, end if end <= horizon else None)
    window = [s for s in postings.sides if "20260101" <= s["date"] <= horizon.replace("-", "")]
    docs = {s["doc"] for s in window}
    money = docs & postings.money_docs
    out["window"] = {"sides": len(window), "documents": len(docs), "money_documents": len(money),
                     "money_document_customers": len({(s["doc"], s["customer"]) for s in window if s["doc"] in money and s["customer"]})}
    return out


def differences(result: dict) -> list[str]:
    """What keeps the run from matching the base: a signed balance; Дт/Кт fitting neither reading of 1C, even with the
    signed balance right; a row the base cannot confirm (UNKNOWN); a missing non-zero balance; a general control."""
    r, c = result["snapshots"], result["controls"]
    found = {"signed balance": r["signed_differ"], "components fit neither reading": r["components"].get("neither", 0),
             "unknown rows": r["rows_unknown"], "missing non-zero": r["missing_nonzero"], "general controls": len(c["differ"])}
    return [f"{name}: {count}" for name, count in found.items() if count]


def report(run: Path, base: Path, horizon: str, result: dict) -> str:
    r, c = result["snapshots"], result["controls"]
    lines = [f"# Сверка {run.name} (снимки и контроли) с таблицами DBF базы", "",
             f"База `{base}`, охват 2026-01-01..{horizon}. Остатки пересчитаны из 1SENTRY за всю историю базы, мимо БухгалтерскиеИтоги.", "",
             "## 1. balance_snapshots.csv", "",
             f"Известных строк сверено {r['rows_checked']}, расхождений сальдо {r['signed_differ']}; "
             f"неизвестных строк (UNKNOWN, базой не подтверждены) {r['rows_unknown']}.", ""]
    for item in r["examples"]:
        lines.append(f"- {item['key']}: выгрузка {rub(item['export'])}, база {rub(item['base'])}")
    lines += ["", f"Компоненты Дт/Кт: {r['components']}.", ""]
    lines += [f"- ни одно прочтение: {item}" for item in r["component_examples"]]
    lines += ["",
              f"Ненулевых остатков на началах кварталов, которых нет в выгрузке: {r['missing_nonzero']}.", ""]
    lines += [f"- {item}" for item in r["missing_examples"]]
    lines += ["", "## 2. ledger_quarters.csv", "", f"Кварталов {c['quarters']}, расхождений общих контролей {len(c['differ'])}.", ""]
    lines += [f"- {item['quarter']}: выгрузка {item['export']}, база {item['base']}" for item in c["differ"]]
    lines += ["", "## 3. Итог", "", "; ".join(differences(result)) or "Всё совпало."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path)
    parser.add_argument("run", type=Path, nargs="?")
    parser.add_argument("--forecast", help="YYYY-MM-DD: match_horizon_end of the run to forecast")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
        postings = Postings(args.base)
    except (OSError, KeyError, ValueError) as error:
        print(f"база не прочитана: {error}")
        return 2
    if args.forecast:
        print(json.dumps(forecast(postings, args.forecast), ensure_ascii=False, indent=1))
        return 0
    if args.run is None:
        parser.error("нужен каталог запуска или --forecast")
    local = Findings()
    snapshots = read_table(args.run, e05.SNAPSHOTS_FILE, e05.SNAPSHOTS_HEADER, local)
    quarters = read_table(args.run, e05.QUARTERS_FILE, e05.QUARTERS_HEADER, local)
    start = e02.run_start(e02.log_of(args.run))
    window = start[1] if start else None
    if snapshots is None or quarters is None or window is None:
        print(f"набор не прочитан: окно {window}, {local.items}")
        return 2
    result = compare(postings, snapshots, quarters, window[1])
    text = report(args.run, args.base, window[1], result)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"snapshots": {k: v for k, v in result["snapshots"].items() if k not in {"examples", "missing_examples"}},
                      "controls": {"quarters": result["controls"]["quarters"], "differ": len(result["controls"]["differ"])}}, ensure_ascii=False, indent=1))
    found = differences(result)
    print("итог: " + ("; ".join(found) if found else "всё совпало"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
