"""Narrow text checks of 1C 7.7 modules, written for E0.1 and used by the static checks of check_e02+.

This is NOT a 1C compiler: declaration order (calls before definition need "Далее"), forward signatures, form
identifiers, built-in names used as variables, global calls missing from the installed 1C 7.7 syntax helper, and the
TXT/ERT comparison through the ERT reader named in sync_modules.bat. The E0.1 run of this script against the
diagnostic agent kudir_export_agent was removed with the agent after KP0.5 (13.10.2026); it is in the git history.
"""

from pathlib import Path
import hashlib
import importlib.util
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
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


def ert_checks(stems=("kudir_export",)):
    sync = read(ROOT / "sync_modules.bat")
    match = re.search(r'SYNC_PY=([^"\r\n]+)', sync)
    reader = Path(match[1]).with_name("ert_module.py") if match else None
    if not reader or not reader.is_file():
        return {"status": "NOT_CHECKED", "reason": "local read-only ERT reader unavailable"}
    module = load_module("_e01_ert_reader", reader)
    results = {"reader": str(reader), "reader_sha256": hashlib.sha256(reader.read_bytes()).hexdigest()}
    for stem in stems:
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
