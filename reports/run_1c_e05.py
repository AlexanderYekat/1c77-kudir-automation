"""Load kudir_export.ert in a real 1C 7.7 session and optionally run the E0.5 set, through the harness77 platform core.

    python reports/run_1c_e05.py --output <fresh ASCII dir> [--command check|e05] [--start 2026-01-01 --end 2026-01-31]

harness77 (C:\\MyProjects\\harness77, research/platform77) owns the guarded session: the 1C OLE server under x86 CDB on
a private desktop, ownership of the own process verified before Initialize (other 1C sessions stay untouched), the
coordinated shutdown without AV, the pinned base-for-test and binaries. This script adds only the KUDiR client: a 1C++
controller opens the ERT with ОткрытьФорму("Отчет", <список KUDIR4-HEADLESS>, <файл>) - the whole module is compiled on
loading, compiler messages are saved through MessageView - and with "e05" the form runs КУДиР4_НаборЭ05 for the window.
The export writes to %KUDIR_PROTO_ROOT%\\exchange as from the form. The two session limits of the core (145 s total,
cscript //T:140) are raised for this run only; nothing in harness77 is changed.

--base takes another copy of the same base instead of base-for-test (the owner confirmed on 12.10.2026 that the bases
on this computer are copies). The core pins the path of base-for-test; for another directory this script applies the
same preflight except the path: the pinned configuration and binaries (1Cv7.MD, 1Cv7.DD, 1CPP.dll, FormEx.dll, exe,
helper), x86 CDB, a fresh ASCII output outside the base. A base held by 1C in exclusive mode (its DBF cannot be read)
is refused before the session: the owner's 1C is never closed from here.

Exit codes: 0 - loaded (and the set finished with END PASS/INCOMPLETE); 1 - compiler errors or the set failed;
2 - the session itself was blocked (see run.json).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(r"C:\MyProjects\harness77")
EXE = Path(r"C:\Program Files (x86)\1Cv77\BIN\1cv7l.exe")
CDB = Path(r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\cdb.exe")
COM_CLEANUP = Path(".artifacts/platform77/com-coordinated-build-v3-20261011/com_cleanup77.dll")
IDENTITY = Path(".artifacts/platform77/proxy-challenge-build-20261011/proxy_identity77.dll")
PROTOCOL_DIRECTORY = "protocols"
MARKER = "KUDIR4-HEADLESS"

CONTROLLER = """Функция Kudir77(Путь, Команда, Протокол, Метка, Нач, Кон) Экспорт
    Сообщения77 = СоздатьОбъект("MessageView");
    Сообщения77.SetSystemMessages();
    Сообщения77.Clear();
    Сообщить("HARNESS77-BEGIN:" + Метка);
    Параметры77 = СоздатьОбъект("СписокЗначений");
    Параметры77.ДобавитьЗначение("KUDIR4-HEADLESS");
    Параметры77.ДобавитьЗначение(Команда);
    Параметры77.ДобавитьЗначение(Дата(Число(Лев(Нач, 4)), Число(Сред(Нач, 6, 2)), Число(Прав(Нач, 2))));
    Параметры77.ДобавитьЗначение(Дата(Число(Лев(Кон, 4)), Число(Сред(Кон, 6, 2)), Число(Прав(Кон, 2))));
    Контекст77 = Параметры77;
    Ответ77 = "selected:" + Путь + РазделительСтрок;
    Попытка
        Открыта77 = ОткрытьФорму("Отчет", Контекст77, Путь);
        Ответ77 = Ответ77 + "open-return:" + СокрЛП(Строка(Открыта77)) + РазделительСтрок;
        Если Открыта77 = 1 Тогда
            Контекст77.Форма.Закрыть(0);
            Ответ77 = Ответ77 + "unexpected-visible-form" + РазделительСтрок;
        КонецЕсли;
    Исключение
        Ответ77 = Ответ77 + "open-error:" + ОписаниеОшибки() + РазделительСтрок;
    КонецПопытки;
    Для Номер77 = 5 По Параметры77.РазмерСписка() Цикл
        Ответ77 = Ответ77 + Параметры77.ПолучитьЗначение(Номер77) + РазделительСтрок;
    КонецЦикла;
    Контекст77 = 0;
    Параметры77 = 0;
    Сообщить("HARNESS77-END:" + Метка);
    Сообщения77.SaveToFile(Протокол);
    Возврат Ответ77;
