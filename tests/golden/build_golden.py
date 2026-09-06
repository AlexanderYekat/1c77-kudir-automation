"""Собрать golden CSV schema v2. Matcher не вызывается."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from kudir_proto.csv_io import (  # noqa: E402
    DOCUMENTS_FIELDS,
    KUDIR_RESULT_FIELDS,
    LEDGER_FIELDS,
    MATCHES_FIELDS,
    OPENING_FIELDS,
    PAYMENTS_FIELDS,
    RUN_STATUS_KEYS,
    UNRESOLVED_FIELDS,
    write_kv,
    write_rows,
)

GOLDEN = Path(__file__).resolve().parent
C1 = "C|00001"
C1N = "Клиент А"
V1 = "V|00001"
VN = "По договору"
ACC = "62.1"


def _blank(fields: list[str], **kwargs: str) -> dict[str, str]:
    row = {k: "" for k in fields}
    row.update(kwargs)
    return row


def pay(**kwargs: str) -> dict[str, str]:
    row = _blank(
        PAYMENTS_FIELDS,
        PaymentKind="BANK",
        PaymentGranularity="POSTING",
        ВидДокументаОплаты="Выписка",
        СчетДебета="51",
        СчетКредита="62.1",
        КонтрагентID=C1,
        Контрагент=C1N,
        ВидРасчетовID=V1,
        ВидРасчетовСПокупателем=VN,
        is_target="1",
    )
    row.update(kwargs)
    return row


def led(**kwargs: str) -> dict[str, str]:
    row = _blank(
        LEDGER_FIELDS,
        Счет62=ACC,
        КонтрагентID=C1,
        Контрагент=C1N,
        ВидРасчетовID=V1,
        ВидРасчетовСПокупателем=VN,
    )
    row.update(kwargs)
    return row


def doc(**kwargs: str) -> dict[str, str]:
    row = _blank(
        DOCUMENTS_FIELDS,
        Проведен="1",
        ПомеченНаУдаление="0",
        КонтрагентID=C1,
        Контрагент=C1N,
        ВидРасчетовID=V1,
        ВидРасчетовСПокупателем=VN,
        СуммаОблагаемаяКоп="0",
        СуммаНеоблагаемаяКоп="0",
        СуммаНДСКоп="0",
    )
    row.update(kwargs)
    # Matching-фикстуры без явного НДС: вся сумма — необлагаемая (колонка НДС=0).
    amount = (row.get("СуммаКоп") or "0").strip() or "0"
    if (
        "СуммаОблагаемаяКоп" not in kwargs
        and "СуммаНеоблагаемаяКоп" not in kwargs
        and "СуммаНДСКоп" not in kwargs
        and amount != "0"
    ):
        row["СуммаОблагаемаяКоп"] = "0"
        row["СуммаНеоблагаемаяКоп"] = amount
        row["СуммаНДСКоп"] = "0"
    return row


def opening(**kwargs: str) -> dict[str, str]:
    row = _blank(
        OPENING_FIELDS,
        КонтрагентID=C1,
        Счет62=ACC,
        ВидРасчетовID=V1,
        ВидРасчетов=VN,
        ОстатокДтКоп="0",
        ОстатокКтКоп="0",
    )
    row.update(kwargs)
    return row


def result(**kwargs: str) -> dict[str, str]:
    row = _blank(
        KUDIR_RESULT_FIELDS,
        vat_base_kopecks="0",
        vat_kopecks="0",
        income_kopecks=kwargs.get("amount_kopecks", "0"),
    )
    row.update(kwargs)
    if "income_kopecks" not in kwargs:
        row["income_kopecks"] = row["amount_kopecks"]
    return row


def match(**kwargs: str) -> dict[str, str]:
    row = _blank(
        MATCHES_FIELDS,
        direct_reference_score="0",
        invoice_link_score="0",
        amount_score="0",
        contract_score="0",
        calculation_type_score="0",
        semantic_score="0",
        chronology_score="0",
        score="0",
        confidence="HIGH",
    )
    row.update(kwargs)
    return row


def status(
    run_id: str,
    tax_ready: str = "1",
    unresolved_debt: str = "0",
    unresolved_count: str = "0",
    st: str = "SUCCESS_DEGRADED",
    state_uncertain: str = "0",
    historical_unresolved_count: str = "0",
    historical_unresolved_kopecks: str = "0",
) -> dict[str, str]:
    return {
        "run_id": run_id,
        "status": st,
        "tax_ready": tax_ready,
        "unresolved_debt_kopecks": unresolved_debt,
        "unresolved_count": unresolved_count,
        "state_uncertain": state_uncertain,
        "historical_unresolved_count": historical_unresolved_count,
        "historical_unresolved_kopecks": historical_unresolved_kopecks,
        "parser_available": "0",
        "schema_version": "2",
        "scoring_hash": "golden",
    }


def write_case(
    name: str,
    manifest: dict[str, str],
    payments: list[dict[str, str]],
    ledger: list[dict[str, str]],
    documents: list[dict[str, str]],
    openings: list[dict[str, str]],
    results: list[dict[str, str]],
    matches: list[dict[str, str]],
    unresolved: list[dict[str, str]],
    run_status: dict[str, str],
    analysis_log: str | None = None,
) -> None:
    d = GOLDEN / name
    d.mkdir(parents=True, exist_ok=True)
    write_kv(d / "manifest.csv", manifest)
    write_rows(d / "payments.csv", PAYMENTS_FIELDS, payments)
    write_rows(d / "ledger62.csv", LEDGER_FIELDS, ledger)
    write_rows(d / "documents.csv", DOCUMENTS_FIELDS, documents)
    write_rows(d / "opening_balances.csv", OPENING_FIELDS, openings)
    write_rows(d / "expected_kudir_result.csv", KUDIR_RESULT_FIELDS, results)
    write_rows(d / "expected_matches.csv", MATCHES_FIELDS, matches)
    write_rows(d / "expected_unresolved.csv", UNRESOLVED_FIELDS, unresolved)
    write_kv(d / "expected_run_status.csv", run_status, keys=RUN_STATUS_KEYS)
    if analysis_log is not None:
        (d / "expected_analysis.log").write_text(analysis_log, encoding="utf-8")


def q1_2026(run_id: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "schema_version": "2",
        "encoding": "windows-1251",
        "history_start": "2025-10-01",
        "date_start": "2026-01-01",
        "date_end": "2026-03-31",
        "quarter_end": "2026-03-31",
        "match_horizon_end": "2026-06-30",
    }


def q4_2025(run_id: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "schema_version": "2",
        "encoding": "windows-1251",
        "history_start": "2025-10-01",
        "date_start": "2025-10-01",
        "date_end": "2025-12-31",
        "quarter_end": "2025-12-31",
        "match_horizon_end": "2026-03-31",
    }


def build_2_1() -> None:
    run_id = "KUDIR_GOLDEN_2_1"
    inv = "DOC|0000000001"
    doc_a = "DOC|0000000002"
    doc_b = "DOC|0000000003"
    p1 = "BANK|0000000100|2"
    p2 = "BANK|0000000300|2"
    da = "D|00001|A"
    write_case(
        "advance_then_later_payment",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0001",
                ДатаДокументаОплаты="2026-01-10",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                НазначениеПлатежа="Оплата по счету №1",
            ),
            pay(
                payment_id=p2,
                ДатаОперации="2026-03-01",
                ПозицияДокумента="0000000300",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0002",
                ДатаДокументаОплаты="2026-03-01",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            led(
                ledger_event_id="LED|0000000100|2|1",
                source_document_id="BANK|0000000100",
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0001",
                ДатаДокумента="2026-01-10",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000200|1|1",
                source_document_id=doc_a,
                ДатаОперации="2026-02-10",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-1",
                ДатаДокумента="2026-02-10",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000210|1|1",
                source_document_id=doc_b,
                ДатаОперации="2026-02-15",
                ПозицияДокумента="0000000210",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="B-1",
                ДатаДокумента="2026-02-15",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000300|2|1",
                source_document_id="BANK|0000000300",
                payment_id=p2,
                ДатаОперации="2026-03-01",
                ПозицияДокумента="0000000300",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0002",
                ДатаДокумента="2026-03-01",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            doc(
                document_id=inv,
                DocumentRole="INVOICE",
                DocumentType="INVOICE",
                ВидДокумента="Счет на оплату",
                НомерДокумента="1",
                ДатаДокумента="2026-01-09",
                ПозицияДокумента="0000000001",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-1",
                ДатаДокумента="2026-02-10",
                ПозицияДокумента="0000000200",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
                Комментарий="Введен на основании: Счет на оплату №1 от 09.01.2026",
            ),
            doc(
                document_id=doc_b,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="B-1",
                ДатаДокумента="2026-02-15",
                ПозицияДокумента="0000000210",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id=doc_a,
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="1000000",
                repayment_kopecks="0",
                match_type="ADVANCE_INVOICE_BASE",
                confidence="HIGH",
                reason="HIGH_INVOICE_CHAIN",
                СодержаниеЗаписи="Получен аванс от Клиент А в счёт товара по накладной №A-1 от 2026-02-10",
            ),
            result(
                run_id=run_id,
                result_row_id="R00002",
                payment_id=p2,
                row_type="DEBT",
                matched_document_id=doc_b,
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                confidence="HIGH",
                reason="REMAINDER_AFTER_HIGH_ADVANCE_CLOSE",
                СодержаниеЗаписи="Оплата от Клиент А за товар по накладной №B-1 от 2026-02-15",
            ),
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_a,
                matched_amount_kopecks="1000000",
                match_type="ADVANCE_INVOICE_BASE",
                score="375",
                invoice_link_score="180",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="INVOICE_BASE_EXACT+EXACT_REMAINDER",
            ),
            match(
                payment_id=p2,
                document_id=doc_b,
                matched_amount_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                score="195",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            ),
        ],
        [],
        status(run_id),
    )


def build_2_2() -> None:
    run_id = "KUDIR_GOLDEN_2_2"
    p1 = "BANK|0000000200|3"
    doc_a = "DOC|0000000150"
    da, db = "D|00001|A", "D|00001|B"
    write_case(
        "multi_analytics_split",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-20",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="3",
                НомерДокументаОплаты="0010",
                ДатаДокументаОплаты="2026-01-20",
                СуммаКоп="1500000",
                ДоговорID="",
                Договор="",
                НазначениеПлатежа="",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000150|1|1",
                source_document_id=doc_a,
                ДатаОперации="2025-12-15",
                ПозицияДокумента="0000000150",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-10",
                ДатаДокумента="2025-12-15",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000200|3|1",
                source_document_id="BANK|0000000200",
                payment_id=p1,
                ДатаОперации="2026-01-20",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="3",
                ВидДокумента="Выписка",
                НомерДокумента="0010",
                ДатаДокумента="2026-01-20",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000200|3|2",
                source_document_id="BANK|0000000200",
                payment_id=p1,
                ДатаОперации="2026-01-20",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="3",
                ВидДокумента="Выписка",
                НомерДокумента="0010",
                ДатаДокумента="2026-01-20",
                СуммаКоп="500000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-10",
                ДатаДокумента="2025-12-15",
                ПозицияДокумента="0000000150",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            )
        ],
        [
            opening(ДоговорID=da, ДатаСальдо="2025-10-01"),
            opening(ДоговорID=db, ДатаСальдо="2025-10-01"),
        ],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="DEBT",
                matched_document_id=doc_a,
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                confidence="HIGH",
                reason="ANALYTICS_A_DEBT",
                СодержаниеЗаписи="Оплата от Клиент А за товар по накладной №A-10 от 2025-12-15",
            ),
            result(
                run_id=run_id,
                result_row_id="R00002",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id="",
                contract_id=db,
                amount_kopecks="500000",
                advance_kopecks="500000",
                repayment_kopecks="0",
                match_type="ADVANCE_OPEN",
                confidence="HIGH",
                reason="ANALYTICS_B_NO_DEBT",
                СодержаниеЗаписи="Получен аванс от Клиент А по договору B",
            ),
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_a,
                matched_amount_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                score="195",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            )
        ],
        [],
        status(run_id),
    )


def build_2_3() -> None:
    run_id = "KUDIR_GOLDEN_2_3"
    p1 = "BANK|0000000210|2"
    doc_a = "DOC|0000000140"
    da, db = "D|00001|A", "D|00001|B"
    write_case(
        "no_cross_analytics_netting",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-25",
                ПозицияДокумента="0000000210",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0011",
                ДатаДокументаОплаты="2026-01-25",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000140|1|1",
                source_document_id=doc_a,
                ДатаОперации="2025-12-10",
                ПозицияДокумента="0000000140",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-11",
                ДатаДокумента="2025-12-10",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000210|2|1",
                source_document_id="BANK|0000000210",
                payment_id=p1,
                ДатаОперации="2026-01-25",
                ПозицияДокумента="0000000210",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0011",
                ДатаДокумента="2026-01-25",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-11",
                ДатаДокумента="2025-12-10",
                ПозицияДокумента="0000000140",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            )
        ],
        [
            opening(ДоговорID=da, ДатаСальдо="2025-10-01"),
            opening(ДоговорID=db, ДатаСальдо="2025-10-01", ОстатокКтКоп="400000"),
        ],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="DEBT",
                matched_document_id=doc_a,
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                confidence="HIGH",
                reason="NO_NETTING_WITH_B_CREDIT",
                СодержаниеЗаписи="Оплата от Клиент А за товар по накладной №A-11 от 2025-12-10",
            )
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_a,
                matched_amount_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                score="195",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            )
        ],
        [],
        status(run_id),
    )


def build_2_4() -> None:
    run_id = "KUDIR_GOLDEN_2_4"
    p1 = "BANK|0000001210|2"
    inv = "DOC|0000001200"
    doc_jan = "DOC|0000000115"
    da = "D|00001|A"
    write_case(
        "december_advance_january_shipment",
        q4_2025(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2025-12-10",
                ПозицияДокумента="0000001210",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0090",
                ДатаДокументаОплаты="2025-12-10",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                НазначениеПлатежа="Оплата по счету №90",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000001210|2|1",
                source_document_id="BANK|0000001210",
                payment_id=p1,
                ДатаОперации="2025-12-10",
                ПозицияДокумента="0000001210",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0090",
                ДатаДокумента="2025-12-10",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            doc(
                document_id=inv,
                DocumentRole="INVOICE",
                DocumentType="INVOICE",
                ВидДокумента="Счет на оплату",
                НомерДокумента="90",
                ДатаДокумента="2025-12-09",
                ПозицияДокумента="0000001200",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
            doc(
                document_id=doc_jan,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="Я-90",
                ДатаДокумента="2026-01-15",
                ПозицияДокумента="0000000115",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
                Комментарий="Введен на основании: Счет на оплату №90 от 09.12.2025",
            ),
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id=doc_jan,
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="1000000",
                repayment_kopecks="0",
                match_type="ADVANCE_EXTENDED_MATCH",
                confidence="HIGH",
                reason="EXTENDED_MATCH_NO_TAX_RECALC_Q4",
                СодержаниеЗаписи="Получен аванс от Клиент А в счёт товара по накладной №Я-90 от 2026-01-15",
            )
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_jan,
                matched_amount_kopecks="1000000",
                match_type="ADVANCE_EXTENDED_MATCH",
                score="375",
                invoice_link_score="180",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="ADVANCE_EXTENDED_MATCH",
            )
        ],
        [],
        status(run_id),
    )


def build_2_5() -> None:
    run_id = "KUDIR_GOLDEN_2_5"
    p1 = "BANK|0000001220|2"
    doc_jan = "DOC|0000000125"
    da = "D|00001|A"
    write_case(
        "pre_vat_advance_post_vat_shipment",
        q4_2025(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2025-12-20",
                ПозицияДокумента="0000001220",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0091",
                ДатаДокументаОплаты="2025-12-20",
                СуммаКоп="1050000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000001220|2|1",
                source_document_id="BANK|0000001220",
                payment_id=p1,
                ДатаОперации="2025-12-20",
                ПозицияДокумента="0000001220",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0091",
                ДатаДокумента="2025-12-20",
                СуммаКоп="1050000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            doc(
                document_id=doc_jan,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="Я-91",
                ДатаДокумента="2026-01-25",
                ПозицияДокумента="0000000125",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1050000",
            )
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id=doc_jan,
                contract_id=da,
                amount_kopecks="1050000",
                advance_kopecks="1050000",
                repayment_kopecks="0",
                vat_base_kopecks="0",
                vat_kopecks="0",
                match_type="ADVANCE_EXTENDED_MATCH",
                confidence="HIGH",
                reason="PRE_VAT_ADVANCE;NO_CALCULATED_5_105",
                СодержаниеЗаписи="Получен аванс от Клиент А в счёт товара по накладной №Я-91 от 2026-01-25",
            )
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_jan,
                matched_amount_kopecks="1050000",
                match_type="ADVANCE_EXTENDED_MATCH",
                score="195",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="PRE_VAT_ADVANCE",
            )
        ],
        [],
        status(run_id),
    )


def build_2_6() -> None:
    run_id = "KUDIR_GOLDEN_2_6"
    doc_a = "DOC|0000000100"
    da = "D|00001|A"
    write_case(
        "unattributed_62_movement",
        q1_2026(run_id),
        [],
        [
            led(
                ledger_event_id="LED|0000000100|1|1",
                source_document_id=doc_a,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-20",
                ДатаДокумента="2026-01-10",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000120|1|1",
                source_document_id="",
                payment_id="",
                ДатаОперации="2026-01-20",
                ПозицияДокумента="0000000120",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Корректировка долга",
                НомерДокумента="Z-1",
                ДатаДокумента="2026-01-20",
                СуммаКоп="400000",
                СчетДебета="76",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СодержаниеПроводки="Зачёт",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-20",
                ДатаДокумента="2026-01-10",
                ПозицияДокумента="0000000100",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            )
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [],
        [],
        [],
        status(run_id),
        analysis_log=(
            "DIAGNOSTIC unattributed_62 remainder_mismatch "
            "analytics=C|00001+62.1+D|00001|A+V|00001 "
            "signed_balance_kop=600000 document_remaining_sum_kop=1000000\n"
        ),
    )


def build_2_7() -> None:
    run_id = "KUDIR_GOLDEN_2_7"
    p1 = "BANK|0000000400|2"
    da = "D|00001|A"
    write_case(
        "success_tax_not_ready",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-12",
                ПозицияДокумента="0000000400",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0020",
                ДатаДокументаОплаты="2026-01-12",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000400|2|1",
                source_document_id="BANK|0000000400",
                payment_id=p1,
                ДатаОперации="2026-01-12",
                ПозицияДокумента="0000000400",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0020",
                ДатаДокумента="2026-01-12",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01", ОстатокДтКоп="1000000")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="DEBT_UNRESOLVED",
                matched_document_id="",
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="DEBT_UNRESOLVED",
                confidence="LOW",
                reason="NO_DOCUMENT_IN_HISTORY",
                СодержаниеЗаписи="Оплата задолженности от Клиент А по договору A",
            )
        ],
        [],
        [{"payment_id": p1, "amount_kopecks": "1000000", "reason": "NO_DOCUMENT_IN_HISTORY"}],
        status(run_id, tax_ready="0", unresolved_debt="1000000", unresolved_count="1"),
    )


def build_2_8() -> None:
    run_id = "KUDIR_GOLDEN_2_8"
    p1 = "BANK|0000000100|2"
    doc_a = "DOC|0000000200"
    doc_b = "DOC|0000000210"
    da = "D|00001|A"
    write_case(
        "ambiguous_equal_shipments",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0030",
                ДатаДокументаОплаты="2026-01-10",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000100|2|1",
                source_document_id="BANK|0000000100",
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0030",
                ДатаДокумента="2026-01-10",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000200|1|1",
                source_document_id=doc_a,
                ДатаОперации="2026-02-10",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="X-1",
                ДатаДокумента="2026-02-10",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000210|1|1",
                source_document_id=doc_b,
                ДатаОперации="2026-02-12",
                ПозицияДокумента="0000000210",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="X-2",
                ДатаДокумента="2026-02-12",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="X-1",
                ДатаДокумента="2026-02-10",
                ПозицияДокумента="0000000200",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
            doc(
                document_id=doc_b,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="X-2",
                ДатаДокумента="2026-02-12",
                ПозицияДокумента="0000000210",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id="",
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="1000000",
                repayment_kopecks="0",
                match_type="ADVANCE_OPEN",
                confidence="LOW",
                reason="AMBIGUOUS_EQUAL_SHIPMENTS_NO_HIGH",
                СодержаниеЗаписи="Получен аванс от Клиент А по договору A",
            )
        ],
        [],
        [],
        status(run_id),
    )


def build_2_9() -> None:
    run_id = "KUDIR_GOLDEN_2_9"
    stmt = "0000000500"
    p4 = f"BANK|{stmt}|4"
    p6 = f"BANK|{stmt}|6"
    da, db = "D|00001|A", "D|00001|B"
    doc_a = "DOC|0000000160"
    write_case(
        "bank_multi_correspondence",
        q1_2026(run_id),
        [
            pay(
                payment_id=p4,
                ДатаОперации="2026-01-18",
                ПозицияДокумента=stmt,
                НомерПроводкиВДокументе="4",
                НомерДокументаОплаты="0001",
                ДатаДокументаОплаты="2026-01-18",
                СуммаКоп="1000000",
                ДоговорID="",
                Договор="",
            ),
            pay(
                payment_id=p6,
                ДатаОперации="2026-01-18",
                ПозицияДокумента=stmt,
                НомерПроводкиВДокументе="6",
                НомерДокументаОплаты="0001",
                ДатаДокументаОплаты="2026-01-18",
                СуммаКоп="200000",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
            ),
        ],
        [
            led(
                ledger_event_id="LED|0000000160|1|1",
                source_document_id=doc_a,
                ДатаОперации="2025-12-20",
                ПозицияДокумента="0000000160",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-40",
                ДатаДокумента="2025-12-20",
                СуммаКоп="700000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id=f"LED|{stmt}|4|1",
                source_document_id=f"BANK|{stmt}",
                payment_id=p4,
                ДатаОперации="2026-01-18",
                ПозицияДокумента=stmt,
                НомерПроводкиВДокументе="4",
                ВидДокумента="Выписка",
                НомерДокумента="0001",
                ДатаДокумента="2026-01-18",
                СуммаКоп="700000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id=f"LED|{stmt}|4|2",
                source_document_id=f"BANK|{stmt}",
                payment_id=p4,
                ДатаОперации="2026-01-18",
                ПозицияДокумента=stmt,
                НомерПроводкиВДокументе="4",
                ВидДокумента="Выписка",
                НомерДокумента="0001",
                ДатаДокумента="2026-01-18",
                СуммаКоп="300000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
            ),
            led(
                ledger_event_id=f"LED|{stmt}|6|1",
                source_document_id=f"BANK|{stmt}",
                payment_id=p6,
                ДатаОперации="2026-01-18",
                ПозицияДокумента=stmt,
                НомерПроводкиВДокументе="6",
                ВидДокумента="Выписка",
                НомерДокумента="0001",
                ДатаДокумента="2026-01-18",
                СуммаКоп="200000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-40",
                ДатаДокумента="2025-12-20",
                ПозицияДокумента="0000000160",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="700000",
            )
        ],
        [
            opening(ДоговорID=da, ДатаСальдо="2025-10-01"),
            opening(ДоговорID=db, ДатаСальдо="2025-10-01"),
        ],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p4,
                row_type="DEBT",
                matched_document_id=doc_a,
                contract_id=da,
                amount_kopecks="700000",
                advance_kopecks="0",
                repayment_kopecks="700000",
                match_type="EXACT_REMAINDER",
                confidence="HIGH",
                reason="POSTING_4_CORR_1",
                СодержаниеЗаписи="Оплата от Клиент А за товар по накладной №A-40 от 2025-12-20",
            ),
            result(
                run_id=run_id,
                result_row_id="R00002",
                payment_id=p4,
                row_type="ADVANCE",
                matched_document_id="",
                contract_id=db,
                amount_kopecks="300000",
                advance_kopecks="300000",
                repayment_kopecks="0",
                match_type="ADVANCE_OPEN",
                confidence="HIGH",
                reason="POSTING_4_CORR_2",
                СодержаниеЗаписи="Получен аванс от Клиент А по договору B",
            ),
            result(
                run_id=run_id,
                result_row_id="R00003",
                payment_id=p6,
                row_type="ADVANCE",
                matched_document_id="",
                contract_id=db,
                amount_kopecks="200000",
                advance_kopecks="200000",
                repayment_kopecks="0",
                match_type="ADVANCE_OPEN",
                confidence="HIGH",
                reason="POSTING_6_SEPARATE_PAYMENT",
                СодержаниеЗаписи="Получен аванс от Клиент А по договору B",
            ),
        ],
        [
            match(
                payment_id=p4,
                document_id=doc_a,
                matched_amount_kopecks="700000",
                match_type="EXACT_REMAINDER",
                score="195",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            )
        ],
        [],
        status(run_id),
    )


def build_4_5_partial_advance() -> None:
    """ТЗ: аванс 105 000 → реализации 42 000 и 21 000 → остаток 42 000."""
    run_id = "KUDIR_GOLDEN_4_5_PARTIAL"
    p1 = "BANK|0000000100|2"
    doc_a = "DOC|0000000200"
    doc_b = "DOC|0000000210"
    da = "D|00001|A"
    write_case(
        "partial_advance_split",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0105",
                ДатаДокументаОплаты="2026-01-10",
                СуммаКоп="10500000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000100|2|1",
                source_document_id="BANK|0000000100",
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0105",
                ДатаДокумента="2026-01-10",
                СуммаКоп="10500000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000200|1|1",
                source_document_id=doc_a,
                ДатаОперации="2026-02-10",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-42",
                ДатаДокумента="2026-02-10",
                СуммаКоп="4200000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000210|1|1",
                source_document_id=doc_b,
                ДатаОперации="2026-02-20",
                ПозицияДокумента="0000000210",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-21",
                ДатаДокумента="2026-02-20",
                СуммаКоп="2100000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-42",
                ДатаДокумента="2026-02-10",
                ПозицияДокумента="0000000200",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="4200000",
            ),
            doc(
                document_id=doc_b,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-21",
                ДатаДокумента="2026-02-20",
                ПозицияДокумента="0000000210",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="2100000",
            ),
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id=doc_a,
                contract_id=da,
                amount_kopecks="4200000",
                advance_kopecks="4200000",
                repayment_kopecks="0",
                match_type="ADVANCE_TAX_WINDOW_AMOUNT",
                confidence="HIGH",
                reason="ADVANCE_TAX_WINDOW",
                СодержаниеЗаписи="Получен аванс от Клиент А в счёт товара по накладной №A-42 от 2026-02-10",
            ),
            result(
                run_id=run_id,
                result_row_id="R00002",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id=doc_b,
                contract_id=da,
                amount_kopecks="2100000",
                advance_kopecks="2100000",
                repayment_kopecks="0",
                match_type="ADVANCE_TAX_WINDOW_AMOUNT",
                confidence="HIGH",
                reason="ADVANCE_TAX_WINDOW",
                СодержаниеЗаписи="Получен аванс от Клиент А в счёт товара по накладной №A-21 от 2026-02-20",
            ),
            result(
                run_id=run_id,
                result_row_id="R00003",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id="",
                contract_id=da,
                amount_kopecks="4200000",
                advance_kopecks="4200000",
                repayment_kopecks="0",
                match_type="ADVANCE_OPEN",
                confidence="HIGH",
                reason="ADVANCE_OPEN",
                СодержаниеЗаписи="Получен аванс от Клиент А по договору A",
            ),
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_a,
                matched_amount_kopecks="4200000",
                match_type="ADVANCE_TAX_WINDOW_AMOUNT",
                score="165",
                amount_score="90",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            ),
            match(
                payment_id=p1,
                document_id=doc_b,
                matched_amount_kopecks="2100000",
                match_type="ADVANCE_TAX_WINDOW_AMOUNT",
                score="165",
                amount_score="90",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            ),
        ],
        [],
        status(run_id),
    )


def build_4_5_same_analytics_correspondences() -> None:
    """Две корреспонденции одной аналитики: долг 10 000, части 6 000+6 000 → DEBT 10 000 + ADVANCE 2 000."""
    run_id = "KUDIR_GOLDEN_4_5_SAME_AN"
    p1 = "BANK|0000000300|4"
    da = "D|00001|A"
    write_case(
        "same_analytics_two_correspondences",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-18",
                ПозицияДокумента="0000000300",
                НомерПроводкиВДокументе="4",
                НомерДокументаОплаты="0060",
                ДатаДокументаОплаты="2026-01-18",
                СуммаКоп="1200000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000300|4|1",
                source_document_id="BANK|0000000300",
                payment_id=p1,
                ДатаОперации="2026-01-18",
                ПозицияДокумента="0000000300",
                НомерПроводкиВДокументе="4",
                НомерСтрокиВДокументе="1",
                ВидДокумента="Выписка",
                НомерДокумента="0060",
                ДатаДокумента="2026-01-18",
                СуммаКоп="600000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000300|4|2",
                source_document_id="BANK|0000000300",
                payment_id=p1,
                ДатаОперации="2026-01-18",
                ПозицияДокумента="0000000300",
                НомерПроводкиВДокументе="4",
                НомерСтрокиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0060",
                ДатаДокумента="2026-01-18",
                СуммаКоп="600000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01", ОстатокДтКоп="1000000")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="DEBT_UNRESOLVED",
                matched_document_id="",
                contract_id=da,
                amount_kopecks="600000",
                advance_kopecks="0",
                repayment_kopecks="600000",
                match_type="DEBT_UNRESOLVED",
                confidence="LOW",
                reason="NO_DOCUMENT_IN_HISTORY",
                СодержаниеЗаписи="Оплата задолженности от Клиент А по договору A",
            ),
            result(
                run_id=run_id,
                result_row_id="R00002",
                payment_id=p1,
                row_type="DEBT_UNRESOLVED",
                matched_document_id="",
                contract_id=da,
                amount_kopecks="400000",
                advance_kopecks="0",
                repayment_kopecks="400000",
                match_type="DEBT_UNRESOLVED",
                confidence="LOW",
                reason="NO_DOCUMENT_IN_HISTORY",
                СодержаниеЗаписи="Оплата задолженности от Клиент А по договору A",
            ),
            result(
                run_id=run_id,
                result_row_id="R00003",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id="",
                contract_id=da,
                amount_kopecks="200000",
                advance_kopecks="200000",
                repayment_kopecks="0",
                match_type="ADVANCE_OPEN",
                confidence="HIGH",
                reason="ANALYTICS_A_NO_DEBT",
                СодержаниеЗаписи="Получен аванс от Клиент А по договору A",
            ),
        ],
        [],
        [
            {"payment_id": p1, "amount_kopecks": "600000", "reason": "NO_DOCUMENT_IN_HISTORY"},
            {"payment_id": p1, "amount_kopecks": "400000", "reason": "NO_DOCUMENT_IN_HISTORY"},
        ],
        status(run_id, tax_ready="0", unresolved_debt="1000000", unresolved_count="2"),
    )


def build_4_5_posting_numeric_order() -> None:
    """Номер проводки 2, затем 10: отгрузка создаёт долг до оплаты. Строковая сортировка дала бы 10 раньше 2."""
    run_id = "KUDIR_GOLDEN_4_5_POSTING"
    p1 = "BANK|0000000100|10"
    doc_a = "DOC|0000000100"
    da = "D|00001|A"
    pos = "0000000100"
    write_case(
        "posting_numeric_order",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-15",
                ПозицияДокумента=pos,
                НомерПроводкиВДокументе="10",
                НомерДокументаОплаты="0010",
                ДатаДокументаОплаты="2026-01-15",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000100|2|1",
                source_document_id=doc_a,
                ДатаОперации="2026-01-15",
                ПозицияДокумента=pos,
                НомерПроводкиВДокументе="2",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-10",
                ДатаДокумента="2026-01-15",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000100|10|1",
                source_document_id="BANK|0000000100",
                payment_id=p1,
                ДатаОперации="2026-01-15",
                ПозицияДокумента=pos,
                НомерПроводкиВДокументе="10",
                ВидДокумента="Выписка",
                НомерДокумента="0010",
                ДатаДокумента="2026-01-15",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-10",
                ДатаДокумента="2026-01-15",
                ПозицияДокумента=pos,
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            )
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="DEBT",
                matched_document_id=doc_a,
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                confidence="HIGH",
                reason="EXACT_REMAINDER",
                СодержаниеЗаписи="Оплата от Клиент А за товар по накладной №A-10 от 2026-01-15",
            )
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_a,
                matched_amount_kopecks="1000000",
                match_type="EXACT_REMAINDER",
                score="195",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            )
        ],
        [],
        status(run_id),
    )


def build_4_6_direct_ref_act_not_goods() -> None:
    """АКТ №25 не даёт HIGH на отгрузку №25: тип и дата обязательны."""
    run_id = "KUDIR_GOLDEN_4_6_2"
    goods = "DOC|0000000025"
    decoy = "DOC|0000000099"
    p1 = "BANK|0000000600|2"
    da = "D|00001|A"
    write_case(
        "direct_ref_act_not_goods",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-02-01",
                ПозицияДокумента="0000000600",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0025",
                ДатаДокументаОплаты="2026-02-01",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                НазначениеПлатежа="Оплата по акту №25 от 10.08.2026",
            )
        ],
        [
            led(
                ledger_event_id="LED|0000000215|1|1",
                source_document_id=goods,
                ДатаОперации="2026-01-15",
                ПозицияДокумента="0000000215",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="25",
                ДатаДокумента="2026-01-15",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000220|1|1",
                source_document_id=decoy,
                ДатаОперации="2026-01-20",
                ПозицияДокумента="0000000220",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="99",
                ДатаДокумента="2026-01-20",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000600|2|1",
                source_document_id="BANK|0000000600",
                payment_id=p1,
                ДатаОперации="2026-02-01",
                ПозицияДокумента="0000000600",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0025",
                ДатаДокумента="2026-02-01",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            doc(
                document_id=goods,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="25",
                ДатаДокумента="2026-01-15",
                ПозицияДокумента="0000000215",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
            doc(
                document_id=decoy,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="99",
                ДатаДокумента="2026-01-20",
                ПозицияДокумента="0000000220",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="DEBT_UNRESOLVED",
                matched_document_id="",
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="DEBT_UNRESOLVED",
                confidence="LOW",
                reason="NO_DOCUMENT_IN_HISTORY",
                СодержаниеЗаписи="Оплата задолженности от Клиент А по договору A",
            )
        ],
        [],
        [{"payment_id": p1, "amount_kopecks": "1000000", "reason": "NO_DOCUMENT_IN_HISTORY"}],
        status(run_id, tax_ready="0", unresolved_debt="1000000", unresolved_count="1"),
    )


def build_4_6_advance_other_contract() -> None:
    """HIGH-аванс закрывается сразу при другом договоре реализации; мартовская оплата не забирает remainder."""
    run_id = "KUDIR_GOLDEN_4_6_3"
    inv = "DOC|0000000001"
    doc_b = "DOC|0000000002"
    p1 = "BANK|0000000100|2"
    p2 = "BANK|0000000300|2"
    da = "D|00001|A"
    db = "D|00001|B"
    write_case(
        "advance_other_contract_then_later_payment",
        q1_2026(run_id),
        [
            pay(
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0001",
                ДатаДокументаОплаты="2026-01-10",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                НазначениеПлатежа="Оплата по счету №1",
            ),
            pay(
                payment_id=p2,
                ДатаОперации="2026-03-01",
                ПозицияДокумента="0000000300",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0002",
                ДатаДокументаОплаты="2026-03-01",
                СуммаКоп="1000000",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
            ),
        ],
        [
            led(
                ledger_event_id="LED|0000000100|2|1",
                source_document_id="BANK|0000000100",
                payment_id=p1,
                ДатаОперации="2026-01-10",
                ПозицияДокумента="0000000100",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0001",
                ДатаДокумента="2026-01-10",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000200|1|1",
                source_document_id=doc_b,
                ДатаОперации="2026-02-10",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="1",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="B-1",
                ДатаДокумента="2026-02-10",
                СуммаКоп="1000000",
                СчетДебета="62.1",
                СчетКредита="90",
                Сторона62="D",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
            ),
            led(
                ledger_event_id="LED|0000000300|2|1",
                source_document_id="BANK|0000000300",
                payment_id=p2,
                ДатаОперации="2026-03-01",
                ПозицияДокумента="0000000300",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0002",
                ДатаДокумента="2026-03-01",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
            ),
        ],
        [
            doc(
                document_id=inv,
                DocumentRole="INVOICE",
                DocumentType="INVOICE",
                ВидДокумента="Счет на оплату",
                НомерДокумента="1",
                ДатаДокумента="2026-01-09",
                ПозицияДокумента="0000000001",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
            doc(
                document_id=doc_b,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="B-1",
                ДатаДокумента="2026-02-10",
                ПозицияДокумента="0000000200",
                ДоговорID=db,
                Договор="B",
                НомерДоговора="B",
                СуммаКоп="1000000",
                Комментарий="Введен на основании: Счет на оплату №1 от 09.01.2026",
            ),
        ],
        [
            opening(ДоговорID=da, ДатаСальдо="2025-10-01"),
            opening(ДоговорID=db, ДатаСальдо="2025-10-01"),
        ],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p1,
                row_type="ADVANCE",
                matched_document_id=doc_b,
                contract_id=db,
                amount_kopecks="1000000",
                advance_kopecks="1000000",
                repayment_kopecks="0",
                match_type="ADVANCE_INVOICE_BASE",
                confidence="HIGH",
                reason="HIGH_INVOICE_CHAIN",
                СодержаниеЗаписи="Получен аванс от Клиент А в счёт товара по накладной №B-1 от 2026-02-10",
            ),
            result(
                run_id=run_id,
                result_row_id="R00002",
                payment_id=p2,
                row_type="DEBT_UNRESOLVED",
                matched_document_id="",
                contract_id=db,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="DEBT_UNRESOLVED",
                confidence="LOW",
                reason="NO_DOCUMENT_IN_HISTORY",
                СодержаниеЗаписи="Оплата задолженности от Клиент А по договору B",
            ),
        ],
        [
            match(
                payment_id=p1,
                document_id=doc_b,
                matched_amount_kopecks="1000000",
                match_type="ADVANCE_INVOICE_BASE",
                score="315",
                invoice_link_score="180",
                amount_score="120",
                contract_score="-20",
                calculation_type_score="35",
                reason="INVOICE_BASE_EXACT+EXACT_REMAINDER",
            )
        ],
        [{"payment_id": p2, "amount_kopecks": "1000000", "reason": "NO_DOCUMENT_IN_HISTORY"}],
        status(run_id, tax_ready="0", unresolved_debt="1000000", unresolved_count="1"),
    )


def build_4_6_historical_unresolved() -> None:
    """Историческая неоднозначность на той же аналитике → state_uncertain, tax_ready=0."""
    run_id = "KUDIR_GOLDEN_4_6_7"
    doc_a = "DOC|0000000002"
    doc_b = "DOC|0000000003"
    p_hist = "BANK|0000000050|2"
    p_tgt = "BANK|0000000200|2"
    da = "D|00001|A"
    write_case(
        "historical_unresolved_blocks_tax",
        q1_2026(run_id),
        [
            pay(
                payment_id=p_hist,
                is_target="0",
                ДатаОперации="2025-12-15",
                ПозицияДокумента="0000000050",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0090",
                ДатаДокументаОплаты="2025-12-15",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            pay(
                payment_id=p_tgt,
                ДатаОперации="2026-01-20",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="2",
                НомерДокументаОплаты="0002",
                ДатаДокументаОплаты="2026-01-20",
                СуммаКоп="1000000",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                НазначениеПлатежа="Оплата по накладной №A-1",
            ),
        ],
        [
            led(
                ledger_event_id="LED|0000000050|2|1",
                source_document_id="BANK|0000000050",
                payment_id=p_hist,
                ДатаОперации="2025-12-15",
                ПозицияДокумента="0000000050",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0090",
                ДатаДокумента="2025-12-15",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
            led(
                ledger_event_id="LED|0000000200|2|1",
                source_document_id="BANK|0000000200",
                payment_id=p_tgt,
                ДатаОперации="2026-01-20",
                ПозицияДокумента="0000000200",
                НомерПроводкиВДокументе="2",
                ВидДокумента="Выписка",
                НомерДокумента="0002",
                ДатаДокумента="2026-01-20",
                СуммаКоп="1000000",
                СчетДебета="51",
                СчетКредита="62.1",
                Сторона62="K",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
            ),
        ],
        [
            doc(
                document_id=doc_a,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="A-1",
                ДатаДокумента="2025-11-01",
                ПозицияДокумента="0000000002",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
            doc(
                document_id=doc_b,
                DocumentRole="RECEIVABLE",
                DocumentType="GOODS_SHIPMENT",
                ВидДокумента="Отгрузка товаров",
                НомерДокумента="B-1",
                ДатаДокумента="2025-11-15",
                ПозицияДокумента="0000000003",
                ДоговорID=da,
                Договор="A",
                НомерДоговора="A",
                СуммаКоп="1000000",
            ),
        ],
        [opening(ДоговорID=da, ДатаСальдо="2025-10-01", ОстатокДтКоп="2000000")],
        [
            result(
                run_id=run_id,
                result_row_id="R00001",
                payment_id=p_tgt,
                row_type="DEBT",
                matched_document_id=doc_a,
                contract_id=da,
                amount_kopecks="1000000",
                advance_kopecks="0",
                repayment_kopecks="1000000",
                match_type="DIRECT_DOCUMENT_EXACT",
                confidence="HIGH",
                reason="EXACT_REMAINDER",
                СодержаниеЗаписи="Оплата от Клиент А за товар по накладной №A-1 от 2025-11-01",
            )
        ],
        [
            match(
                payment_id=p_tgt,
                document_id=doc_a,
                matched_amount_kopecks="1000000",
                match_type="DIRECT_DOCUMENT_EXACT",
                score="395",
                direct_reference_score="200",
                amount_score="120",
                contract_score="40",
                calculation_type_score="35",
                reason="EXACT_REMAINDER",
            )
        ],
        [],
        status(
            run_id,
            tax_ready="0",
            state_uncertain="1",
            historical_unresolved_count="1",
            historical_unresolved_kopecks="1000000",
        ),
    )


def main() -> None:
    build_2_1()
    build_2_2()
    build_2_3()
    build_2_4()
    build_2_5()
    build_2_6()
    build_2_7()
    build_2_8()
    build_2_9()
    build_4_5_partial_advance()
    build_4_5_same_analytics_correspondences()
    build_4_5_posting_numeric_order()
    build_4_6_direct_ref_act_not_goods()
    build_4_6_advance_other_contract()
    build_4_6_historical_unresolved()
    print("golden 2.1–2.9, 4.5 and 4.6 written")


if __name__ == "__main__":
    main()
