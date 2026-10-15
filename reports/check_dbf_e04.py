"""Independent check of an E0.4 run against the 1C 7.7 DBF tables of the same base (read-only).

    python reports/check_dbf_e04.py <base directory> exchange/KUDIR4_20261012_001 --metadata <metadata.txt> [--out report.md] [--json out.json]

The export reads documents and postings through the 1C object model. This script reads the same facts straight from
the DBF files of the base and compares them with documents.csv and ledger62.csv field by field:

- documents of the kinds Счет, РасходнаяНакладная, ОказаниеУслуг dated within the run window (1SJOURN): presence,
  number, date, position, posted/marked flags (CLOSED bit 1, ISMARK), customer and contract (header attributes resolved
  in SC133/SC112, contract ID by the contract owner), settlement kind (header enum resolved by the metadata dump),
  document amount (stored column total «Всего» of the header), VAT aggregates and status recomputed from the table rows
  by №1 §15.1, comment (1SBLOB, the document comment field);
- every 62.1/62.2 side of the postings dated within the window (1SENTRY): presence, amount, accounts, side, date,
  customer/contract/settlement kind subconto, posting text, primary document, comment of the source document.

Table and field names come from 1Cv7.DD of the base (by their descriptions), enum values from the metadata dump of the
configuration (UTF-8 text with "EnumVal" lists). The base must not be open in 1C in exclusive mode: the files are read,
never written. Run it on a copy of the DBF files when 1C holds them.

Exit codes: 0 - everything matched; 1 - differences found; 2 - the base or the run could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_e04 as e04  # noqa: E402
import check_e02 as e02  # noqa: E402  run_start: the window of a run of any stage
from check_e02 import Findings, read_table  # noqa: E402
from check_e03 import LEDGER_FILE, LEDGER_HEADER, rub  # noqa: E402

KINDS = ("Счет", "РасходнаяНакладная", "ОказаниеУслуг")
# 1SBLOB.FIELDID of the document comment (base-36 "D7"): the texts of this field equal the comments 1C returned for
# all 2730 documents of the copy checked on 12.10.2026.
COMMENT_FIELD = 475
K_PATTERN = re.compile(r'\{"O","0","0","(\d+)","0","0","\s*(\d+)(.{3})"\}')
EXAMPLES = 10


# --- DBF -----------------------------------------------------------------------------------------------

def read_dbf(path: Path, fields: set[str] | None = None) -> list[dict]:
    """Rows of a dBase table as text (cp1251, unstripped); deleted rows are skipped."""
    data = path.read_bytes()
    count, header_len, record_len = struct.unpack("<IHH", data[4:12])
    layout, pos, offset = [], 32, 1
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("ascii")
        length = data[pos + 16]
        if fields is None or name in fields:
            layout.append((name, offset, length))
        offset += length
        pos += 32
    rows = []
    for i in range(count):
        record = data[header_len + i * record_len: header_len + (i + 1) * record_len]
        if record[:1] == b"*":
            continue
        rows.append({name: record[o:o + n].decode("cp1251") for name, o, n in layout})
    return rows


def b36(text: str) -> int:
    text = text.strip()
    return int(text, 36) if text else 0


def kopecks(text: str) -> int:
    text = text.strip()
    return int(Decimal(text) * 100) if text else 0


def account_code(text: str) -> str:
    """1SACCS.SCHKOD is stored by the mask of the chart: " 62. 1.    " -> "62.1"."""
    return ".".join(part.strip() for part in text.split(".") if part.strip())


def iso(date: str) -> str:
    date = date.strip()
    return f"{date[:4]}-{date[4:6]}-{date[6:]}" if date else ""


def parse_dd(path: Path) -> dict[str, dict]:
    """Tables of 1Cv7.DD: {table: {"descr": text, "fields": {field descr: field name}}}."""
    tables: dict[str, dict] = {}
    current = None
    for line in path.read_bytes().decode("cp1251").splitlines():
        parts = [p.strip() for p in line[2:].split("|")]
        if line.startswith("T="):
            current = tables.setdefault(parts[0], {"descr": parts[1], "fields": {}})
        elif line.startswith("F=") and current is not None:
            current["fields"][parts[1]] = parts[0]
    return tables


def enum_values(metadata: Path, enum: str) -> dict[int, tuple[str, str]]:
    """{value id: (identifier, presentation)} of an enum in the metadata dump."""
    lines = metadata.read_text(encoding="utf-8", errors="replace").splitlines()
    values: dict[int, tuple[str, str]] = {}
    for i, line in enumerate(lines):
        if re.match(r'\{"\d+","' + re.escape(enum) + '",', line) and i + 1 < len(lines) and lines[i + 1].startswith('{"EnumVal"'):
            for item in lines[i + 2:]:
                m = re.match(r'\{"(\d+)","([^"]*)","[^"]*","([^"]*)"', item)
                if not m:
                    break
                values[int(m.group(1))] = (m.group(2), m.group(3))
                if item.endswith("}}},"):
                    break
            break
    return values


def blob_texts(rows: list[dict], field: int) -> dict[str, str]:
    """Long strings of one field: {OBJID: text}. Block 0 starts with the length in hex (9 characters)."""
    blocks: dict[str, dict[int, str]] = defaultdict(dict)
    for r in rows:
        if b36(r["FIELDID"]) == field:
            blocks[r["OBJID"]][int(r["BLOCKNO"])] = r["BLOCK"]
    texts = {}
    for obj, parts in blocks.items():
        joined = "".join(parts[n] for n in sorted(parts))
        texts[obj] = joined[9:9 + int(joined[:9].strip() or "0", 16)]
    return texts


# --- base model ----------------------------------------------------------------------------------------

class Base:
    def __init__(self, directory: Path, metadata: Path) -> None:
        self.dir = directory
        dd = parse_dd(directory / "1Cv7.DD")
        self.tables = {}
        for kind in KINDS:
            head = next(t for t, v in dd.items() if t.startswith("DH") and v["descr"] == f"Документ {kind}")
            self.tables[kind] = (head, "DT" + head[2:], dd[head]["fields"], dd["DT" + head[2:]]["fields"])
        enum = enum_values(metadata, "ВидыРасчетовСПокупателями")
        if not enum:
            raise ValueError("в выгрузке метаданных нет значений перечисления ВидыРасчетовСПокупателями")
        self.settlement = enum
        self.customers = {r["ID"]: r for r in read_dbf(directory / "SC133.DBF", {"ID", "CODE", "DESCR"})}
        self.contracts = {r["ID"]: r for r in read_dbf(directory / "SC112.DBF", {"ID", "CODE", "DESCR", "PARENTEXT", dd["SC112"]["fields"]["(P)ДатаВозникновения"]})}
        self.contract_date_field = dd["SC112"]["fields"]["(P)ДатаВозникновения"]
        variants = dd["SC11147"]["fields"]
        self.variant_fields = (variants["(P)СуммаВключаетНДС"], variants["(P)СтавкаНДС"])
        self.variants = {r["ID"]: r for r in read_dbf(directory / "SC11147.DBF")}
        self.vat_rates = {r["ID"]: r["DESCR"].strip() for r in read_dbf(directory / "SC211.DBF", {"ID", "DESCR"})}
        self.journal = read_dbf(directory / "1SJOURN.DBF", {"IDDOC", "IDDOCDEF", "DATE", "TIME", "DOCNO", "CLOSED", "ISMARK"})
        self.by_id = {r["IDDOC"]: r for r in self.journal}
        self.comments = blob_texts(read_dbf(directory / "1SBLOB.DBF"), COMMENT_FIELD)
        accounts = read_dbf(directory / "1SACCS.DBF", {"ID", "SCHKOD", "SC0", "SC1", "SC2"})
        self.accounts = {r["ID"]: account_code(r["SCHKOD"]) for r in accounts}
        self.account_subconto = {account_code(r["SCHKOD"]): (r["SC0"], r["SC1"], r["SC2"]) for r in accounts}
        entry = dd["1SENTRY"]["fields"]
        self.entry_fields = (entry["(P)СодержаниеПроводк"], entry["(P)ПервичныйДокумент"])

    def key(self, iddoc: str) -> str:
        j = self.by_id[iddoc]
        return f'{{"O","0","0","{b36(j["IDDOCDEF"])}","0","0","{b36(iddoc[:6]):>10}{iddoc[6:]}"}}'

    def position(self, iddoc: str) -> str:
        j = self.by_id[iddoc]
        return f"#{j['DATE']}{b36(j['TIME']):>10}{b36(iddoc[:6]):>10}{iddoc[6:]}"

    def customer_id(self, ref: str) -> tuple[str, str]:
        c = self.customers.get(ref)
        return (f"C|{c['CODE'].strip()}", c["DESCR"].strip()) if c else ("", "")

    def contract(self, ref: str) -> dict:
        c = self.contracts.get(ref)
        if not c:
            return {}
        owner = self.customers.get(c["PARENTEXT"])
        return {"ДоговорID": f"D|C|{owner['CODE'].strip()}|{c['CODE'].strip()}" if owner else "",
                "Договор": c["DESCR"].strip(), "НомерДоговора": c["DESCR"].strip(),
                "ДатаДоговора": iso(c[self.contract_date_field]), "owner": f"C|{owner['CODE'].strip()}" if owner else ""}

    def settlement_kind(self, ref: str) -> tuple[str, str]:
        value = self.settlement.get(b36(ref[:6]) if ref.strip() else 0)
        return (f"VR|{value[0]}", value[1]) if value else ("", "")


def expected_documents(base: Base, window: tuple[str, str]) -> dict[str, dict]:
    """documents.csv rows as the base gives them, keyed by document_id; plus row details for review."""
    start, end = (w.replace("-", "") for w in window)
    docs: dict[str, dict] = {}
    for kind in KINDS:
        head, table, hf, tf = base.tables[kind]
        heads = {r["IDDOC"]: r for r in read_dbf(base.dir / f"{head}.DBF")}
        lines: dict[str, list[dict]] = defaultdict(list)
        for r in read_dbf(base.dir / f"{table}.DBF"):
            lines[r["IDDOC"]].append(r)
        kind_id = int(head[2:])
        for j in base.journal:
            if b36(j["IDDOCDEF"]) != kind_id or not (start <= j["DATE"] <= end):
                continue
            iddoc = j["IDDOC"]
            h = heads.get(iddoc, {})
            customer, name = base.customer_id(h.get(hf["(P)Контрагент"], ""))
            contract = base.contract(h.get(hf["(P)Договор"], "")) if customer else {}
            vr_field = next((f for d, f in hf.items() if d.startswith("(P)ВыбВидыРасчетов")), None)
            vr, vr_name = base.settlement_kind(h.get(vr_field, "")) if vr_field else ("", "")
            amount = kopecks(h.get(hf["(P)Всего"], ""))
            rows = sorted(lines.get(iddoc, []), key=lambda r: int(r["LINENO"]))
            taxable = exempt = vat = on_top = 0
            status, reason = "KNOWN", ""
            for r in rows:
                s, n, t = (kopecks(r[tf[f"(P){c}"]]) for c in ("Сумма", "НДС", "Всего"))
                if n < 0:
                    status, reason = "UNKNOWN", "отрицательный НДС строки"
                elif t not in (s, s + n):
                    status, reason = "UNKNOWN", "строка несогласована"
                if t == s + n and n != 0 and t != s:
                    on_top += 1
                if n > 0:
                    taxable, vat = taxable + t, vat + n
                else:
                    exempt += t
            if status == "KNOWN" and taxable + exempt != amount:
                status, reason = "UNKNOWN", "агрегаты не равны сумме документа"
            variant = base.variants.get(h.get(hf.get("(P)ВариантРасчетаНал", ""), ""), {})
            key = f"DOC|{base.key(iddoc)}"
            docs[key] = {
                "ВидДокумента": kind, "НомерДокумента": j["DOCNO"].strip(), "ДатаДокумента": iso(j["DATE"]),
                "ПозицияДокумента": base.position(iddoc), "КонтрагентID": customer, "Контрагент": name,
                "ДоговорID": contract.get("ДоговорID", ""), "Договор": contract.get("Договор", ""),
                "НомерДоговора": contract.get("НомерДоговора", ""), "ДатаДоговора": contract.get("ДатаДоговора", ""),
                "ВидРасчетовID": vr, "ВидРасчетовСПокупателем": vr_name, "СуммаКоп": str(amount),
                "СуммаОблагаемаяКоп": str(taxable) if status == "KNOWN" else "",
                "СуммаНеоблагаемаяКоп": str(exempt) if status == "KNOWN" else "",
                "СуммаНДСКоп": str(vat) if status == "KNOWN" else "",
                "Проведен": str(int(j["CLOSED"].strip() or "0") & 1), "ПомеченНаУдаление": "1" if j["ISMARK"] == "*" else "0",
                "Комментарий": base.comments.get(iddoc, ""), "VATCompositionStatus": status,
                "_reason": reason, "_on_top": on_top, "_owner": contract.get("owner", ""), "_iddoc": iddoc,
                "_rows": [(int(r["LINENO"]), *(kopecks(r[tf[f"(P){c}"]]) for c in ("Сумма", "НДС", "Всего"))) for r in rows],
                "_variant": (variant.get("DESCR", "").strip(), variant.get(base.variant_fields[0], "").strip(),
                             base.vat_rates.get(variant.get(base.variant_fields[1], ""), "")),
            }
    return docs


def expected_ledger(base: Base, window: tuple[str, str]) -> dict[tuple[str, int, str], dict]:
    """62.1/62.2 sides of postings dated within the window, keyed by (document_id, posting number, side)."""
    start, end = (w.replace("-", "") for w in window)
    sides: dict[tuple[str, int, str], dict] = {}
    entries = read_dbf(base.dir / "1SENTRY.DBF")
    # The export numbers postings in the order 1C returns them for a document (index DOCID, NUMBER, CORNO), from 1.
    ordinal: dict[int, int] = {}
    by_doc: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(entries):
        by_doc[e["DOCID"]].append(i)
    for indexes in by_doc.values():
        for n, i in enumerate(sorted(indexes, key=lambda i: (int(entries[i]["NUMBER"]), int(entries[i]["CORNO"]))), 1):
            ordinal[i] = n
    for i, e in enumerate(entries):
        if not (start <= e["DATE"] <= end):
            continue
        dt, kt = base.accounts.get(e["ACCDTID"], ""), base.accounts.get(e["ACCKTID"], "")
        for side, account, prefix in (("D", dt, "DT"), ("K", kt, "KT")):
            if account not in ("62.1", "62.2"):
                continue
            values = [e[f"{prefix}SC{i}"] for i in range(3)]
            kinds = [e[f"V{prefix}SC{i}"] for i in range(3)]
            customer = base.customer_id(values[0][:9])[0] if kinds[0] == base.account_subconto[account][0] else ""
            contract = base.contract(values[1][:9]) if kinds[1] == base.account_subconto[account][1] else {}
            vr = base.settlement_kind(values[2][:9])[0] if kinds[2] == base.account_subconto[account][2] else ""
            key = (f"DOC|{base.key(e['DOCID'])}", ordinal[i], side)
            sides[key] = {
                "ДатаОперации": iso(e["DATE"]), "СуммаКоп": str(kopecks(e["SUM"])), "СчетДебета": dt, "СчетКредита": kt,
                "Счет62": account, "Сторона62": side, "КонтрагентID": customer, "ДоговорID": contract.get("ДоговорID", ""),
                "ВидРасчетовID": vr, "СодержаниеПроводки": e[base.entry_fields[0]],
                "ПервичныйДокумент": e[base.entry_fields[1]], "КомментарийДокумента": base.comments.get(e["DOCID"], ""),
            }
    return sides


# --- comparison ----------------------------------------------------------------------------------------

DOC_FIELDS = ["ВидДокумента", "НомерДокумента", "ДатаДокумента", "ПозицияДокумента", "КонтрагентID", "Контрагент",
              "ДоговорID", "Договор", "НомерДоговора", "ДатаДоговора", "ВидРасчетовID", "ВидРасчетовСПокупателем",
              "СуммаКоп", "СуммаОблагаемаяКоп", "СуммаНеоблагаемаяКоп", "СуммаНДСКоп", "Проведен", "ПомеченНаУдаление",
              "Комментарий", "VATCompositionStatus"]
LEDGER_FIELDS = ["ДатаОперации", "СуммаКоп", "СчетДебета", "СчетКредита", "Счет62", "Сторона62", "КонтрагентID",
                 "ДоговорID", "ВидРасчетовID", "СодержаниеПроводки", "ПервичныйДокумент", "КомментарийДокумента"]


def compare(base_docs: dict[str, dict], documents: list[dict], base_sides: dict, ledger: list[dict]) -> dict:
    result: dict = {"documents": {}, "ledger": {}}
    exported = {d["document_id"]: d for d in documents}
    r = result["documents"]
    r["base"], r["export"] = len(base_docs), len(exported)
    r["missing_in_export"] = sorted(set(base_docs) - set(exported))
    r["extra_in_export"] = sorted(set(exported) - set(base_docs))
    diffs: dict[str, list] = defaultdict(list)
    for key in sorted(set(base_docs) & set(exported)):
        b, x = base_docs[key], exported[key]
        for field in DOC_FIELDS:
            if b[field] != x[field]:
                diffs[field].append((key, x[field], b[field]))
    r["field_differences"] = {f: len(v) for f, v in diffs.items()}
    r["examples"] = {f: v[:EXAMPLES] for f, v in diffs.items()}

    l = result["ledger"]
    rows: dict[tuple[str, int, str], dict] = {}
    for row in ledger:
        parts = row["ledger_event_id"].split("|")
        rows[(f"DOC|{parts[1]}", int(row["НомерПроводкиВДокументе"] or 0), row["Сторона62"])] = row
    l["base"], l["export"] = len(base_sides), len(rows)
    l["missing_in_export"] = sorted(set(base_sides) - set(rows))[:EXAMPLES]
    l["missing_count"] = len(set(base_sides) - set(rows))
    l["extra_in_export"] = sorted(set(rows) - set(base_sides))[:EXAMPLES]
    l["extra_count"] = len(set(rows) - set(base_sides))
    ldiffs: dict[str, list] = defaultdict(list)
    for key in sorted(set(base_sides) & set(rows)):
        b, x = base_sides[key], rows[key]
        for field in LEDGER_FIELDS:
            if b[field] != x[field]:
                ldiffs[field].append((key, x[field], b[field]))
    l["field_differences"] = {f: len(v) for f, v in ldiffs.items()}
    l["examples"] = {f: v[:EXAMPLES] for f, v in ldiffs.items()}
    return result


def revenue_by_document(base_sides: dict) -> dict[str, int]:
    total: dict[str, int] = defaultdict(int)
    for (doc, _, side), s in base_sides.items():
        if side == "D" and s["СчетКредита"].startswith("90"):
            total[doc] += int(s["СуммаКоп"])
    return total


def report(run: Path, base_dir: Path, window: tuple[str, str], base_docs: dict, documents: list[dict], base_sides: dict, result: dict) -> str:
    def title(d: dict) -> str:
        return f"{d['ВидДокумента']} № {d['НомерДокумента']} от {d['ДатаДокумента']}"

    lines = [f"# Сверка {run.name} с таблицами DBF базы", "",
             f"База `{base_dir}`, окно {'..'.join(window)}. Таблицы читались напрямую (1SJOURN, DH/DT документов, SC133, SC112, "
             "SC11147, 1SBLOB, 1SENTRY, 1SACCS), мимо объектной модели 1С, которой пользуется выгрузка.", ""]
    r = result["documents"]
    lines += ["## 1. Документы", "",
              f"В базе {r['base']} документов трёх видов в окне, в documents.csv {r['export']}; нет в выгрузке — {len(r['missing_in_export'])}, лишних в выгрузке — {len(r['extra_in_export'])}.", ""]
    for key in r["missing_in_export"][:EXAMPLES]:
        lines.append(f"- нет в выгрузке: {title(base_docs[key])}")
    lines += ["", "| Вид | Флаги | Документов | Сумма «Всего» по шапке | НДС по строкам |", "|---|---|---:|---:|---:|"]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for d in base_docs.values():
        groups[(d["ВидДокумента"], "все")].append(d)
        groups[(d["ВидДокумента"], f"проведён {d['Проведен']}, помечен {d['ПомеченНаУдаление']}")].append(d)
    for (kind, flags), docs in sorted(groups.items()):
        lines.append(f"| {kind} | {flags} | {len(docs)} | {rub(sum(int(d['СуммаКоп']) for d in docs))} | {rub(sum(sum(row[2] for row in d['_rows']) for d in docs))} |")
    lines += ["", "Расхождения полей documents.csv с базой:", ""]
    if r["field_differences"]:
        for field, count in r["field_differences"].items():
            lines.append(f"- **{field}** — {count}")
            for key, exported, expected in r["examples"][field][:5]:
                note = " (так 1С представляет элемент с пустым наименованием)" if exported == "<>" and expected == "" else ""
                lines.append(f"  - {title(base_docs[key])}: выгрузка «{exported}», база «{expected}»{note}")
    else:
        lines.append("- нет: все поля всех документов совпали (номер, дата, позиция, флаги, покупатель, договор, вид расчётов, сумма, агрегаты и статус НДС, комментарий).")
    lines.append("")

    on_top = [d for d in base_docs.values() if d["_on_top"]]
    lines += ["## 2. НДС «сверху» и ставка", "",
              f"По строкам базы документов с НДС «сверху» («Всего» = «Сумма» + «НДС»): {len(on_top)} — " + ", ".join(title(d) for d in on_top) + ".",
              f"По журналу запуска: {len(e04.vat_on_top((run / '1c_export.log').read_bytes().decode('cp1251', 'replace')))}.", ""]
    rates = Counter()
    odd = []
    for d in base_docs.values():
        if d["VATCompositionStatus"] == "KNOWN" and int(d["СуммаНДСКоп"] or 0) > 0:
            rate = e04.implied_rate(int(d["СуммаНДСКоп"]), int(d["СуммаОблагаемаяКоп"]))
            rates[rate] += 1
            if rate != "5%":
                odd.append(d)
    lines += [f"Ставка по агрегатам: {dict(rates.most_common())}.", ""]
    variants = Counter(d["_variant"] for d in base_docs.values())
    lines += ["Варианты расчёта налогов документов окна (наименование, «Сумма включает НДС», ставка НДС → документов):", ""]
    lines += [f"- «{v[0] or '—'}», {v[1] or '—'}, {v[2] or '—'} → {n}" for v, n in variants.most_common()]
    lines += [""]
    for d in on_top + odd:
        lines += [f"### {title(d)}", "",
                  f"Вариант расчёта налогов: «{d['_variant'][0] or '—'}», сумма включает НДС: {d['_variant'][1] or '—'}, ставка: {d['_variant'][2] or '—'}. "
                  f"Сумма документа {rub(int(d['СуммаКоп']))}, НДС {rub(int(d['СуммаНДСКоп'] or 0))}.", "",
                  "| Строка | Сумма | НДС | Всего |", "|---:|---:|---:|---:|"]
        lines += [f"| {n} | {rub(s)} | {rub(v)} | {rub(t)} |" for n, s, v, t in d["_rows"]]
        lines += [""]

    revenue = revenue_by_document(base_sides)
    differs = [d for k, d in base_docs.items() if d["Проведен"] == "1" and d["ВидДокумента"] != "Счет" and revenue.get(k, 0) != int(d["СуммаКоп"])]
    lines += ["## 3. Проведённые исполнения против проводок Дт 62 Кт 90 в 1SENTRY", "",
              f"Документов, где сумма «Всего» не равна сумме проводок Дт 62 Кт 90: {len(differs)}" + (": " + ", ".join(title(d) for d in differs[:EXAMPLES]) if differs else "."), ""]

    foreign = [d for d in base_docs.values() if d["_owner"] and d["_owner"] != d["КонтрагентID"]]
    lines += ["## 4. Договоры", "",
              f"Документов окна с договором другого покупателя: {len(foreign)}" + (" — " + "; ".join(f"{title(d)}: покупатель {d['КонтрагентID']}, владелец договора {d['_owner']}" for d in foreign) if foreign else "") + ".", ""]

    l = result["ledger"]
    lines += ["## 5. Проводки 62 (ledger62.csv против 1SENTRY)", "",
              f"Сторон 62 в базе {l['base']}, в ledger62 {l['export']}; нет в выгрузке — {l['missing_count']}, лишних — {l['extra_count']}.", ""]
    if l["field_differences"]:
        for field, count in l["field_differences"].items():
            lines.append(f"- **{field}** — {count}")
            for key, exported, expected in l["examples"][field][:5]:
                lines.append(f"  - {key[0]} проводка {key[1]} {key[2]}: выгрузка «{exported}», база «{expected}»")
    else:
        lines.append("- расхождений полей нет (дата, сумма, счета, сторона, покупатель, договор, вид расчётов, содержание, первичный документ, комментарий документа).")
    lines.append("")
    return "\n".join(lines) + "\n"


def contract_codes(base: Base) -> dict:
    owners: dict[str, set[str]] = defaultdict(set)
    for c in base.contracts.values():
        owners[c["CODE"].strip()].add(c["PARENTEXT"])
    shared = {code: len(o) for code, o in owners.items() if len(o) > 1}
    return {"contracts": len(base.contracts), "codes": len(owners), "codes_with_several_owners": len(shared),
            "most_shared": sorted(shared.items(), key=lambda kv: -kv[1])[:5]}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--window", help="YYYY-MM-DD..YYYY-MM-DD; by default the window of the run log")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    start = e02.run_start(e02.log_of(args.run))
    window = tuple(args.window.split("..")) if args.window else (start[1] if start else None)
    local = Findings()
    documents = read_table(args.run, e04.DOCUMENTS_FILE, e04.DOCUMENTS_HEADER, local)
    ledger = read_table(args.run, LEDGER_FILE, LEDGER_HEADER, local)
    if not window or documents is None or ledger is None:
        print(f"набор не прочитан: окно {window}, {local.items}")
        return 2
    if args.window:
        # A narrower window (a base copy made earlier than the run): compare only what both sides cover.
        documents = [d for d in documents if window[0] <= d["ДатаДокумента"] <= window[1]]
        ledger = [r for r in ledger if window[0] <= r["ДатаОперации"] <= window[1]]
    try:
        base = Base(args.base, args.metadata)
    except (OSError, StopIteration, KeyError, ValueError) as error:
        print(f"база не прочитана: {error}")
        return 2
    base_docs = expected_documents(base, window)
    base_sides = expected_ledger(base, window)
    result = compare(base_docs, documents, base_sides, ledger)
    result["contract_codes"] = contract_codes(base)
    text = report(args.run, args.base, window, base_docs, documents, base_sides, result)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    summary = {"documents": {k: v for k, v in result["documents"].items() if k != "examples"},
               "ledger": {k: v for k, v in result["ledger"].items() if k != "examples"}, "contract_codes": result["contract_codes"]}
    summary["documents"]["missing_in_export"] = len(summary["documents"]["missing_in_export"])
    summary["documents"]["extra_in_export"] = len(summary["documents"]["extra_in_export"])
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    clean = not any([result["documents"]["missing_in_export"], result["documents"]["extra_in_export"], result["documents"]["field_differences"],
                     result["ledger"]["missing_count"], result["ledger"]["extra_count"], result["ledger"]["field_differences"]])
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
