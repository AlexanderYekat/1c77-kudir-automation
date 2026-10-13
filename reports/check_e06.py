"""Read-only checks for E0.6 of plan No. 13 (the FULL package, publication, recovery after a failure).

    python reports/check_e06.py static
    python reports/check_e06.py package exchange/KUDIR4_20261013_001 [--json out.json]
    python reports/check_e06.py compare exchange/KUDIR4_20261012_005 exchange/KUDIR4_20261013_001
    python reports/check_e06.py memory exchange/KUDIR4_20261013_001 [exchange/KUDIR4_20261013_002 ...]

static  - the narrow text checks of check_e02 with the form button ВыгрузкаПолная(). It is not a 1C compiler.
package - one directory written by the «Выгрузка» button (КУДиР4_ПолнаяВыгрузка). A published package (export_status
          COMPLETE) must have the eight files of №1 §5 and nothing else but the technical 1c_export.log and csv_selftest.csv,
          no *.tmp left by the publication, manifest coverage_status=COMPLETE, no ERROR in diagnostics.csv and the log END
          итог=COMPLETE; then every check of the content made by check_e05 (ledger62, documents, snapshots and their
          reconciliations with zero tolerance, quarters REFRESHED by this run, id_map references and orphans, CSV
          self-test). A FAILED or WRITING directory is never accepted (verdict FAIL): its manifest must keep
          coverage_status=INCOMPLETE and the reason must be an ERROR of diagnostics.csv or, when diagnostics.csv was already
          closed, of the log. The files it did write are still checked, but their content findings (a stopped run leaves
          partial files) are reported as UNPUBLISHED, not ERROR: only the form of the failed directory is judged.
compare - two runs over the same area: ledger62, documents, id_map, snapshots and controls equal (check_e05 compare),
          manifests equal except run_id. An E0.5 set may be the first one: its manifest is not compared.
memory  - the memory points of the logs (КУДиР4_Память, №4 §15) as a table per run, in MB.

Exit codes: 0 - PASS; 1 - ERROR findings or not a published package (FAIL).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import check_e02 as e02  # noqa: E402  shared CSV reader, ID parsing, id_map checks, static checks
import check_e03 as e03  # noqa: E402  ledger62.csv checks
import check_e04 as e04  # noqa: E402  documents.csv checks
import check_e05 as e05  # noqa: E402  snapshots, reconciliations, quarters
from check_e02 import DATE, Findings, read_key_value, read_table  # noqa: E402
from check_e03 import LEDGER_FILE, LEDGER_HEADER  # noqa: E402
from check_e04 import DOCUMENTS_FILE, DOCUMENTS_HEADER  # noqa: E402
from check_e05 import QUARTERS_FILE, QUARTERS_HEADER, SNAPSHOTS_FILE, SNAPSHOTS_HEADER  # noqa: E402

LABEL = "Э0.6"
FORM_ACTION = "ВыгрузкаПолная()"
SELFTEST_FILE = "csv_selftest.csv"
LOG_FILE = "1c_export.log"
# №1 §5: the eight files of a successful run directory, in the contract's order.
PACKAGE_FILES = ["manifest.csv", LEDGER_FILE, DOCUMENTS_FILE, SNAPSHOTS_FILE, QUARTERS_FILE, "id_map.csv", "diagnostics.csv", "export_status.csv"]
# The technical log and the CSV self-test of the exporter are not input tables (№1 §5), but they are its own files.
TECHNICAL_FILES = {LOG_FILE, SELFTEST_FILE}
STATES = {"WRITING", "FAILED", "COMPLETE"}
# Log line of a VAT-on-top document (КУДиР4_ДокументКандидат), the source for the owner's check of such documents.
VAT_ON_TOP_LOG = "Э0.6 НДС сверху: "
MEMORY = re.compile(r"^(\d{4}-\d{2}-\d{2} +\d{1,2}:\d{2}:\d{2}) \| \w+ \| Э0\.6 память \[(.+?)\]: pid=(\d+) private_kb=(\d+) ws_kb=(\d+) "
                    r"virtual_kb=(\d+) peak_ws_kb=(\d+) peak_virtual_kb=(\d+) peak_private_kb=(\d+)", re.M)
MEMORY_FIELDS = ("private_kb", "ws_kb", "virtual_kb", "peak_ws_kb", "peak_virtual_kb", "peak_private_kb")


def window_of(log_text: str | None) -> tuple[str, str] | None:
    found = re.findall(rf"START {LABEL} .*?окно=(\d{{4}}-\d{{2}}-\d{{2}})\.\.(\d{{4}}-\d{{2}}-\d{{2}})", log_text or "")
    return found[0] if found else None


def memory_points(log_text: str) -> list[dict]:
    return [{"time": m[1], "point": m[2], "pid": m[3], **{f: int(v) for f, v in zip(MEMORY_FIELDS, m.groups()[3:])}}
            for m in MEMORY.finditer(log_text or "")]


def check_meta(directory: Path, findings: Findings, summary: dict) -> tuple[str | None, str | None]:
    """manifest.csv, export_status.csv, the set of files and the log END of a FULL run directory (№1 §§5, 23.2)."""
    manifest = read_key_value(directory, "manifest.csv", e02.MANIFEST_KEYS, findings)
    status = read_key_value(directory, "export_status.csv", e02.STATUS_KEYS, findings)
    run_id = directory.name
    state = status.get("status") if status else None
    coverage = manifest.get("coverage_status") if manifest else None
    summary.update({"status": state, "coverage_status": coverage})
    if manifest is not None:
        summary["source_id"] = manifest.get("source_id")
        summary["match_horizon_end"] = manifest.get("match_horizon_end")
        if manifest.get("run_id") != run_id:
            findings.error("CSV_INVALID", "manifest.csv", "run_id", f"run_id {manifest.get('run_id')!r} не равен имени каталога {run_id!r}")
        for key, expected in e02.MANIFEST_FIXED.items():
            if key != "coverage_status" and manifest.get(key) != expected:
                findings.error("CSV_INVALID", "manifest.csv", key, f"{manifest.get(key)!r}, ожидалось {expected!r}")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", manifest.get("source_id", "")):
            findings.error("CSV_INVALID", "manifest.csv", "source_id", f"недопустимый source_id {manifest.get('source_id')!r}")
        if not DATE.fullmatch(manifest.get("match_horizon_end", "")):
            findings.error("CSV_INVALID", "manifest.csv", "match_horizon_end", "не дата YYYY-MM-DD")
    if status is not None:
        if status.get("run_id") != run_id or status.get("schema_version") != "4":
            findings.error("CSV_INVALID", "export_status.csv", "PACKAGE", "run_id/schema_version не совпадают с каталогом и v4")
        if state not in STATES:
            findings.error("CSV_INVALID", "export_status.csv", "status", f"недопустимый статус {state!r}")
    # COMPLETE of export_status and of the coverage go together: a failed run never claims the area (№1 §§4, 23.2).
    if state == "COMPLETE" and coverage != "COMPLETE":
        findings.error("CSV_INVALID", "manifest.csv", "coverage_status", f"export_status COMPLETE, а coverage_status {coverage!r}")
    if state in {"WRITING", "FAILED"} and coverage != "INCOMPLETE":
        findings.error("CSV_INVALID", "manifest.csv", "coverage_status", f"export_status {state}, а coverage_status {coverage!r}: неопубликованный каталог объявляет охват")
    present = sorted(p.name for p in directory.iterdir() if p.is_file())
    summary["files"] = present
    leftovers = [name for name in present if name.endswith(".tmp")]
    extra = [name for name in present if name not in PACKAGE_FILES and name not in TECHNICAL_FILES and name not in leftovers]
    if extra:
        findings.error("CSV_INVALID", "PACKAGE", "PACKAGE", f"посторонние файлы в каталоге пакета: {extra} (№1 §5)")
    if state == "COMPLETE":
        missing = [name for name in PACKAGE_FILES if name not in present]
        if missing:
            findings.error("CSV_INVALID", "PACKAGE", "PACKAGE", f"COMPLETE без обязательных файлов {missing} (№1 §5)")
        if leftovers:
            findings.error("CSV_INVALID", "PACKAGE", "PACKAGE", f"COMPLETE, а временные файлы публикации остались: {leftovers}")
    log = directory / LOG_FILE
    log_text = log.read_bytes().decode("cp1251", "replace") if log.is_file() else None
    end = re.findall(rf"END {re.escape(LABEL)} итог=(\w+)", log_text or "")
    summary["log_result"] = end[-1] if end else None
    if log_text is None:
        findings.error("CSV_INVALID", LOG_FILE, "PACKAGE", "журнал запуска отсутствует")
    elif not end:
        findings.info("RUN_NOT_FINISHED", LOG_FILE, "PACKAGE", "нет строки END: запуск прерван до завершения")
        if state == "COMPLETE":
            findings.error("CSV_INVALID", LOG_FILE, "PACKAGE", "COMPLETE без строки END журнала")
    elif end[-1] != state:
        findings.error("CSV_INVALID", LOG_FILE, "PACKAGE", f"итог журнала {end[-1]}, export_status {state!r}")
    if log_text is not None and window_of(log_text) is None:
        findings.error("CSV_INVALID", LOG_FILE, "PACKAGE", f"нет строки START {LABEL} с окном чтения")
    return state, log_text


def check_diagnostics(directory: Path, findings: Findings, summary: dict, state: str | None, log_text: str | None) -> list[dict]:
    """diagnostics.csv by №1 §27. COMPLETE has no ERROR; FAILED names its reason in diagnostics.csv or, for a failure
    after diagnostics.csv was closed (its own check, the final status), in the log."""
    rows = read_table(directory, "diagnostics.csv", e02.DIAG_HEADER, findings) or []
    for number, row in enumerate(rows, 2):
        if row["severity"] not in {"ERROR", "WARNING"}:
            findings.error("CSV_INVALID", "diagnostics.csv", f"record {number}", f"severity {row['severity']!r}")
        if row["code"] not in e02.DIAG_CODES:
            findings.error("CSV_INVALID", "diagnostics.csv", f"record {number}", f"код {row['code']!r} вне перечня №1 §27")
        if not row["file"] or not row["record_key"] or not row["message"]:
            findings.error("CSV_INVALID", "diagnostics.csv", f"record {number}", "пустые file/record_key/message")
    errors = [row for row in rows if row["severity"] == "ERROR"]
    summary["diagnostics"] = dict(Counter(f"{row['severity']}:{row['code']}" for row in rows))
    if state == "COMPLETE" and errors:
        findings.error("CSV_INVALID", "export_status.csv", "status", f"COMPLETE при {len(errors)} ERROR в diagnostics.csv")
    if state == "FAILED" and not errors and " | ERROR | " not in (log_text or ""):
        findings.error("CSV_INVALID", "export_status.csv", "status", "FAILED без ERROR в diagnostics.csv и в журнале")
    for row in errors[:50]:
        findings.add("REPORTED", row["code"], row["file"], row["record_key"], f"{row['field']}: {row['message']}")
    return rows


def check_package(directory: Path) -> tuple[Findings, dict]:
    findings = Findings()
    summary: dict = {"directory": str(directory)}
    state, log_text = check_meta(directory, findings, summary)
    window = window_of(log_text)
    horizon = summary.get("match_horizon_end")
    published = state == "COMPLETE"

    def table(name: str, header: list[str]) -> list[dict] | None:
        if not published and not (directory / name).is_file():
            findings.info("NOT_CHECKED", name, "PACKAGE", "файл не создан: запуск не опубликован")
            return None
        return read_table(directory, name, header, findings)

    if window and (window[0] != e05.HISTORY_START or window[1] != horizon):
        findings.error("CSV_INVALID", LOG_FILE, "PACKAGE", f"окно {window}: начало - history_start {e05.HISTORY_START}, конец - match_horizon_end {horizon}")
    diagnostics = check_diagnostics(directory, findings, summary, state, log_text)
    # Up to here - the form of the run directory; below - the content of the files it wrote.
    content_from = len(findings.items)
    id_map = table("id_map.csv", e02.ID_MAP_HEADER) or []
    by_key = e02.check_id_map(id_map, findings, summary)
    ledger = table(LEDGER_FILE, LEDGER_HEADER) or []
    e03.check_ledger(ledger, by_key, diagnostics, findings, summary)
    documents = table(DOCUMENTS_FILE, DOCUMENTS_HEADER) or []
    e04.check_documents(documents, by_key, ledger, diagnostics, window, findings, summary)
    snapshots = table(SNAPSHOTS_FILE, SNAPSHOTS_HEADER) or []
    groups = e05.check_snapshots(snapshots, ledger, documents, by_key, diagnostics, horizon, findings, summary)
    if snapshots:
        e05.reconcile_snapshots(groups, ledger, findings, summary)
    quarters = table(QUARTERS_FILE, QUARTERS_HEADER) or []
    e05.check_quarters(quarters, ledger, groups["day"], horizon, directory.name, diagnostics, not published, findings, summary)
    used = {row["source_document_id"] for row in ledger} | {row["payment_id"] for row in ledger} | {row["ДоговорID"] for row in ledger}
    used |= {row["document_id"] for row in documents} | {row["ДоговорID"] for row in documents}
    used |= {row["ДоговорID"] for row in snapshots} | {row["BeforeDocumentID"] for row in snapshots}
    orphans = [key for key in by_key if key[1] not in used]
    if orphans:
        message = f"записей id_map без ссылок из ledger62/documents/balance_snapshots: {len(orphans)}"
        if published:
            findings.error("CSV_INVALID", "id_map.csv", orphans[0][1], message)
        else:
            findings.info("NOT_CHECKED", "id_map.csv", orphans[0][1], message + " (запуск не опубликован)")
    e02.check_selftest(table(SELFTEST_FILE, e02.SELFTEST_HEADER), SELFTEST_FILE, findings, summary)
    summary["memory"] = memory_points(log_text or "")
    if published and not summary["memory"]:
        findings.info("NOT_CHECKED", LOG_FILE, "PACKAGE", "в журнале нет замеров памяти")
    if not published:
        # A stopped run leaves partial files: a missing snapshot or quarter there is a consequence of the stop, not a
        # separate defect. Such findings stay visible as UNPUBLISHED; the directory is not accepted anyway.
        content = [item for item in findings.items[content_from:] if item["severity"] == "ERROR"]
        for item in content:
            item["severity"] = "UNPUBLISHED"
        summary["unpublished_content_findings"] = len(content)
        findings.add("NOT_PUBLISHED", "NOT_PUBLISHED", "export_status.csv", "PACKAGE", f"export_status {state!r}: пакет не принимается")
    summary["verdict"] = "PASS" if published and not findings.errors else "FAIL"
    summary["files_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()}
    return findings, summary


def compare_packages(first: Path, second: Path) -> tuple[Findings, dict]:
    findings, summary = e05.compare_packages(first, second)
    # check_e05 reads the window from an E0.5 log; an E0.6 run has its own START line - compare the declared area.
    findings.items = [item for item in findings.items if not (item["file"] == LOG_FILE and "окна чтения" in item["message"])]
    local = Findings()
    manifests = [read_key_value(d, "manifest.csv", e02.MANIFEST_KEYS, local) or {} for d in (first, second)]
    for key in e02.MANIFEST_KEYS:
        if key in {"run_id", "coverage_status"}:
            continue
        if manifests[0].get(key) != manifests[1].get(key):
            findings.error("COMPARE", "manifest.csv", key, f"{manifests[0].get(key)!r} и {manifests[1].get(key)!r}")
    summary["manifest"] = {key: (manifests[0].get(key), manifests[1].get(key)) for key in ("source_id", "match_horizon_end", "coverage_status")}
    return findings, summary


def memory_table(directories: list[Path]) -> str:
    lines = ["| Запуск | Точка | Время | Частная, МБ | Рабочий набор, МБ | Виртуальная, МБ | Пик рабочего набора, МБ | Пик виртуальной, МБ | Пик частной, МБ |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for directory in directories:
        log = directory / LOG_FILE
        points = memory_points(log.read_bytes().decode("cp1251", "replace") if log.is_file() else "")
        if not points:
            lines.append(f"| {directory.name} | замеров нет | | | | | | | |")
        for p in points:
            mb = [f"{p[f] / 1024:.1f}" for f in MEMORY_FIELDS]
            lines.append(f"| {directory.name} | {p['point']} | {p['time'].split()[-1]} | " + " | ".join(mb) + " |")
    return "\n".join(lines)


def check_static() -> tuple[Findings, dict]:
    return e02.check_static(FORM_ACTION)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("static")
    one = sub.add_parser("package")
    one.add_argument("directory", type=Path)
    one.add_argument("--json")
    two = sub.add_parser("compare")
    two.add_argument("first", type=Path)
    two.add_argument("second", type=Path)
    mem = sub.add_parser("memory")
    mem.add_argument("directories", type=Path, nargs="+")
    args = parser.parse_args(argv)
    if args.mode == "static":
        return e02.report(*check_static(), None)
    if args.mode == "package":
        return e02.report(*check_package(args.directory), args.json)
    if args.mode == "compare":
        return e02.report(*compare_packages(args.first, args.second), None)
    print(memory_table(args.directories))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
