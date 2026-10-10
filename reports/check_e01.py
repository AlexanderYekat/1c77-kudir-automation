"""Read-only checks for E0.1. This is NOT a 1C compiler or a database test.

Run: python reports/check_e01.py --output-prefix <new-prefix>
Optional full legacy suite: python reports/check_e01.py --run-tests --output-prefix <new-prefix>
Output is restricted to this reports directory; source/ERT/database are not edited.
Existing protocols are not overwritten unless --overwrite is given.
The optional ERT reader is discovered from the project's sync_modules.bat.
Static checks include 1C 7.7 declaration order (calls before definition need "Далее"),
forward signatures, form identifiers, built-in names used as variables and global calls
missing from the installed 1C 7.7 syntax helper (both modules); they are narrow text checks,
not a compiler.
"""

from pathlib import Path
from collections import Counter
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
TOKEN = re.compile(r'"(?:[^"\n]|"")*"|//[^\n]*')
# Global functions of 1C 7.7 that the modules call; such a name cannot be a parameter or variable
# ("Ожидается имя формального параметра", "Ожидается символ '('"). Not a complete list.
BUILTIN_NAMES = {name.lower() for name in """Строка Число Дата Мин Макс Окр Цел Лев Прав Сред СтрДлина СокрЛ СокрП
СокрЛП Найти СтрЗаменить Симв КодСимв ВРег НРег ПустаяСтрока Формат Шаблон Пропись ТекущаяДата ТекущееВремя
ДобавитьМесяц НачГода КонГода НачМесяца КонМесяца НачКвартала КонКвартала ДатаГод ДатаМесяц ДатаЧисло Сообщить
Состояние Предупреждение Вопрос СоздатьОбъект ПустоеЗначение ТипЗначения ТипЗначенияСтр ЗначениеВСтрокуВнутр
ЗначениеИзСтрокиВнутр ОписаниеОшибки КаталогИБ КаталогПрограммы""".split()}
# Syntax helper of the installed 1C 7.7: the list of functions and methods the platform knows.
SYNTAX_HELPER = Path(r"C:\Program Files (x86)\1Cv77\BIN\1cv7Lang.als")
CALL_KEYWORDS = {"если", "иначеесли", "пока", "и", "или", "не", "возврат"}


def read(path):
    return path.read_text(encoding="utf-8-sig")


def without_comments(text):
    return TOKEN.sub(lambda m: "" if m[0].startswith("//") else m[0], text)


def code_only(text):
    return TOKEN.sub("", text)


def canonical(text):
    return "\n".join(line.strip() for line in without_comments(text).splitlines() if line.strip())


