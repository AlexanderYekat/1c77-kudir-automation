"""reports/check_dbf_e04.py на синтетической базе DBF.

База собирается так, как её хранит 1С 7.7 (dBase, cp1251, ID в base-36, коды счетов по маске, итог колонки «Всего»
в шапке, комментарий документа в 1SBLOB): счёт с НДС в сумме, акт с НДС «сверху» на основании счёта, проведённый,
с проводкой Дт 62.1 Кт 90.1.1 второй в документе. Проверяется чтение таблиц и сопоставление с documents.csv/ledger62.csv
так, как их пишет выгрузка: совпадающий набор чист, расхождение поля и потеря проводки видны.
"""

from __future__ import annotations

import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reports"))

import check_dbf_e04 as chk  # noqa: E402


def write_dbf(path: Path, fields: list[tuple[str, str, int]], rows: list[dict], deleted: int = -1) -> None:
    """dBase III table: fields (name, type, length); values are padded like 1C does (text left, numbers right)."""
    record_len = 1 + sum(n for _, _, n in fields)
    header_len = 32 + 32 * len(fields) + 1
    out = bytearray(struct.pack("<B3BIHH20x", 0x03, 126, 10, 12, len(rows), header_len, record_len))
    for name, kind, length in fields:
        out += struct.pack("<11sc4xBB14x", name.encode("ascii"), kind.encode("ascii"), length, 0)
    out += b"\r"
    for i, row in enumerate(rows):
        out += b"*" if i == deleted else b" "
        for name, kind, length in fields:
            value = str(row.get(name, ""))
            out += (value.rjust(length) if kind == "N" else value.ljust(length)).encode("cp1251")[:length]
    path.write_bytes(bytes(out) + b"\x1a")


def b36(number: int, width: int = 6) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    text = ""
    while number:
        number, rest = divmod(number, 36)
        text = digits[rest] + text
    return (text or "0").rjust(width)


