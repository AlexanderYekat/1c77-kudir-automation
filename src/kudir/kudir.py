"""Текст СодержаниеЗаписи и каркас строки kudir_result. НДС цикла 1 = 0."""

from __future__ import annotations

from kudir_proto.csv_io import sanitize_kudir_content


def is_service(doc: dict[str, str] | None) -> bool:
    if not doc:
        return False
    kind = ((doc.get("DocumentType") or "") + " " + (doc.get("ВидДокумента") or "")).lower()
    return "service" in kind or "услуг" in kind or "акт" in kind


def content_line(
    row_type: str,
    counterparty_name: str,
    doc: dict[str, str] | None,
    contract_name: str,
) -> str:
    name = (counterparty_name or "").strip() or "контрагент"
    if row_type == "ADVANCE" and doc:
        num = (doc.get("НомерДокумента") or "").strip()
        dt = (doc.get("ДатаДокумента") or "").strip()
        if is_service(doc):
            base = f"Получен аванс от {name} в счёт услуг"
            if num:
                base += f" по акту №{num}"
        else:
            base = f"Получен аванс от {name} в счёт товара"
            if num:
                base += f" по накладной №{num}"
        if dt:
            base += f" от {dt}"
        return sanitize_kudir_content(base)
    if row_type == "ADVANCE":
        if contract_name:
            return sanitize_kudir_content(f"Получен аванс от {name} по договору {contract_name}")
        return sanitize_kudir_content(f"Получен аванс от {name}")
    if row_type in ("DEBT",) and doc:
        num = (doc.get("НомерДокумента") or "").strip()
        dt = (doc.get("ДатаДокумента") or "").strip()
        if is_service(doc):
            base = f"Оплата от {name} за услуги"
            if num:
                base += f" по акту №{num}"
        else:
            base = f"Оплата от {name} за товар"
            if num:
                base += f" по накладной №{num}"
        if dt:
            base += f" от {dt}"
        return sanitize_kudir_content(base)
    if contract_name:
        return sanitize_kudir_content(f"Оплата задолженности от {name} по договору {contract_name}")
    return sanitize_kudir_content(f"Оплата задолженности от {name}")


def result_row(
    *,
    run_id: str,
    result_row_id: str,
    payment_id: str,
    row_type: str,
    matched: dict[str, str] | None,
    contract_id: str,
    amount: int,
    match_type: str,
    confidence: str,
    reason: str,
    counterparty_name: str,
    contract_name: str,
) -> dict[str, str]:
    is_advance = row_type == "ADVANCE"
    return {
        "run_id": run_id,
        "result_row_id": result_row_id,
        "payment_id": payment_id,
        "row_type": row_type,
        "matched_document_id": (matched or {}).get("document_id", "") if matched else "",
        "contract_id": contract_id,
        "amount_kopecks": str(amount),
        "advance_kopecks": str(amount if is_advance else 0),
        "repayment_kopecks": str(0 if is_advance else amount),
        "income_kopecks": str(amount),
        "vat_base_kopecks": "0",
        "vat_kopecks": "0",
        "match_type": match_type,
        "confidence": confidence,
        "reason": reason,
        "СодержаниеЗаписи": content_line(row_type, counterparty_name, matched, contract_name),
    }
