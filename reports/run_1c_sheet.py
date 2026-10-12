"""Fill the KP0.5 sheet from the standard reports of the configuration, built in a real 1C 7.7 session (harness77 core).

    python reports/run_1c_sheet.py collect --output <fresh ASCII dir> --base <base> --plan exchange/KUDIR4_20261012_005
    python reports/run_1c_sheet.py collect --output <fresh ASCII dir> --base <base> osv_20260101_20260331 ...
    python reports/run_1c_sheet.py fill exchange/KUDIR4_20261012_005 <collect dir> [--out exchange/sverka-..-1c.md]

collect --plan derives the commands from the run: osv and osvk for every quarter of ledger_quarters.csv (to its
covered_through) and a card for every document example of the sheet (from the start of its quarter to its date).
fill compares the export with those reports and writes the filled sheet: section 1 - the controls of every quarter
against the ОСВ per subaccount; section 2 - every DAY_START row (all customers and contracts, not only the examples)
against the ОСВ by Контрагенты and Договоры; section 3 - the customer balance before and after every example document
against the running balance of the card. Exit code 0 - everything matched.

Commands (dates YYYYMMDD):
  osv_<d1>_<d2>          - «Оборотно-сальдовая ведомость по счету» 62 with subaccounts, no subconto (sheet section 1);
  osvk_<d1>_<d2>         - the same report for 62 without subaccounts, Контрагенты and Договоры expanded (section 2);
  card_<code>_<d1>_<d2>  - «Карточка счета» 62 with the customer of that code selected (section 3).

The reports are not re-implemented: the controller calls the configuration's own drill-down entry
глОбработкаРасшифровки(Расшифровка, 0, Таблица) with Обновить=1, so the report module fills the given Таблица exactly as
it does for a user, and the table is saved with Таблица.Записать(<file>, "TXT") next to the command's protocol. A period
beyond КонецРассчитанногоПериодаБИ() is refused before the report: its modal warning would hang a session without a user.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import defaultdict
from datetime import date as Date, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_e05 as e05  # noqa: E402
import run_1c_e05 as runner  # noqa: E402
from check_e02 import Findings, read_table  # noqa: E402
from check_e03 import rub  # noqa: E402

COMMAND = re.compile(r"(osv|osvk)_(\d{8})_(\d{8})|card_([0-9A-Za-z]+)_(\d{8})_(\d{8})")

CONTROLLER = """Функция ДатаКоманды77(Т)
    Возврат Дата(Число(Лев(Т, 4)), Число(Сред(Т, 5, 2)), Число(Прав(Т, 2)));
КонецФункции

Функция ДатаISO77(Д)
    Возврат "" + ДатаГод(Д) + "-" + Прав("0" + ДатаМесяц(Д), 2) + "-" + Прав("0" + ДатаЧисло(Д), 2);
КонецФункции

Функция Код77(З)
    Ответ = "";
    Попытка
        Если ТипЗначенияСтр(З) = "Справочник" Тогда
            Ответ = СокрЛП(З.Код);
        КонецЕсли;
    Исключение
        Ответ = "?";
    КонецПопытки;
    Возврат Ответ;
КонецФункции

Функция Владелец77(З)
    Ответ = "";
    Попытка
        Если ТипЗначенияСтр(З) = "Справочник" Тогда
            Ответ = СокрЛП(З.Владелец.Код);
        КонецЕсли;
    Исключение
        Ответ = "?";
    КонецПопытки;
    Возврат Ответ;
КонецФункции