DD = """#AccWare Data Dictionary
T=SC112   |Справочник Договоры           |A          |SC112      |1
F=ID        |ID object           |C   |9     |0
F=CODE      |object code         |C   |6     |0
F=DESCR     |object description  |C   |85    |0
F=PARENTEXT |Parent in other tabl|C   |9     |0
F=SP13387   |(P)ДатаВозникновения|D   |8     |0
T=SC133   |Справочник Контрагенты        |A          |SC133      |1
F=ID        |ID object           |C   |9     |0
F=CODE      |object code         |C   |8     |0
F=DESCR     |object description  |C   |30    |0
T=SC11147 |Справочник ВариантыРасчетаНало|A          |SC11147    |1
F=ID        |ID object           |C   |9     |0
F=DESCR     |object description  |C   |50    |0
F=SP11140   |(P)СуммаВключаетНДС |N   |2     |0
F=SP11143   |(P)СтавкаНДС        |C   |9     |0
T=DH12517 |Документ Счет                 |A          |DH12517    |1
F=IDDOC     |ID Document's       |C   |9     |0
F=SP12753   |(P)Контрагент       |C   |9     |0
F=SP16112   |(P)Договор          |C   |9     |0
F=SP16113   |(P)ВариантРасчетаНал|C   |9     |0
F=SP12515   |(P)Всего            |N   |16    |2
T=DT12517 |Документ (Мн.ч.) Счет         |A          |DT12517    |1
F=IDDOC     |ID Document's       |C   |9     |0
F=LINENO    |LineNo              |N   |4     |0
F=SP12511   |(P)Сумма            |N   |16    |2
F=SP12512   |(P)НДС              |N   |16    |2
F=SP12515   |(P)Всего            |N   |16    |2
T=DH294   |Документ РасходнаяНакладная   |A          |DH294      |1
F=IDDOC     |ID Document's       |C   |9     |0
F=SP277     |(P)Контрагент       |C   |9     |0
F=SP278     |(P)Договор          |C   |9     |0
F=SP45581   |(P)ВыбВидыРасчетовСП|C   |9     |0
F=SP290     |(P)Всего            |N   |16    |2
T=DT294   |Документ (Мн.ч.) РасходнаяНакл|A          |DT294      |1
F=IDDOC     |ID Document's       |C   |9     |0
F=LINENO    |LineNo              |N   |4     |0
F=SP286     |(P)Сумма            |N   |16    |2
F=SP287     |(P)НДС              |N   |16    |2
F=SP290     |(P)Всего            |N   |16    |2
T=DH321   |Документ ОказаниеУслуг        |A          |DH321      |1
F=IDDOC     |ID Document's       |C   |9     |0
F=SP309     |(P)Контрагент       |C   |9     |0
F=SP310     |(P)Договор          |C   |9     |0
F=SP16019   |(P)ВариантРасчетаНал|C   |9     |0
F=SP45579   |(P)ВыбВидыРасчетовСП|C   |9     |0
F=SP319     |(P)Всего            |N   |16    |2
T=DT321   |Документ (Мн.ч.) ОказаниеУслуг|A          |DT321      |1
F=IDDOC     |ID Document's       |C   |9     |0
F=LINENO    |LineNo              |N   |4     |0
F=SP316     |(P)Сумма            |N   |16    |2
F=SP317     |(P)НДС              |N   |16    |2
F=SP319     |(P)Всего            |N   |16    |2
T=1SENTRY |Проводки                      |A          |1SENTRY    |1
F=DOCID     |Document Id         |C   |9     |0
F=SP547     |(P)СодержаниеПроводк|C   |75    |0
F=SP45088   |(P)ПервичныйДокумент|C   |25    |0
"""
METADATA = """{"11631","ВидыРасчетовСПокупателями","Виды расчетов с покупателями","Виды расчетов с покупателями",
{"EnumVal",
{"45565","РасчетыВ_Розницу","","Расчеты за товары за наличный расчет","Расчеты за товары за наличный расчет"},
{"45567","РасчетыЗаУслугиПоРемонтуККМ_Весов","","Расчеты За Услуги По Ремонту ККМ и Весов","Расчеты За Услуги По Ремонту ККМ и Весов"}}},
"""
INVOICE, ACT = 460001, 460002
CUSTOMER, CONTRACT, VARIANT_IN, VARIANT_ON_TOP, RATE = "     1   ", "     2   ", "     3   ", "     4   ", "     5   "
ACC62, ACC90, ACC20 = "     A   ", "     B   ", "     C   "
COMMENT = "Введен на основании: Счет на оплату № 00087        от 14 Января 2026 г."
ACT_ID = f"DOC|{{\"O\",\"0\",\"0\",\"321\",\"0\",\"0\",\"{ACT:>10}   \"}}"
INVOICE_ID = f"DOC|{{\"O\",\"0\",\"0\",\"12517\",\"0\",\"0\",\"{INVOICE:>10}   \"}}"