КонецФункции
"""

CLIENT = r'''Option Explicit
Dim v, fso, result, actualBase, controller, runId, eventPath, command, t
Dim sourceController, ertPath, evidencePath
Set fso = CreateObject("Scripting.FileSystemObject")
runId = __RUN_ID__
ertPath = __ERT_PATH__
eventPath = fso.BuildPath(fso.GetParentFolderName(WScript.Arguments(2)), "ert-events.jsonl")
__SOURCES__
__UTILITIES__
On Error Resume Next
Set v = CreateObject("V77L.Application")
CheckCOM "activation"
__PROXY_IDENTITY__
result = v.Initialize(v.RMTrade, "/D""" & WScript.Arguments(0) & """ /N""" & WScript.Arguments(1) & """", "NO_SPLASH_SHOW")
CheckCOM "initialize"
If Not result Then Fail "initialize", "returned false"
actualBase = v.EvalExpr("КаталогИБ()")
CheckCOM "base-path"
If LCase(actualBase) <> LCase(WScript.Arguments(0) & "\") Then Fail "base-path", "unexpected database"
Emit "phase", "base", "ok", 0
Set controller = v.CreateObject("ВыполняемыйМодуль")
CheckCOM "create-controller"
controller.AssignSource sourceController
CheckCOM "assign-controller"
result = controller.Compile()
CheckCOM "compile-controller"
If result <> 1 Then Fail "compile-controller", CStr(result)
result = controller.Execute()
CheckCOM "execute-controller"
If result <> 1 Then Fail "execute-controller", CStr(result)
Emit "phase", "controller", "ok", 0
For Each command In Array(__COMMANDS__)
    evidencePath = fso.BuildPath(__PROTOCOL_ROOT__, command & ".txt")
    CallMarker command, "started"
    t = CDbl(Timer)
    result = controller.Kudir77(ertPath, command, evidencePath, runId & ":" & command, __START__, __END__)
    CheckCOM command
    Emit "ert", command, CStr(result), Elapsed(t)
    CallMarker command, "finished"
Next
Set controller = Nothing
CheckCOM "release-controller"
__SHUTDOWN__
Set v = Nothing
CheckCOM "release"
ShutdownEvent "server_released", "ok"
Log "released"
Emit "complete", "ert", "done", 0

Sub CallMarker(name, state)
    Dim path, marker
    path = fso.BuildPath(fso.GetParentFolderName(eventPath), "ert-" & name & "-" & state)
    Set marker = fso.CreateTextFile(path & ".tmp", False)
    marker.WriteLine runId
    marker.Close
    fso.MoveFile path & ".tmp", path
    CheckCOM "call-marker-" & state
End Sub
'''


def prepare(out: Path, ert: Path, commands: list[str], start: str, end: str) -> tuple[dict, dict]:
    from module_suite77 import vb_assignment, vb_literal
    from shutdown77 import atomic_json, prepare_shutdown, sha256

    run_id = str(uuid.uuid4())
    out.mkdir(parents=False, exist_ok=False)
    target = out / "kudir_export.ert"
    target.write_bytes(ert.read_bytes())
    (out / "controller.1s").write_text(CONTROLLER, encoding="utf-8")
    protocols = out / PROTOCOL_DIRECTORY
    protocols.mkdir()
    platform = HARNESS / "research" / "platform77"
    suite = (platform / "module_suite77.vbs").read_text(encoding="utf-8")
    utilities = suite[suite.index("Function Quote"):suite.index("Sub CompileCase")]
    shutdown, shutdown_manifest = prepare_shutdown(out, run_id)
    client = CLIENT.replace("__PROXY_IDENTITY__", (platform / "proxy_identity77.vbs").read_text(encoding="utf-8"))
    for key, value in {"RUN_ID": vb_literal(run_id), "ERT_PATH": vb_literal(str(target)),
                       "SOURCES": vb_assignment("sourceController", CONTROLLER), "UTILITIES": utilities,
                       "COMMANDS": ", ".join(vb_literal(c) for c in commands), "SHUTDOWN": shutdown,
                       "START": vb_literal(start), "END": vb_literal(end), "PROTOCOL_ROOT": vb_literal(str(protocols))}.items():
        client = client.replace("__" + key + "__", value)
    (out / "client.vbs").write_text(client, encoding="utf-16")
    shutdown_manifest["sources"]["client.vbs"] = sha256(out / "client.vbs")
    manifest = {"schema_version": 1, "run_id": run_id, "commands": commands, "window": [start, end],
                "ert_path": str(target), "ert_source": str(ert), "ert_sha256": sha256(target),
                "sources": {name: sha256(out / name) for name in ("kudir_export.ert", "controller.1s", "client.vbs")}}
    atomic_json(out / "kudir-manifest.json", manifest)
    atomic_json(out / "shutdown-manifest.json", shutdown_manifest)
    return manifest, shutdown_manifest


def preflight_copy(base: Path, exe: Path, cdb: Path, helper: Path, out: Path) -> None:
    """The preflight of the core (run_shutdown77.preflight) for a copy of the base outside .local/bases."""
    from shutdown77 import PINNED, sha256

    if out.is_relative_to(base) or base.is_relative_to(out):
        raise ValueError("output and database trees must not overlap")
    if any(c in str(out) for c in '"; \r\n') or not str(out).isascii():
        raise ValueError("output must be ASCII without spaces or debugger separators")
    if out.exists():
        raise ValueError("output must be a fresh directory")
    for name, path in {"exe": exe, "helper": helper, **{n: base / n for n in ("1Cv7.MD", "1Cv7.DD", "1CPP.dll", "FormEx.dll")}}.items():
        if sha256(path) != PINNED[name]:
            raise ValueError("unqualified binary/configuration: " + name)
    data = cdb.read_bytes()
    pe = int.from_bytes(data[0x3c:0x40], "little")
    if data[:2] != b"MZ" or data[pe:pe + 6] != b"PE\0\0\x4c\x01":
        raise ValueError("x86 CDB is required")
    try:
        with (base / "1SJOURN.DBF").open("rb") as stream:
            stream.read(32)
    except PermissionError:
        raise ValueError("the base is open in 1C in exclusive mode: close that 1C first") from None


def read_results(out: Path, manifest: dict) -> dict:
    """Operations of the client: protocol of compiler/run messages, values the ERT appended, the run directory."""
    result = {"operations": [], "issues": []}
    try:
        events = [json.loads(line) for line in (out / "ert-events.jsonl").read_text(encoding="utf-16").splitlines()]
    except (OSError, UnicodeError, ValueError) as error:
        result["issues"].append(f"events unavailable: {error}")
        return result
    result["events"] = [(e.get("kind"), e.get("name")) for e in events]
    for event in events:
        if event.get("run_id") != manifest["run_id"]:
            result["issues"].append("foreign run_id in events")
        if event.get("kind") != "ert":
            if event.get("kind") == "blocked":
                result["issues"].append(f"client blocked at {event.get('name')}: {event.get('value')}")
            continue
        name, actual = event["name"], event["value"]
        path = out / PROTOCOL_DIRECTORY / (name + ".txt")
        try:
            data = path.read_bytes()
            protocol = data.decode("utf-16" if data.startswith(b"\xff\xfe") else "cp1251")
        except (OSError, UnicodeError) as error:
            protocol = ""
            result["issues"].append(f"protocol unavailable: {error}")
        lines = actual.splitlines()
        diagnostics = [{"line": int(m[1]), "message": m[2]} for m in re.finditer(r"^\{[^\r\n]*\(([0-9]{1,9})\)\}:[ \t]*([^\r\n]*)", protocol, re.M)]
        catalog = next((line[len("catalog:"):] for line in lines if line.startswith("catalog:")), "")
        row = {"name": name, "elapsed_ms": event.get("elapsed_ms"), "actual": lines, "diagnostics": diagnostics,
               "loaded": any(line.startswith("loaded:") for line in lines), "catalog": catalog,
               "protocol_lines": len(protocol.splitlines()), "protocol": str(path)}
        if catalog:
            log = Path(catalog) / "1c_export.log"
            text = log.read_bytes().decode("cp1251", "replace") if log.is_file() else ""
            end = re.findall(r"END Э0\.5 итог=(\w+)", text)
            row["set_result"] = end[-1] if end else None
            row["set_summary"] = next((l.split("| ", 2)[-1] for l in text.splitlines() if "Э0.5 итог" in l), "")
        result["operations"].append(row)
    return result


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True, help="fresh directory, ASCII without spaces (CDB technical files)")
    parser.add_argument("--command", choices=("check", "e05"), default="check")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-01-31")
    parser.add_argument("--ert", type=Path, default=ROOT / "1cv77-extforms" / "kudir_export.ert")
    parser.add_argument("--base", type=Path, help="another copy of the same base (default: harness77 base-for-test)")
    parser.add_argument("--user", default="ВВВ")
    parser.add_argument("--session-seconds", type=int, default=900, help="whole guarded session, including shutdown")
    args = parser.parse_args(argv)
    for value in (args.start, args.end):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parser.error("dates are YYYY-MM-DD")
    if "KUDIR_PROTO_ROOT" not in os.environ:
        os.environ["KUDIR_PROTO_ROOT"] = str(ROOT)

    sys.path.insert(0, str(HARNESS / "research" / "platform77"))
    import desktop77
    import shutdown77
    from concurrent77 import IDENTITY_SHA
    from run_shutdown77 import preflight
    from shutdown77 import MODE, assess, atomic_json, sha256

    # The core's limits fit its 42-probes; a KUDiR set runs for minutes. Raise them for this process only.
    shutdown77.Deadline.__init__.__defaults__ = (args.session_seconds, 10)
    original = desktop77.DesktopProcess

    class LongClient(original):
        def __init__(self, command, desktop, stdout, cwd=None):
            command = [f"//T:{args.session_seconds - 5}" if part == "//T:140" else part for part in command]
            super().__init__(command, desktop, stdout, cwd)

    desktop77.DesktopProcess = LongClient

    commands = ["check"] if args.command == "check" else ["check", "e05"]
    report = {"schema_version": 1, "scope": f"KUDiR E0.5 ERT on {args.base or 'base-for-test'}", "shutdown_mode": MODE, "fault": "none",
              "status": "blocked", "ert_operation_timeout_seconds": args.session_seconds - 30,
              "identity_observer_profile": "challenge-only-v2", "client_log_profile": "held-streams-v2"}
    gate = HARNESS / ".artifacts" / "platform77" / "active-session.json"
    owns_gate = False
    out = args.output.resolve()
    try:
        qualified = (HARNESS / ".local" / "bases" / "base-for-test").resolve(strict=True)
        base = args.base.resolve(strict=True) if args.base else qualified
        helper, identity = (HARNESS / COM_CLEANUP).resolve(strict=True), (HARNESS / IDENTITY).resolve(strict=True)
        if sha256(identity) != IDENTITY_SHA:
            raise ValueError("unqualified proxy inspector")
        (preflight if base == qualified else preflight_copy)(base, EXE.resolve(strict=True), CDB.resolve(strict=True), helper, out)
        ert = args.ert.resolve(strict=True)
        if not (base / "kudir_source_id.txt").is_file():
            raise ValueError("the base has no kudir_source_id.txt: the export would refuse to start")
        gate.parent.mkdir(parents=True, exist_ok=True)
        with gate.open("x", encoding="utf-8") as stream:
            owns_gate = True
            stream.write(json.dumps({"output": str(out), "scope": "KUDiR ERT"}))
        manifest, shutdown_manifest = prepare(out, ert, commands, args.start, args.end)
        report["run_id"] = manifest["run_id"]
        report["ert_commands"] = commands
        from session77 import run_session
        with desktop77.PrivateDesktop() as desktop:
            report = run_session(base, EXE, CDB, helper, args.user, out, report, desktop=desktop.name, identity_dll=identity)
        report["desktop_closed"] = True
        report["shutdown_assessment"] = assess(out, shutdown_manifest, report)
        report["kudir"] = read_results(out, manifest)
        operations = report["kudir"]["operations"]
        blocked = bool(report.get("error") or report["kudir"]["issues"] or report["shutdown_assessment"]["status"] != "passed"
                       or [o["name"] for o in operations] != commands)
        failed = any(o["diagnostics"] or not o["loaded"] for o in operations) or any(
            o["name"] == "e05" and o.get("set_result") not in ("PASS", "INCOMPLETE") for o in operations)
        report["status"] = "blocked" if blocked else "failed" if failed else "passed"
        atomic_json(out / "run.json", report)
        if report.get("cleanup_verified") and report["desktop_closed"]:
            gate.unlink()
            owns_gate = False
        brief = {k: report.get(k) for k in ("status", "run_id", "elapsed_seconds", "error", "timeout_phase", "cleanup_verified")}
        brief["shutdown"] = report["shutdown_assessment"]["status"], report["shutdown_assessment"]["issues"]
        brief["operations"] = [{k: o.get(k) for k in ("name", "elapsed_ms", "loaded", "diagnostics", "actual", "set_result", "set_summary")} for o in operations]
        brief["issues"] = report["kudir"]["issues"]
        print(json.dumps(brief, ensure_ascii=False, indent=2))
        return {"passed": 0, "failed": 1, "blocked": 2}[report["status"]]
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["active_gate_retained"] = owns_gate
        if out.exists():
            atomic_json(out / "run.json", report)
        print(json.dumps({k: report.get(k) for k in ("status", "error", "active_gate_retained")}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