Процедура КлючиСтрок77(Т, ВидОтч, ФайлКл)
    // Расшифровка каждой строки отчёта: субконто строки ОСВ, документ и номер проводки строки карточки. Строки
    // итогов могут нести расшифровку предыдущей строки - их отличает текст строки на стороне Python.
    Ключи = СоздатьОбъект("Текст");
    Для Н = 1 По Т.ВысотаТаблицы() Цикл
        Р = Т.Область("R" + Н + "C1").Расшифровка();
        Если ТипЗначенияСтр(Р) = "СписокЗначений" Тогда
            Стр = "" + Н;
            Если ВидОтч = "osvk" Тогда
                С2 = Р.Получить("Субконто2");
                Стр = Стр + Симв(9) + Код77(Р.Получить("Субконто1")) + Симв(9) + Код77(С2) + Симв(9) + Владелец77(С2);
            Иначе
                Д = Р.Получить("Документ");
                Если ТипЗначенияСтр(Д) = "Документ" Тогда
                    Стр = Стр + Симв(9) + Д.Вид() + Симв(9) + СокрЛП(Д.НомерДок) + Симв(9) + ДатаISO77(Д.ДатаДок) + Симв(9) + Р.Получить("НомерПроводки");
                КонецЕсли;
            КонецЕсли;
            Ключи.ДобавитьСтроку(Стр);
        КонецЕсли;
    КонецЦикла;
    Ключи.Записать(ФайлКл);
КонецПроцедуры

Функция Kudir77(Путь, Команда, Протокол, Метка, Нач, Кон) Экспорт
    Сообщения77 = СоздатьОбъект("MessageView");
    Сообщения77.SetSystemMessages();
    Сообщения77.Clear();
    Сообщить("HARNESS77-BEGIN:" + Метка);
    Ответ77 = "";
    Файл77 = Лев(Протокол, СтрДлина(Протокол) - 4) + "-table.txt";
    Части77 = СоздатьОбъект("СписокЗначений");
    Ост77 = Команда + "_";
    Пока СтрДлина(Ост77) > 0 Цикл
        П77 = Найти(Ост77, "_");
        Части77.ДобавитьЗначение(Лев(Ост77, П77 - 1));
        Ост77 = Сред(Ост77, П77 + 1);
    КонецЦикла;
    Вид77 = Части77.ПолучитьЗначение(1);
    Нач77 = ДатаКоманды77(Части77.ПолучитьЗначение(Части77.РазмерСписка() - 1));
    Кон77 = ДатаКоманды77(Части77.ПолучитьЗначение(Части77.РазмерСписка()));
    Р77 = СоздатьОбъект("СписокЗначений");
    Р77.Установить("Обновить", 1);
    Р77.Установить("Дата1", Нач77);
    Р77.Установить("Дата2", Кон77);
    Р77.Установить("Счет", СчетПоКоду("62"));
    Готово77 = 1;
    Если (Нач77 > Кон77) Или (Кон77 > КонецРассчитанногоПериодаБИ()) Тогда
        Ответ77 = "refused:period " + Нач77 + ".." + Кон77 + ", итоги рассчитаны по " + КонецРассчитанногоПериодаБИ();
        Готово77 = 0;
    ИначеЕсли Вид77 = "osv" Тогда
        Р77.Установить("Отчет", "ОборотноСальдоваяВедомостьПоСчету");
        Р77.Установить("ДанныеПоСубсчетам", 1);
        Р77.Установить("ОтборСубконто1", 3);
        Р77.Установить("ОтборСубконто2", 3);
        Р77.Установить("ОтборСубконто3", 3);
        Р77.Установить("ПоГруппам1", 0);
        Р77.Установить("ПоГруппам2", 0);
        Р77.Установить("ПоГруппам3", 0);
    ИначеЕсли Вид77 = "osvk" Тогда
        Р77.Установить("Отчет", "ОборотноСальдоваяВедомостьПоСчету");
        Р77.Установить("ДанныеПоСубсчетам", 0);
        Р77.Установить("ВидСубконто1", ВидыСубконто.Контрагенты);
        Р77.Установить("ОтборСубконто1", 1);
        Р77.Установить("ВидСубконто2", ВидыСубконто.Договоры);
        Р77.Установить("ОтборСубконто2", 1);
        Р77.Установить("ОтборСубконто3", 3);
        Р77.Установить("ПоГруппам1", 0);
        Р77.Установить("ПоГруппам2", 0);
        Р77.Установить("ПоГруппам3", 0);
    ИначеЕсли Вид77 = "card" Тогда
        Спр77 = СоздатьОбъект("Справочник.Контрагенты");
        Если Спр77.НайтиПоКоду(Части77.ПолучитьЗначение(2), 0) = 1 Тогда
            Р77.Установить("Отчет", "КарточкаСчета");
            Р77.Установить("ВидСубконто1", ВидыСубконто.Контрагенты);
            Р77.Установить("Субконто1", Спр77.ТекущийЭлемент());
            Р77.Установить("ОтборСубконто1", 2);
            Р77.Установить("ВидПериода", 1);
            Ответ77 = Ответ77 + "customer:" + Спр77.Код + " " + Спр77.Наименование + РазделительСтрок;
        Иначе
            Ответ77 = "refused:customer code not found";
            Готово77 = 0;
        КонецЕсли;
    Иначе
        Ответ77 = "refused:unknown command";
        Готово77 = 0;
    КонецЕсли;
    Если Готово77 = 1 Тогда
        Попытка
            Т77 = СоздатьОбъект("Таблица");
            глОбработкаРасшифровки(Р77, 0, Т77);
            Т77.Записать(Файл77, "TXT");
            Ответ77 = Ответ77 + "table:" + Т77.ВысотаТаблицы() + "x" + Т77.ШиринаТаблицы() + РазделительСтрок + "file:" + Файл77;
            Если Вид77 <> "osv" Тогда
                КлючиСтрок77(Т77, Вид77, Лев(Протокол, СтрДлина(Протокол) - 4) + "-keys.txt");
            КонецЕсли;
        Исключение
            Ответ77 = Ответ77 + "error:" + ОписаниеОшибки();
        КонецПопытки;
    КонецЕсли;
    Сообщить("HARNESS77-END:" + Метка);
    Сообщения77.SaveToFile(Протокол);
    Возврат Ответ77;
