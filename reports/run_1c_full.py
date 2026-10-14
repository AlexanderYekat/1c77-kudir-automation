"""Run the FULL export of kudir_export.ert in a real 1C 7.7 session (harness77 core): one or several runs with controlled
faults in one 1C process, then judge publication and recovery (plan No. 13, E0.6).

    python reports/run_1c_full.py --output <fresh ASCII dir> [--base <copy>] [--start 2026-01-01] [--end 2026-01-31]
        --run full_ok_ledger62_ok [--run full ...] [--session-seconds 1500]

Each --run is one ОткрытьФорму of the ERT (a fresh form context) in the same 1C process. Inside it the module runs one
FULL per step of the run name (КУДиР4_БезФормы, КУДиР4_ЦельСбоя): ok - no fault; ledger62, documents, snapshots,
quarters, idmap - an exception right after the first record of that file; precheck - a duplicate quarter appended to
ledger_quarters.csv before the package check; publish - an exception after manifest.csv says COMPLETE and before
export_status. The steps of one --run share the form and its module variables: the owner pressing the button again
after a failure.

Judged (exit 1 when anything fails):
- every step: export_status.csv and manifest.csv carry the run_id of the directory, diagnostics.csv exists;
- an ok step publishes export_status=COMPLETE with manifest coverage_status=COMPLETE and no ERROR in diagnostics.csv,
  and check_e06 package passes (all files and their content);
- a fault step leaves FAILED (never COMPLETE), coverage_status=INCOMPLETE, an ERROR in diagnostics.csv, no
  export_status.csv.tmp/manifest.csv.tmp left that a reader could take for a result, and no form error of check_e06;
- every run directory is new, and every KUDIR4_* directory that existed before the session is byte-identical after it:
  previous packages, successful or not, are never touched.
The memory lines of the logs (КУДиР4_Память) and the result per step go to series.json in the output directory.
Exit codes as run_1c_e05: 0 - passed, 1 - failed, 2 - the session itself was blocked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_e06  # noqa: E402
import run_1c_e05 as runner  # noqa: E402
from check_e02 import Findings, parse_csv  # noqa: E402

STEPS = ("ok", "ledger62", "documents", "snapshots", "quarters", "idmap", "precheck", "publish")
RUN_NAME = re.compile(r"full(?:_(?:" + "|".join(STEPS) + r"))*")
MEMORY = re.compile(r"^(\d{4}-\d{2}-\d{2} +\d{1,2}:\d{2}:\d{2}) \| \w+ \| Э0\.6 память \[(.+?)\]: pid=(\d+) private_kb=(\d+) ws_kb=(\d+) "
                    r"virtual_kb=(\d+) peak_ws_kb=(\d+) peak_virtual_kb=(\d+) peak_private_kb=(\d+)", re.M)
MEMORY_FIELDS = ("private_kb", "ws_kb", "virtual_kb", "peak_ws_kb", "peak_virtual_kb", "peak_private_kb")
END = re.compile(r"END Э0\.6 итог=(\w+)")


def steps_of(run: str) -> list[str]:
    return run.split("_")[1:] or ["ok"]


def exchange_root() -> Path:
    return Path(os.environ.get("KUDIR_PROTO_ROOT", str(runner.ROOT))) / "exchange"


def tree_hashes(exchange: Path) -> dict[str, dict[str, str]]:
    """sha256 of every file of every KUDIR4_* run directory."""
    result = {}
    for directory in sorted(exchange.glob("KUDIR4_*")):
        if directory.is_dir():
            result[directory.name] = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(directory.iterdir()) if f.is_file()}
    return result


def key_values(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    rows = parse_csv(path.read_bytes(), path.name, Findings()) or []
    return {row[0]: row[1] for row in rows[1:] if len(row) == 2}


def memory_points(log_text: str) -> list[dict]:
    return [{"time": m[1], "point": m[2], "pid": m[3], **{f: int(v) for f, v in zip(MEMORY_FIELDS, m.groups()[3:])}}
            for m in MEMORY.finditer(log_text)]


def outcome(catalog: Path) -> dict:
    """What a reader of the run directory sees: export_status, coverage, ERROR rows, the log END and memory lines."""
    status = key_values(catalog / "export_status.csv") or {}
    manifest = key_values(catalog / "manifest.csv") or {}
    diagnostics = catalog / "diagnostics.csv"
    rows = parse_csv(diagnostics.read_bytes(), diagnostics.name, Findings()) if diagnostics.is_file() else None
    errors = [r for r in (rows or [])[1:] if r and r[0] == "ERROR"]
    log = catalog / "1c_export.log"
    text = log.read_bytes().decode("cp1251", "replace") if log.is_file() else ""
    end = END.findall(text)
    # The whole package check: files, metadata and content of a published directory, the form of a failed one.
    findings, summary = check_e06.check_package(catalog)
    return {
        "catalog": catalog.name, "status": status.get("status"), "status_run_id": status.get("run_id"),
        "coverage_status": manifest.get("coverage_status"), "manifest_run_id": manifest.get("run_id"),
        "diagnostics": rows is not None,
        "errors": len(errors), "first_errors": [r[4] + " " + r[1] + ": " + r[5][:160] for r in errors[:3] if len(r) >= 6],
        "leftovers": sorted(p.name for p in catalog.glob("*.tmp")), "end": end[-1] if end else None,
        "package_verdict": summary["verdict"],
        "package_errors": [f"{i['file']}: {i['message'][:160]}" for i in findings.items if i["severity"] == "ERROR"],
        "memory": memory_points(text),
    }


def judge_step(step: str, result: dict) -> list[str]:
    problems = []
    # WRITING, manifest and diagnostics.csv are written before any step can fail: a missing file or run_id is a defect.
    if result["status_run_id"] != result["catalog"] or result["manifest_run_id"] != result["catalog"]:
        problems.append(f"run_id export_status {result['status_run_id']!r}, manifest {result['manifest_run_id']!r}, каталог {result['catalog']}")
    if not result["diagnostics"]:
        problems.append("нет diagnostics.csv")
    if step == "ok":
        if result["status"] != "COMPLETE" or result["coverage_status"] != "COMPLETE":
            problems.append(f"ожидался COMPLETE/COMPLETE, получено {result['status']}/{result['coverage_status']}")
        if result["errors"]:
            problems.append(f"ERROR в diagnostics.csv: {result['errors']}")
        if result["package_verdict"] != "PASS":
            problems.append(f"check_e06 package {result['package_verdict']}, ошибок {len(result['package_errors'])}: {result['package_errors'][:3]}")
    else:
        if result["package_errors"]:
            problems.append(f"сбой {step}: ошибки формы каталога {result['package_errors'][:3]}")
        if result["status"] != "FAILED":
            problems.append(f"сбой {step}: ожидался FAILED, получено {result['status']}")
        if result["coverage_status"] != "INCOMPLETE":
            problems.append(f"сбой {step}: coverage_status {result['coverage_status']}, ожидался INCOMPLETE")
        if not result["errors"]:
            problems.append(f"сбой {step}: нет ERROR в diagnostics.csv")
    if result["leftovers"]:
        problems.append(f"оставлены временные файлы {result['leftovers']}")
    if result["end"] != result["status"]:
        problems.append(f"итог журнала {result['end']}, export_status {result['status']}")
    return problems


def judge_series(operations: list[dict], runs: list[str], before: dict, after: dict, exchange: Path) -> dict:
    report = {"runs": [], "problems": []}
    for operation in operations:
        if operation["name"] == "check":
            if operation["diagnostics"] or not operation["loaded"]:
                report["problems"].append(f"загрузка ERT: {operation['diagnostics'] or 'не загружен'}")
            continue
        lines = operation["actual"]
        catalogs = [line[len("catalog:"):] for line in lines if line.startswith("catalog:")]
        statuses = [line[len("status:"):] for line in lines if line.startswith("status:")]
        steps = steps_of(operation["name"])
        if any(line.startswith("error:") for line in lines) or len(catalogs) != len(steps) or len(statuses) != len(steps):
            report["problems"].append(f"{operation['name']}: шагов {len(steps)}, каталогов {len(catalogs)}, ответ {lines[-3:]}")
        for step, catalog, said in zip(steps, catalogs, statuses):
            path = Path(catalog)
            if not catalog or not path.is_dir():
                report["problems"].append(f"{operation['name']} {step}: каталог не создан ({said})")
                continue
            result = {"run": operation["name"], "step": step, "said": said, **outcome(path)}
            result["problems"] = judge_step(step, result)
            if said != result["status"]:
                result["problems"].append(f"модуль сообщил {said}, в export_status {result['status']}")
            if path.name in before:
                result["problems"].append("каталог существовал до сеанса")
            report["runs"].append(result)
            report["problems"] += [f"{path.name} ({step}): {p}" for p in result["problems"]]
    changed = sorted(name for name, files in before.items() if after.get(name) != files)
    if changed:
        report["problems"].append(f"изменены прежние каталоги: {changed}")
    report["previous_directories_unchanged"] = len(before) - len(changed)
    report["new_directories"] = sorted(set(after) - set(before))
    return report


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    runner.session_arguments(parser)
    parser.add_argument("--run", action="append", required=True, help="full or full_<step>_<step>...; steps: " + ", ".join(STEPS))
    parser.add_argument("--ert", type=Path, default=runner.ROOT / "1cv77-extforms" / "kudir_export.ert")
    args = parser.parse_args(argv)
    for value in (args.start, args.end):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parser.error("dates are YYYY-MM-DD")
    for run in args.run:
        if not RUN_NAME.fullmatch(run):
            parser.error(f"--run {run!r}: full or full_<step>_<step>..., steps {', '.join(STEPS)}")
    if len(set(args.run)) != len(args.run):
        parser.error("--run values must differ: each names its protocol file")
    exchange = exchange_root()
    before = tree_hashes(exchange)
    series: dict = {}

    def judge(operations: list[dict]) -> bool:
        series.update(judge_series(operations, args.run, before, tree_hashes(exchange), exchange))
        return bool(series["problems"])

    scope = f"KUDiR E0.6 FULL runs {args.run} on {args.base or 'base-for-test'}"
    code, report = runner.guarded_session(args, ["check", *args.run], runner.CONTROLLER, args.ert, scope, judge)
    # A blocked session (e.g. the deadline of 1C shutdown after the runs) skips the judge in guarded_session; the runs
    # it did finish are still judged, and the hashes taken before the session are kept for a later check by hand.
    operations = (report.get("kudir") or {}).get("operations") or []
    if not series and operations:
        judge(operations)
    series["session_status"] = report.get("status")
    if args.output.is_dir():
        (args.output / "before.json").write_text(json.dumps(before, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (args.output / "series.json").write_text(json.dumps(series, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    brief = {"problems": series.get("problems"), "previous_directories_unchanged": series.get("previous_directories_unchanged"),
             "runs": [{k: r[k] for k in ("step", "catalog", "status", "coverage_status", "errors", "first_errors", "package_verdict")}
                      for r in series.get("runs", [])]}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