def build_base(directory: Path) -> None:
    (directory / "1Cv7.DD").write_bytes(DD.encode("cp1251"))
    inv, act = b36(INVOICE) + "   ", b36(ACT) + "   "
    write_dbf(directory / "SC133.DBF", [("ID", "C", 9), ("CODE", "C", 8), ("DESCR", "C", 30)],
              [{"ID": CUSTOMER, "CODE": "1548302", "DESCR": "Ромашка"}])
    write_dbf(directory / "SC112.DBF", [("ID", "C", 9), ("CODE", "C", 6), ("DESCR", "C", 85), ("PARENTEXT", "C", 9), ("SP13387", "D", 8)],
              [{"ID": CONTRACT, "CODE": "000002", "DESCR": "5", "PARENTEXT": CUSTOMER, "SP13387": "20260101"}])
    write_dbf(directory / "SC11147.DBF", [("ID", "C", 9), ("DESCR", "C", 50), ("SP11140", "N", 2), ("SP11143", "C", 9)],
              [{"ID": VARIANT_IN, "DESCR": "НДС в сумме 5%", "SP11140": "1", "SP11143": RATE},
               {"ID": VARIANT_ON_TOP, "DESCR": "НДС сверху 5%", "SP11140": "0", "SP11143": RATE}])
    write_dbf(directory / "SC211.DBF", [("ID", "C", 9), ("DESCR", "C", 25)], [{"ID": RATE, "DESCR": "5%"}])
    write_dbf(directory / "1SJOURN.DBF", [("IDDOC", "C", 9), ("IDDOCDEF", "C", 4), ("DATE", "D", 8), ("TIME", "C", 6),
                                          ("DOCNO", "C", 12), ("CLOSED", "N", 1), ("ISMARK", "C", 1)],
              [{"IDDOC": inv, "IDDOCDEF": b36(12517, 4), "DATE": "20260114", "TIME": b36(100000000), "DOCNO": "00087", "CLOSED": "0", "ISMARK": " "},
               {"IDDOC": act, "IDDOCDEF": b36(321, 4), "DATE": "20260204", "TIME": b36(100000000), "DOCNO": "00166", "CLOSED": "5", "ISMARK": " "},
               {"IDDOC": b36(460003) + "   ", "IDDOCDEF": b36(321, 4), "DATE": "20251231", "TIME": b36(1), "DOCNO": "09999", "CLOSED": "1", "ISMARK": " "}])
    money = [("IDDOC", "C", 9), ("LINENO", "N", 4)]
    write_dbf(directory / "DH12517.DBF", [("IDDOC", "C", 9), ("SP12753", "C", 9), ("SP16112", "C", 9), ("SP16113", "C", 9), ("SP12515", "N", 16)],
              [{"IDDOC": inv, "SP12753": CUSTOMER, "SP16112": CONTRACT, "SP16113": VARIANT_IN, "SP12515": "16500.00"}])
    write_dbf(directory / "DT12517.DBF", money + [("SP12511", "N", 16), ("SP12512", "N", 16), ("SP12515", "N", 16)],
              [{"IDDOC": inv, "LINENO": "1", "SP12511": "16500.00", "SP12512": "785.71", "SP12515": "16500.00"}])
    write_dbf(directory / "DH294.DBF", [("IDDOC", "C", 9), ("SP277", "C", 9), ("SP278", "C", 9), ("SP45581", "C", 9), ("SP290", "N", 16)], [])
    write_dbf(directory / "DT294.DBF", money + [("SP286", "N", 16), ("SP287", "N", 16), ("SP290", "N", 16)], [])
    write_dbf(directory / "DH321.DBF", [("IDDOC", "C", 9), ("SP309", "C", 9), ("SP310", "C", 9), ("SP16019", "C", 9), ("SP45579", "C", 9), ("SP319", "N", 16)],
              [{"IDDOC": act, "SP309": CUSTOMER, "SP310": CONTRACT, "SP16019": VARIANT_ON_TOP, "SP45579": b36(45567) + "   ", "SP319": "4725.00"}])
    write_dbf(directory / "DT321.DBF", money + [("SP316", "N", 16), ("SP317", "N", 16), ("SP319", "N", 16)],
              [{"IDDOC": act, "LINENO": "1", "SP316": "4500.00", "SP317": "225.00", "SP319": "4725.00"}])
    # Comment of the act: two blocks, the length in hex at the start of block 0; a deleted row of another field.
    text = f"{len(COMMENT):x}".ljust(9) + COMMENT
    write_dbf(directory / "1SBLOB.DBF", [("FIELDID", "C", 4), ("OBJID", "C", 9), ("BLOCKNO", "N", 4), ("BLOCK", "C", 80)],
              [{"FIELDID": b36(475, 4), "OBJID": act, "BLOCKNO": "1", "BLOCK": text[80:]},
               {"FIELDID": b36(475, 4), "OBJID": act, "BLOCKNO": "0", "BLOCK": text[:80]},
               {"FIELDID": b36(68, 4), "OBJID": inv, "BLOCKNO": "0", "BLOCK": "5        адрес"}])
    sc = [("SC0", "C", 4), ("SC1", "C", 4), ("SC2", "C", 4)]
    write_dbf(directory / "1SACCS.DBF", [("ID", "C", 9), ("SCHKOD", "C", 11)] + sc,
              [{"ID": ACC62, "SCHKOD": " 62. 1.", "SC0": "  EE", "SC1": " 8GG", "SC2": " 90P"},
               {"ID": ACC90, "SCHKOD": " 90. 1. 1."}, {"ID": ACC20, "SCHKOD": " 20."}])
    entry = [("DOCID", "C", 9), ("NUMBER", "N", 5), ("CORNO", "N", 5), ("DATE", "D", 8), ("ACCDTID", "C", 9), ("ACCKTID", "C", 9),
             ("SUM", "N", 14), ("SP547", "C", 75), ("SP45088", "C", 25)]
    for side in ("DT", "KT"):
        for i in range(3):
            entry += [(f"V{side}SC{i}", "C", 4), (f"{side}SC{i}", "C", 13)]
    # Stored out of order: the 62 posting has NUMBER 7 and is the second of the act (after NUMBER 3).
    write_dbf(directory / "1SENTRY.DBF", entry, [
        {"DOCID": act, "NUMBER": "7", "CORNO": "0", "DATE": "20260204", "ACCDTID": ACC62, "ACCKTID": ACC90, "SUM": "4725.00",
         "SP547": "Учтена выручка", "VDTSC0": "  EE", "DTSC0": CUSTOMER, "VDTSC1": " 8GG", "DTSC1": CONTRACT,
         "VDTSC2": " 90P", "DTSC2": b36(45567) + "   "},
        {"DOCID": act, "NUMBER": "3", "CORNO": "0", "DATE": "20260204", "ACCDTID": ACC90, "ACCKTID": ACC20, "SUM": "1000.00"},
    ])