КонецФункции
"""


def judge(operations: list[dict]) -> bool:
    """A failure: a command without a saved table."""
    return any(not any(line.startswith("file:") for line in o["actual"]) for o in operations)


# --- plan ----------------------------------------------------------------------------------------------

def compact(date: str) -> str:
    return date.replace("-", "")


def load_run(run: Path) -> dict:
    local = Findings()
    ledger = read_table(run, e05.LEDGER_FILE, e05.LEDGER_HEADER, local) or []
    snapshots = read_table(run, e05.SNAPSHOTS_FILE, e05.SNAPSHOTS_HEADER, local) or []
    quarters = read_table(run, e05.QUARTERS_FILE, e05.QUARTERS_HEADER, local) or []
    log = run / "1c_export.log"
    day: dict[str, dict[tuple, dict]] = defaultdict(dict)
    before: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for row in snapshots:
        (day[row["BoundaryDate"]] if row["BoundaryKind"] == "DAY_START" else before[row["BeforeDocumentID"]])[e05.slice_key(row)] = row
    names: dict[str, str] = {}
    for row in ledger:
        names.setdefault(row["КонтрагентID"], row["Контрагент"])
    log_text = log.read_bytes().decode("cp1251", "replace") if log.is_file() else ""
    return {"ledger": ledger, "quarters": quarters, "day": day, "before": before, "by_doc": e05.documents_in_order(ledger),
            "names": names, "controls": e05.CONTROL_LOG.findall(log_text)}


def plan(run: Path) -> list[str]:
    data = load_run(run)
    commands = []
    for q in data["quarters"]:
        span = f"{compact(q['quarter_start'])}_{compact(q['covered_through'])}"
        commands += [f"osv_{span}", f"osvk_{span}"]
    for _, doc, customer in e05.sample_documents(data["by_doc"], data["before"]):
        date = data["by_doc"][doc][0]["ДатаОперации"]
        commands.append(f"card_{customer[2:]}_{compact(e05.quarter_start(date))}_{compact(date)}")
    return list(dict.fromkeys(commands))


# --- fill ----------------------------------------------------------------------------------------------

def records(path: Path) -> list[list[str]]:
    """Таблица.Записать(.., "TXT"): tab-separated records, CRLF between records, multi-line cells in quotes."""
    return list(csv.reader(io.StringIO(path.read_bytes().decode("cp1251"), newline=""), delimiter="\t"))


def kopecks(cell: str) -> int:
    text = cell.strip().replace(",", "").replace("\xa0", "")
    return int(Decimal(text) * 100) if text else 0


def report_rows(directory: Path, command: str) -> tuple[list[list[str]], list[list[str]]]:
    stem = directory / runner.PROTOCOL_DIRECTORY / command
    table = records(Path(f"{stem}-table.txt"))
    keys_path = Path(f"{stem}-keys.txt")
    keys = [line.split("\t") for line in keys_path.read_bytes().decode("cp1251").splitlines()] if keys_path.is_file() else []
    return table, keys


def next_day(date: str) -> str:
    return (Date.fromisoformat(date) + timedelta(days=1)).isoformat()


def osv_accounts(table: list[list[str]]) -> dict[str, tuple[int, int, int, int]]:
    """ОСВ по счету with subaccounts: account -> (СНД, СНК, СКД, СКК) in kopecks."""
    return {r[0].strip(): (kopecks(r[1]), kopecks(r[2]), kopecks(r[5]), kopecks(r[6]))
            for r in table if len(r) >= 7 and re.fullmatch(r"62\.\d+", r[0].strip())}


def osv_balances(table: list[list[str]], keys: list[list[str]]) -> tuple[dict, dict]:
    """ОСВ by Контрагенты/Договоры: snapshot key -> signed opening and closing balance. The level of a row is its text
    (a contract is indented); the codes come from the drill-down of the row."""
    opening, closing = {}, {}
    for key in keys:
        number, customer, contract, owner = (key + ["", "", ""])[:4]
        row = table[int(number) - 1]
        # Totals rows keep the drill-down of the last customer: not a customer row.
        if not customer or len(row) < 7 or row[0].strip().startswith("Итого"):
            continue
        if row[0].startswith(" "):
            slice_key = ("CUSTOMER_CONTRACT", f"C|{customer}", "VALUE" if contract else "EMPTY", f"D|C|{owner}|{contract}" if contract else "")
        else:
            slice_key = ("CUSTOMER", f"C|{customer}", "ALL", "")
        if slice_key in opening:
            raise ValueError(f"row {number}: {slice_key} repeats in the ОСВ - the drill-down does not identify the row")
        opening[slice_key] = kopecks(row[1]) - kopecks(row[2])
        closing[slice_key] = kopecks(row[5]) - kopecks(row[6])
    return opening, closing


def card_balances(table: list[list[str]], keys: list[list[str]], kind: str, number: str, date: str) -> tuple[int | None, int | None, int]:
    """«Карточка счета»: running balance before the first and after the last posting of the document, its postings."""
    by_row = {int(k[0]): k for k in keys if len(k) >= 5}
    running = before = after = None
    found = 0
    for index, row in enumerate(table, 1):
        if row and row[0].startswith("Сальдо на") and running is None:
            running = kopecks(row[4]) - kopecks(row[6])
            continue
        if len(row) < 9 or not re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", row[0].strip()):
            continue
        side, amount = row[7].strip(), kopecks(row[8])
        balance = amount if side == "Д" else -amount if side == "К" else 0
        key = by_row.get(index)
        if key and key[1].upper() == kind.upper() and key[2] == number and key[3] == date:
            if found == 0:
                before = running
            found += 1
            after = balance
        running = balance
    return before, after, found


def mark(same: bool) -> str:
    return "✓" if same else "✗ РАСХОЖДЕНИЕ"


def fill(run: Path, collected: Path) -> tuple[str, bool]:
    data = load_run(run)
    day, before, by_doc, names = data["day"], data["before"], data["by_doc"], data["names"]
    ok = True
    lines = [f"# Ведомость сверки КП0.5 — {run.name}, заполнена по отчётам 1С", "",
             f"Отчёты конфигурации построены в 1С без участия человека (`run_1c_sheet.py`, каталог `{collected.name}`): «Оборотно-сальдовая "
             "ведомость по счету» 62 и «Карточка счета» 62 вызваны штатной расшифровкой `глОбработкаРасшифровки`, их таблицы сохранены в TXT "
             "и сравнены с выгрузкой до копейки. Суммы в рублях; сальдо = Дт − Кт.", ""]

    # 1. Quarterly controls against the ОСВ per subaccount.
    lines += ["## 1. Общие квартальные контроли — ОСВ по счёту 62 по субсчетам", "",
              "| Квартал | Счёт | Начало Дт / Кт: выгрузка | ОСВ 1С | Конец Дт / Кт: выгрузка | ОСВ 1С | Итог |",
              "|---|---|---:|---:|---:|---:|---|"]
    log = {(start, account): (int(snd), int(snk), skd, skk) for start, account, snd, snk, skd, skk, _ in data["controls"]}
    for q in data["quarters"]:
        accounts = osv_accounts(report_rows(collected, f"osv_{compact(q['quarter_start'])}_{compact(q['covered_through'])}")[0])
        full = q["full_quarter"] == "1"
        for account in sorted(accounts):
            snd, snk, skd, skk = accounts[account]
            exp = log.get((q["quarter_start"], account))
            same_open = exp is not None and (exp[0], exp[1]) == (snd, snk)
            same_close = not full or (exp is not None and bool(exp[2]) and (int(exp[2]), int(exp[3])) == (skd, skk))
            ok &= same_open and same_close
            exp_open = f"{rub(exp[0])} / {rub(exp[1])}" if exp else "—"
            exp_close = f"{rub(int(exp[2]))} / {rub(int(exp[3]))}" if exp and exp[2] else "пусто: квартал не полон"
            lines.append(f"| {q['quarter_start']}..{q['covered_through']} | {account} | {exp_open} | {rub(snd)} / {rub(snk)} | {exp_close} | "
                         f"{rub(skd)} / {rub(skk)} | {mark(same_open and same_close)} |")
        total = (sum(v[0] for v in accounts.values()), sum(v[1] for v in accounts.values()))
        same = total == (int(q["opening_debit_kopecks"]), int(q["opening_credit_kopecks"]))
        ok &= same
        lines.append(f"| {q['quarter_start']} | 62.1+62.2 (ledger_quarters) | {rub(int(q['opening_debit_kopecks']))} / {rub(int(q['opening_credit_kopecks']))} | "
                     f"{rub(total[0])} / {rub(total[1])} | | | {mark(same)} |")
    lines += ["", "У текущего квартала конец в выгрузке пуст (дата выгрузки — не конец квартала); ОСВ 1С показывает остаток на дату охвата для сведения.", ""]

    # 2. Every DAY_START row against the ОСВ by Контрагенты and Договоры.
    lines += ["## 2. Остатки на начало кварталов — ОСВ по счёту 62 по контрагентам и договорам", "",
              "Сверены все строки DAY_START, не только примеры. ОСВ 1С не показывает субконто без остатка и оборотов: такой ключ — ноль. "
              "Строка «по концу квартала» — та же граница по сальдо на конец предыдущего квартала.", "",
              "| Дата | Строк выгрузки | Ненулевых: выгрузка | Ненулевых: ОСВ 1С | Совпало | Расхождений | Ненулевых в 1С без строки выгрузки |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    opening_1c: dict[str, dict] = {}
    closing_1c: dict[str, dict] = {}
    for q in data["quarters"]:
        opening, closing = osv_balances(*report_rows(collected, f"osvk_{compact(q['quarter_start'])}_{compact(q['covered_through'])}"))
        opening_1c[q["quarter_start"]] = opening
        if q["full_quarter"] == "1":
            closing_1c[next_day(q["covered_through"])] = closing
    examples = []
    for date in sorted(day):
        rows = day[date]
        for label, values in (("", opening_1c.get(date)), (" по концу квартала", closing_1c.get(date))):
            if values is None:
                continue
            differ = [(k, e05.signed(r), values.get(k, 0)) for k, r in rows.items() if e05.signed(r) != values.get(k, 0)]
            extra = [(k, None, v) for k, v in values.items() if v and k not in rows]
            ok &= not differ and not extra
            examples += [(date + label, *d) for d in differ + extra]
            lines.append(f"| {date}{label} | {len(rows)} | {sum(1 for r in rows.values() if e05.signed(r))} | {sum(1 for v in values.values() if v)} | "
                         f"{len(rows) - len(differ)} | {len(differ)} | {len(extra)} |")
    lines += [f"- {date} {key}: выгрузка {rub(exported) if exported is not None else 'нет строки'}, ОСВ 1С {rub(value)}" for date, key, exported, value in examples[:20]]
    lines += ["", "### Примеры покупателей ведомости (выгрузка / ОСВ 1С)", ""]
    dates = sorted(day)
    for reason, customer in e05.sample_customers(day):
        lines += [f"#### {customer[2:]} {names.get(customer, '')} — {reason}", "", "| Граница | " + " | ".join(dates) + " |", "|---|" + "---:|" * len(dates)]
        for key in sorted({k for d in dates for k in day[d] if k[1] == customer}, key=lambda k: (k[0] != "CUSTOMER", k[3])):
            title = "Покупатель" if key[0] == "CUSTOMER" else ("пустой договор" if key[2] == "EMPTY" else f"договор {key[3].split('|')[-1]}")
            cells = []
            for d in dates:
                exported = e05.signed(day[d][key]) if key in day[d] else None
                value = opening_1c.get(d, {}).get(key, 0)
                cells.append(f"{rub(exported) if exported is not None else '—'} / {rub(value)} {'✓' if exported == value else '✗'}")
            lines.append(f"| {title} | " + " | ".join(cells) + " |")
        lines.append("")

    # 3. Balance before/after the example documents against the running balance of the card.
    lines += ["## 3. Остатки перед денежными документами — карточка счёта 62 по покупателю", "",
              "До документа — сальдо карточки в строке перед первой проводкой документа, после — в строке его последней проводки.", "",
              "| Документ | Покупатель | До документа: выгрузка / карточка 1С | После документа: выгрузка / карточка 1С | Проводок в карточке | Итог |",
              "|---|---|---:|---:|---:|---|"]
    for reason, doc, customer in e05.sample_documents(by_doc, before):
        head = by_doc[doc][0]
        date = head["ДатаОперации"]
        table, keys = report_rows(collected, f"card_{customer[2:]}_{compact(e05.quarter_start(date))}_{compact(date)}")
        card_before, card_after, found = card_balances(table, keys, head["ВидДокумента"], head["НомерДокумента"], head["ДатаДокумента"])
        snap = before[doc].get(("CUSTOMER", customer, "ALL", ""))
        exported = e05.signed(snap) if snap else None
        after = exported + sum(e05.delta(r) or 0 for r in by_doc[doc] if r["КонтрагентID"] == customer) if exported is not None else None
        same = found > 0 and exported == card_before and after == card_after
        ok &= same
        money = lambda v: rub(v) if v is not None else "—"
        lines.append(f"| {head['ВидДокумента']} № {head['НомерДокумента']} от {head['ДатаДокумента']} ({reason}) | {customer[2:]} {names.get(customer, '')} | "
                     f"{money(exported)} / {money(card_before)} | {money(after)} / {money(card_after)} | {found} | {mark(same)} |")
    lines += ["", f"**Итог: {'всё совпало' if ok else 'есть расхождения'}.**", ""]
    return "\n".join(lines) + "\n", ok


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    collect = sub.add_parser("collect")
    runner.session_arguments(collect)
    collect.add_argument("--plan", type=Path, help="run directory: derive the commands of its sheet")
    collect.add_argument("commands", nargs="*")
    out = sub.add_parser("fill")
    out.add_argument("run", type=Path)
    out.add_argument("collected", type=Path)
    out.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "fill":
        text, ok = fill(args.run, args.collected)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print(f"записано {args.out}")
        else:
            print(text)
        return 0 if ok else 1
    commands = list(args.commands) + (plan(args.plan) if args.plan else [])
    if not commands:
        parser.error("no commands: give them or --plan <run directory>")
    for command in commands:
        if not COMMAND.fullmatch(command):
            parser.error(f"command {command!r}: osv_<d1>_<d2>, osvk_<d1>_<d2> or card_<code>_<d1>_<d2>")
    print(json.dumps({"commands": commands}, ensure_ascii=False))
    code, _ = runner.guarded_session(args, commands, CONTROLLER, None, f"KP0.5 sheet reports on {args.base or 'base-for-test'}", judge)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
