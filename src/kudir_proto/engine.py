"""Заглушка matching: пробрасывает ID без бухгалтерской эвристики."""

from __future__ import annotations

from pathlib import Path

from kudir_proto.csv_io import (
    DOCUMENTS_FIELDS,
    KUDIR_RESULT_FIELDS,
    LEDGER_FIELDS,
    MANIFEST_KEYS,
    MATCHES_FIELDS,
    OPENING_FIELDS,
    PAYMENTS_FIELDS,
    RUN_STATUS_KEYS,
    SCHEMA_VERSION,
    UNRESOLVED_FIELDS,
    file_sha256,
    read_kv,
    read_rows,
    sanitize_kudir_content,
    write_kv,
    write_rows,
)


class ProtoError(Exception):
    """Ошибка прототипа, должна дать FAILED без kudir_result.csv."""


def _content(row: dict[str, str], matched: dict[str, str] | None) -> str:
    name = (row.get("Контрагент") or "").strip()
    if not name:
        name = "контрагент"
    if matched:
        kind = (matched.get("DocumentType") or matched.get("ВидДокумента") or "").upper()
        num = (matched.get("НомерДокумента") or "").strip()
        dt = (matched.get("ДатаДокумента") or "").strip()
        if "SERVICE" in kind or "услуг" in (matched.get("ВидДокумента") or "").lower():
            base = f"Оплата от {name} за услуги"
            if num:
                base += f" по акту №{num}"
            if dt:
                base += f" от {dt}"
            return base
        base = f"Оплата от {name} за товар"
        if num:
            base += f" по накладной №{num}"
        if dt:
            base += f" от {dt}"
        return base
    contract = (row.get("Договор") or row.get("НомерДоговора") or "").strip()
    if contract:
        return f"Оплата задолженности от {name} по договору {contract}"
    return f"Оплата задолженности от {name}"


def stub_match(
    payments: list[dict[str, str]],
    documents: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Каждому целевому платежу — одна строка. Документ берётся первый того же контрагента."""
    by_counterparty: dict[str, list[dict[str, str]]] = {}
    for doc in documents:
        role = (doc.get("DocumentRole") or "").strip().upper()
        if role == "INVOICE":
            continue
        cid = doc.get("КонтрагентID") or ""
        if cid:
            by_counterparty.setdefault(cid, []).append(doc)

    results: list[dict[str, str]] = []
    matches: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    n = 0
    for pay in payments:
        if (pay.get("is_target") or "").strip() != "1":
            continue
        n += 1
        amount = pay.get("СуммаКоп") or "0"
        cid = pay.get("КонтрагентID") or ""
        candidates = by_counterparty.get(cid) or []
        matched = candidates[0] if candidates else None
        doc_id = (matched or {}).get("document_id", "") if matched else ""
        row_type = "DEBT" if doc_id else "DEBT_UNRESOLVED"
        result_id = f"R{n:05d}"
        row = {
            "result_row_id": result_id,
            "payment_id": pay.get("payment_id") or "",
            "row_type": row_type,
            "matched_document_id": doc_id,
            "contract_id": pay.get("ДоговорID") or "",
            "amount_kopecks": amount,
            "advance_kopecks": "0",
            "repayment_kopecks": amount,
            "income_kopecks": amount,
            "vat_base_kopecks": "0",
            "vat_kopecks": "0",
            "match_type": "PROTO_PASSTHROUGH",
            "confidence": "LOW",
            "reason": "PROTO_STUB",
            "СодержаниеЗаписи": sanitize_kudir_content(_content(pay, matched)),
        }
        results.append(row)
        if doc_id:
            matches.append(
                {
                    "payment_id": row["payment_id"],
                    "document_id": doc_id,
                    "matched_amount_kopecks": amount,
                    "match_type": "PROTO_PASSTHROUGH",
                    "score": "0",
                    "confidence": "LOW",
                    "direct_reference_score": "0",
                    "invoice_link_score": "0",
                    "amount_score": "0",
                    "contract_score": "0",
                    "calculation_type_score": "0",
                    "semantic_score": "0",
                    "chronology_score": "0",
                    "reason": "PROTO_STUB_FIRST_SAME_COUNTERPARTY",
                }
            )
        else:
            unresolved.append(
                {
                    "payment_id": row["payment_id"],
                    "amount_kopecks": amount,
                    "reason": "PROTO_NO_DOCUMENT_FOR_COUNTERPARTY",
                }
            )
    return results, matches, unresolved


def run_directory(exchange_dir: Path, scoring_path: Path) -> str:
    exchange_dir = exchange_dir.resolve()
    if not scoring_path.is_file():
        raise ProtoError(f"нет config/scoring.yaml: {scoring_path}")

    manifest_path = exchange_dir / "manifest.csv"
    if not manifest_path.is_file():
        raise ProtoError("нет manifest.csv")
    manifest = read_kv(manifest_path)
    schema = (manifest.get("schema_version") or "").strip()
    if schema != SCHEMA_VERSION:
        raise ProtoError(f"неизвестная schema_version={schema!r}, ожидается {SCHEMA_VERSION}")
    run_id = (manifest.get("run_id") or "").strip()
    if not run_id:
        raise ProtoError("пустой run_id в manifest.csv")
    missing = [k for k in MANIFEST_KEYS if not (manifest.get(k) or "").strip()]
    if missing:
        raise ProtoError("в manifest.csv нет обязательных ключей: " + ", ".join(missing))

    payments = read_rows(exchange_dir / "payments.csv", PAYMENTS_FIELDS)
    documents = read_rows(exchange_dir / "documents.csv", DOCUMENTS_FIELDS)
    read_rows(exchange_dir / "ledger62.csv", LEDGER_FIELDS)
    read_rows(exchange_dir / "opening_balances.csv", OPENING_FIELDS)

    results, matches, unresolved = stub_match(payments, documents)
    for row in results:
        row["run_id"] = run_id

    write_rows(exchange_dir / "kudir_result.csv", KUDIR_RESULT_FIELDS, results)
    write_rows(exchange_dir / "matches.csv", MATCHES_FIELDS, matches)
    write_rows(exchange_dir / "unresolved.csv", UNRESOLVED_FIELDS, unresolved)
    unresolved_sum = sum(int(r.get("amount_kopecks") or "0") for r in unresolved)
    tax_ready = "0" if unresolved else "1"
    write_kv(
        exchange_dir / "run_status.csv",
        {
            "run_id": run_id,
            "status": "SUCCESS",
            "tax_ready": tax_ready,
            "unresolved_debt_kopecks": str(unresolved_sum),
            "unresolved_count": str(len(unresolved)),
            "parser_available": "0",
            "schema_version": SCHEMA_VERSION,
            "scoring_hash": file_sha256(scoring_path),
        },
        keys=RUN_STATUS_KEYS,
    )
    return "SUCCESS"