def routines(text):
    result = {}
    duplicates = []
    forward = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*(Функция|Процедура)\s+(\w+)\s*\(", line, re.I)
        if not match:
            continue
        kind, name = match.groups()
        if re.search(r"\bдалее\b", line, re.I):
            forward.add(name)
            continue
        end = "КонецФункции" if kind.lower() == "функция" else "КонецПроцедуры"
        stop = next(i for i in range(index + 1, len(lines)) if lines[i].strip().lower().startswith(end.lower()))
        if name in result:
            duplicates.append(name)
        result[name] = {"line": index + 1, "end": stop + 1, "text": "\n".join(lines[index:stop + 1])}
    return result, duplicates, forward


def calls_before_definition(text, definitions, forward):
    # 1C 7.7 compiles in one pass: a routine called above its body needs a "Далее" declaration.
    issues = []
    for number, line in enumerate(code_only(text).splitlines(), 1):
        if re.match(r"\s*(Функция|Процедура)\s", line, re.I):
            continue
        for name in re.findall(r"(?<![.\w])(\w+)\s*\(", line):
            if name in definitions and name not in forward and number < definitions[name]["line"]:
                issues.append({"line": number, "name": name, "defined": definitions[name]["line"]})
    return issues


def forward_mismatches(text):
    signature = re.compile(r"\s*(Функция|Процедура)\s+(\w+)\s*\((.*?)\)\s*(далее)?\s*$", re.I)
    declared, defined = {}, {}
    for line in code_only(text).splitlines():
        match = signature.match(line)
        if match:
            target = declared if match[4] else defined
            target[match[2]] = (match[1].lower(), re.sub(r"\s+", " ", match[3].strip()).lower())
    return sorted(name for name in declared if name in defined and declared[name] != defined[name])


def builtin_name_clashes(text):
    # Parameters, Перем, assignment targets and loop counters named like a 1C 7.7 global function.
    issues = []
    for number, line in enumerate(code_only(text).splitlines(), 1):
        names = []
        header = re.match(r"\s*(?:Функция|Процедура)\s+\w+\s*\((.*?)\)", line, re.I)
        if header:
            names = [re.sub(r"(?i)^знач\s+", "", p.strip()).split("=")[0].strip() for p in header[1].split(",")]
        else:
            for declared in re.findall(r"(?i)^\s*Перем\s+([^;]+);", line):
                names += [p.strip().split("[")[0].strip() for p in declared.split(",")]
            names += re.findall(r"(?:^|;)\s*(\w+)\s*=(?!=)", line)
            names += re.findall(r"(?i)\bДля\s+(\w+)\s*=", line)
        issues += [{"line": number, "name": name} for name in names if name.lower() in BUILTIN_NAMES]
    return issues


def syntax_helper_names():
    if not SYNTAX_HELPER.is_file():
        return None
    text = SYNTAX_HELPER.read_bytes().decode("cp1251", "replace")
    names = set()
    for pair in re.findall(r'\{"Item","[^"]*","([^"]*)","([^"]*)"', text):
        names.update(re.match(r"\w*", name.strip())[0].lower() for name in pair)
    return names - {""}


def unknown_global_calls(text, known):
    # A call without "." must be a local routine or a global function of 1C 7.7
    # (otherwise "Процедура не обнаружена", e.g. ОбработкаПрерыванияПользователя from 1C 8).
    local = {name.lower() for name in re.findall(r"(?im)^\s*(?:Функция|Процедура)\s+(\w+)", text)}
    found = {}
    for number, line in enumerate(code_only(text).splitlines(), 1):
        if re.match(r"\s*(Функция|Процедура)\s", line, re.I):
            continue
        for name in re.findall(r"(?<![.\w])(\w+)\s*\(", line):
            if name.lower() not in local | known | CALL_KEYWORDS:
                found.setdefault(name, []).append(number)
    return found


def dialog_controls(dialog):
    controls = []
    for line in dialog.decode("cp1251").splitlines():
        fields = re.findall(r'"((?:[^"]|"")*)"', line)
        if line.startswith('{"') and len(fields) > 12 and fields[1].isupper():
            controls.append({"caption": fields[0], "class": fields[1], "formula": fields[11], "identifier": fields[12]})
    return controls


def undeclared_form_identifiers(module_text, controls):
    # Form attributes of this processing are named "выб..."; each must be a dialog identifier or a module variable.
    code = code_only(module_text)
    declared = {c["identifier"] for c in controls if c["identifier"]}
    for names in re.findall(r"(?im)^\s*Перем\s+([^;]+);", code):
        declared.update(part.split()[0] for part in names.split(",") if part.strip())
    return sorted(set(re.findall(r"\bвыб\w+", code)) - declared)


def lexical_checks(text):
    # Deliberately narrow checks: no claims about 1C grammar, types or API.
    code = "\n".join(line for line in code_only(text).splitlines() if not re.search(r"\bдалее\b", line, re.I))
    stack = []
    errors = []
    pairs = {"конецесли": "если", "конеццикла": "цикл", "конецпопытки": "попытка", "конецфункции": "функция", "конецпроцедуры": "процедура"}
    for match in re.finditer(r"\b(?:Если|КонецЕсли|Цикл|КонецЦикла|Попытка|КонецПопытки|Функция|КонецФункции|Процедура|КонецПроцедуры)\b", code, re.I):
        token = match[0].lower()
        if token in pairs:
            if not stack or stack.pop() != pairs[token]:
                errors.append(f"mismatched {token}")
        else:
            stack.append(token)
    balance = 0
    for c in code:
        if c == "(":
            balance += 1
        elif c == ")":
            balance -= 1
            if balance < 0:
                errors.append("unmatched closing parenthesis")
    return {"block_errors": errors, "unclosed_blocks": stack, "parentheses_balance": balance}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def ert_checks():
    sync = read(ROOT / "sync_modules.bat")
    match = re.search(r'SYNC_PY=([^"\r\n]+)', sync)
    reader = Path(match[1]).with_name("ert_module.py") if match else None
    if not reader or not reader.is_file():
        return {"status": "NOT_CHECKED", "reason": "local read-only ERT reader unavailable"}
    module = load_module("_e01_ert_reader", reader)
    results = {"reader": str(reader), "reader_sha256": hashlib.sha256(reader.read_bytes()).hexdigest()}
    for stem in ("kudir_export", "kudir_export_agent"):
        path = ROOT / "1cv77-extforms" / f"{stem}.ert"
        extracted = module.extract_text(path).replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff\n")
        source = read(ROOT / "1cv77" / f"{stem}.txt").strip("\ufeff\n")
        # The writer stores non-cp1251 characters (e.g. arrows in comments) as fixed fallbacks.
        source, fallbacks = module.sanitize_for_cp1251(source)
        cf = module.CompoundFile(path.read_bytes())
        dialog = cf.read_stream(cf.find_stream("Dialog Stream"))
        controls = dialog_controls(dialog)
        differences = [i + 1 for i, (a, b) in enumerate(zip(source.splitlines(), extracted.splitlines())) if a != b]
        results[stem] = {
            "dialog_controls": controls,
            "diagnostic_button_formula": any(c["class"] == "BUTTON" and c["formula"] == "ДиагностикаЭ01()" for c in controls),
            "undeclared_form_identifiers": undeclared_form_identifiers(extracted, controls),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "version": extracted.splitlines()[0],
            "module_matches_text": source == extracted,
            "cp1251_fallbacks": [[f"U+{ord(src[0]):04X}", dst, count] for src, dst, count in fallbacks],
            "same_line_count": len(source.splitlines()) == len(extracted.splitlines()),
            "different_lines": differences,
            "code_equal_ignoring_comments_and_string_literals": code_only(source) == code_only(extracted),
            "dialog_contains_diagnostic_action": "ДиагностикаЭ01".encode("cp1251") in dialog,
            "dialog_contains_legacy_action": "Сформировать".encode("cp1251") in dialog,
        }
    return results


def clock_test_diagnosis():
    sys.path.insert(0, str(ROOT / "src"))
    from kudir.diagnostics import AnalysisLog
    from kudir.loaders import ExchangeBundle
    from kudir.matcher import Engine
    from kudir.scoring import load_scoring
    cfg = load_scoring(ROOT / "config" / "scoring.yaml")
    timeout = float(cfg.s("timeout_seconds_per_counterparty"))
    cases = {}
    for label, ticks in (("clock_unchanged", (1000.0, 1000.0)), ("clock_advanced", (1000.0, 1001.0))):
        bundle = ExchangeBundle(directory=ROOT, manifest={}, payments=[], ledger=[], documents=[], openings=[])
        engine = Engine(bundle=bundle, cfg=cfg, log=AnalysisLog())
        with patch("kudir.matcher.time.monotonic", side_effect=ticks) as clock:
            first = engine._timeout_deadline("C1")
            second = engine._timeout_deadline("C1")
            other = engine._timeout_deadline("C2")
            again = engine._timeout_deadline("C2")
        passed = first == second == ticks[0] + timeout and other == again == ticks[1] + timeout and clock.call_count == 2 and set(engine._cp_deadline) == {"C1", "C2"}
        cases[label] = {"passed": passed, "clock_calls": clock.call_count, "first_deadline": first, "other_deadline": other}
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--output-prefix", default="e01")
    parser.add_argument("--overwrite", action="store_true", help="replace existing protocols with the same prefix")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.output_prefix):
        parser.error("output-prefix must be a file name without path separators")
    planned = [HERE / f"{args.output_prefix}-checks.json"] + ([HERE / f"{args.output_prefix}-tests.txt"] if args.run_tests else [])
    existing = [p.name for p in planned if p.exists()]
    if existing and not args.overwrite:
        parser.error("protocol already exists, choose a new --output-prefix or pass --overwrite: " + ", ".join(existing))
    target = ROOT / "1cv77" / "kudir_export_agent.txt"
    source = read(target)
    original = read(ROOT / "1cv77" / "kudir_export.txt")
    definitions, duplicates, forward = routines(source)
    baseline, _, baseline_forward = routines(original)
    helper = syntax_helper_names()
    calls = set(re.findall(r"\b(КУДиР_\w+)\s*\(", code_only(source), re.I))
    reached = set()
    pending = ["ДиагностикаЭ01"]
    while pending:
        name = pending.pop()
        if name in reached or name not in definitions:
            continue
        reached.add(name)
        pending.extend(re.findall(r"\b(КУДиР_\w+)\s*\(", code_only(definitions[name]["text"])))
    results = {
        "scope": "source/ERT/legacy Python tests; NO execution or compilation in 1C 7.7",
        "python": sys.version,
        "source_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "source_version": source.splitlines()[0],
        "routines": len(definitions),
        "added_routines": sorted(set(definitions) - set(baseline)),
        "changed_existing_routines_ignoring_comments": [name for name in baseline if canonical(baseline[name]["text"]) != canonical(definitions[name]["text"])],
        "duplicate_routines": duplicates,
        "unresolved_local_helpers": sorted(calls - definitions.keys()),
        "forward_declarations_without_body": sorted(forward - definitions.keys()),
        "calls_before_definition_without_forward": calls_before_definition(source, definitions, forward),
        "forward_signature_mismatch": forward_mismatches(source),
        "lexical": lexical_checks(source),
        "builtin_name_clashes": builtin_name_clashes(source),
        "syntax_helper": str(SYNTAX_HELPER) if helper is not None else "NOT_CHECKED: 1C 7.7 syntax helper not found",
        "unknown_global_calls": unknown_global_calls(source, helper) if helper is not None else None,
        "legacy_export": {
            "source_version": original.splitlines()[0],
            "builtin_name_clashes": builtin_name_clashes(original),
            "unknown_global_calls": unknown_global_calls(original, helper) if helper is not None else None,
            "calls_before_definition_without_forward": calls_before_definition(original, baseline, baseline_forward),
            "forward_signature_mismatch": forward_mismatches(original),
        },
        "diagnostic_reachable_routines": sorted(reached),
        "diagnostic_reaches_export_or_python": bool(reached & {"КУДиР_Выполнить", "КУДиР_ЗаписатьВыгрузку", "КУДиР_ЗапуститьPython"}),
        "diagnostic_reaches_legacy_log": bool(reached & {"КУДиР_НачатьЛог", "КУДиР_ДописатьЛог", "КУДиР_ПисатьЛог"}),
        "ert": ert_checks(),
        "isolated_timeout_test": clock_test_diagnosis(),
        "existing_source_tests_target": "1cv77/kudir_export.txt, not kudir_export_agent.txt",
    }
    source.encode("cp1251")  # Strict encoding check, not a write/conversion.
    results["cp1251_representable"] = True
    tests_output = HERE / f"{args.output_prefix}-tests.txt"
    # Without --run-tests no earlier protocol is reported as a current result.
    results["tests_run"] = bool(args.run_tests)
    if args.run_tests:
        completed = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
        tests_output.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        results["test_process_exit_code"] = completed.returncode
        text = read(tests_output)
        results["saved_test_run"] = {
            "file": str(tests_output.relative_to(ROOT)),
            "counts": dict(Counter(re.findall(r"\.\.\. (ok|FAIL|ERROR|skipped)\b", text))),
            "summary": re.findall(r"^(?:Ran \d+ tests.*|FAILED .*|OK.*)$", text, re.M),
        }
    failures = []
    for field in ("duplicate_routines", "unresolved_local_helpers", "forward_declarations_without_body", "calls_before_definition_without_forward", "forward_signature_mismatch", "changed_existing_routines_ignoring_comments", "diagnostic_reaches_export_or_python", "diagnostic_reaches_legacy_log"):
        if results[field]:
            failures.append(field)
    if any(results["lexical"].values()):
        failures.append("lexical")
    if results["builtin_name_clashes"]:
        failures.append("builtin_name_clashes")
    if results["unknown_global_calls"]:
        failures.append("unknown_global_calls")
    if any(results["legacy_export"][field] for field in ("builtin_name_clashes", "unknown_global_calls", "calls_before_definition_without_forward", "forward_signature_mismatch")):
        failures.append("legacy export static checks")
    legacy_ert = results["ert"].get("kudir_export", {})
    if not legacy_ert.get("module_matches_text") or legacy_ert.get("undeclared_form_identifiers", ["ERT not read"]):
        failures.append("legacy export ERT parity/form identifiers")
    if not all(case["passed"] for case in results["isolated_timeout_test"].values()):
        failures.append("timeout test")
    ert = results["ert"].get("kudir_export_agent", {})
    if not ert.get("module_matches_text") or not ert.get("diagnostic_button_formula"):
        failures.append("ERT parity/action not verified")
    if ert.get("undeclared_form_identifiers", ["ERT not read"]):
        failures.append("undeclared form identifiers")
    if results.get("test_process_exit_code", 0) != 0:
        failures.append("unit tests")
    results["failed_checks"] = failures
    output = HERE / f"{args.output_prefix}-checks.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