def documents_rows() -> list[dict]:
    common = {"КонтрагентID": "C|1548302", "Контрагент": "Ромашка", "ДоговорID": "D|C|1548302|000002", "Договор": "5",
              "НомерДоговора": "5", "ДатаДоговора": "2026-01-01", "ПомеченНаУдаление": "0", "Содержание": "",
              "VATCompositionStatus": "KNOWN", "СуммаНеоблагаемаяКоп": "0"}
    return [
        dict(common, document_id=INVOICE_ID, DocumentRole="INVOICE", DocumentType="INVOICE", ВидДокумента="Счет",
             НомерДокумента="00087", ДатаДокумента="2026-01-14", ПозицияДокумента=f"#20260114 100000000{INVOICE:>10}   ",
             ВидРасчетовID="", ВидРасчетовСПокупателем="", СуммаКоп="1650000", СуммаОблагаемаяКоп="1650000",
             СуммаНДСКоп="78571", Проведен="0", Комментарий=""),
        dict(common, document_id=ACT_ID, DocumentRole="RECEIVABLE", DocumentType="SERVICE_ACT", ВидДокумента="ОказаниеУслуг",
             НомерДокумента="00166", ДатаДокумента="2026-02-04", ПозицияДокумента=f"#20260204 100000000{ACT:>10}   ",
             ВидРасчетовID="VR|РасчетыЗаУслугиПоРемонтуККМ_Весов", ВидРасчетовСПокупателем="Расчеты За Услуги По Ремонту ККМ и Весов",
             СуммаКоп="472500", СуммаОблагаемаяКоп="472500", СуммаНДСКоп="22500", Проведен="1", Комментарий=COMMENT),
    ]


def ledger_rows() -> list[dict]:
    return [{"ledger_event_id": f"LED|{ACT_ID[4:]}|2|1", "НомерПроводкиВДокументе": "2", "ДатаОперации": "2026-02-04",
             "СуммаКоп": "472500", "СчетДебета": "62.1", "СчетКредита": "90.1.1", "Счет62": "62.1", "Сторона62": "D",
             "КонтрагентID": "C|1548302", "ДоговорID": "D|C|1548302|000002", "ВидРасчетовID": "VR|РасчетыЗаУслугиПоРемонтуККМ_Весов",
             "СодержаниеПроводки": "Учтена выручка".ljust(75), "ПервичныйДокумент": " " * 25, "КомментарийДокумента": COMMENT}]


class CheckDbfE04Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        build_base(self.tmp)
        (self.tmp / "metadata.txt").write_text(METADATA, encoding="utf-8")
        self.base = chk.Base(self.tmp, self.tmp / "metadata.txt")
        self.window = ("2026-01-01", "2026-10-10")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def result(self, documents: list[dict], ledger: list[dict]) -> dict:
        return chk.compare(chk.expected_documents(self.base, self.window), documents, chk.expected_ledger(self.base, self.window), ledger)

    def test_readers(self) -> None:
        self.assertEqual(chk.account_code(" 90. 1. 1. "), "90.1.1")
        self.assertEqual(chk.kopecks("      785.71"), 78571)
        self.assertEqual(chk.b36("  9ZO0"), 466128)
        self.assertEqual(self.base.comments, {b36(ACT) + "   ": COMMENT})
        self.assertEqual(self.base.settlement[45567][0], "РасчетыЗаУслугиПоРемонтуККМ_Весов")
        rows = chk.read_dbf(self.tmp / "SC133.DBF")
        self.assertEqual(len(rows), 1)
        write_dbf(self.tmp / "SC133.DBF", [("ID", "C", 9), ("CODE", "C", 8), ("DESCR", "C", 30)], [{"ID": "1"}, {"ID": "2"}], deleted=0)
        self.assertEqual([r["ID"].strip() for r in chk.read_dbf(self.tmp / "SC133.DBF")], ["2"])

    def test_expected_documents(self) -> None:
        docs = chk.expected_documents(self.base, self.window)
        self.assertEqual(set(docs), {INVOICE_ID, ACT_ID})  # the document of 2025 is outside the window
        act = docs[ACT_ID]
        self.assertEqual((act["СуммаКоп"], act["СуммаОблагаемаяКоп"], act["СуммаНДСКоп"], act["_on_top"]), ("472500", "472500", "22500", 1))
        self.assertEqual(act["_variant"], ("НДС сверху 5%", "0", "5%"))
        self.assertEqual((act["Проведен"], docs[INVOICE_ID]["Проведен"]), ("1", "0"))

    def test_matching_run_is_clean(self) -> None:
        result = self.result(documents_rows(), ledger_rows())
        self.assertEqual(result["documents"]["field_differences"], {})
        self.assertEqual((result["documents"]["missing_in_export"], result["documents"]["extra_in_export"]), ([], []))
        self.assertEqual(result["ledger"]["field_differences"], {})
        self.assertEqual((result["ledger"]["missing_count"], result["ledger"]["extra_count"]), (0, 0))

    def test_differences_are_reported(self) -> None:
        documents = documents_rows()
        documents[1]["СуммаКоп"] = "450000"
        documents[1]["Комментарий"] = COMMENT.strip() + " "
        result = self.result(documents, [])
        self.assertEqual(result["documents"]["field_differences"], {"СуммаКоп": 1, "Комментарий": 1})
        self.assertEqual(result["ledger"]["missing_count"], 1)
        result = self.result(documents_rows()[:1], ledger_rows())
        self.assertEqual(result["documents"]["missing_in_export"], [ACT_ID])


if __name__ == "__main__":
    unittest.main()
