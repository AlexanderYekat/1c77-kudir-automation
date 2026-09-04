"""Matcher цикла 1: разрез по аналитике, HIGH сразу, conflict pass для хвостов.

Не наследует kudir_proto.engine.stub_match. FIFO нет.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kudir.base_document_links import document_is_based_on_invoice, numbers_equal
from kudir.combinations import cap_candidates
from kudir.diagnostics import AnalysisLog
from kudir.document_reference_parser import TextRef, extract_text_refs
from kudir.kudir import content_line, result_row
from kudir.ledger import AnalyticsKey, Ledger, analytics_key, kop
from kudir.loaders import ExchangeBundle
from kudir.scoring import ScoreBreakdown, ScoringConfig, classify_confidence
from kudir.semantic import semantic_score

VAT_START = "2026-01-01"


class MatchError(Exception):
    pass


def quarter_end(iso_date: str) -> str:
    year_s, month_s, _ = iso_date.split("-")
    year, month = int(year_s), int(month_s)
    q = (month - 1) // 3
    last = ((3, 31), (6, 30), (9, 30), (12, 31))[q]
    return f"{year:04d}-{last[0]:02d}-{last[1]:02d}"


def sort_tuple(date: str, pos: str, posting: str = "") -> tuple[str, str, str]:
    return (date or "", pos or "", posting or "")


def is_invoice_role(row: dict[str, str]) -> bool:
    return (row.get("DocumentRole") or "").strip().upper() == "INVOICE"


def is_active_doc(row: dict[str, str]) -> bool:
    if (row.get("ПомеченНаУдаление") or "0").strip() == "1":
        return False
    if (row.get("Проведен") or "1").strip() == "0":
        return False
    return True


def exists_at(doc: dict[str, str], date: str, pos: str) -> bool:
    return sort_tuple(doc.get("ДатаДокумента") or "", doc.get("ПозицияДокумента") or "") <= sort_tuple(date, pos)


def after_payment(doc: dict[str, str], date: str, pos: str) -> bool:
    return sort_tuple(doc.get("ДатаДокумента") or "", doc.get("ПозицияДокумента") or "") > sort_tuple(date, pos)


@dataclass
class OpenAdvance:
    payment_id: str
    payment: dict[str, str]
    part: dict[str, str]
    analytics: AnalyticsKey
    amount: int
    refs: list[TextRef]
    result_index: int | None
    corr_index: int
    matched_doc_id: str = ""


@dataclass
class Engine:
    bundle: ExchangeBundle
    cfg: ScoringConfig
    log: AnalysisLog
    ledger: Ledger = field(default_factory=Ledger)
    results: list[dict[str, str]] = field(default_factory=list)
    matches: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    open_advances: list[OpenAdvance] = field(default_factory=list)
    payment_refs: dict[str, list[TextRef]] = field(default_factory=dict)
    _row_n: int = 0

    def run(self) -> None:
        self._index()
        for row in self.bundle.openings:
            self.ledger.apply_opening(row)
        for doc in self.bundle.documents:
            self.ledger.register_document(doc)
        processed: set[str] = set()
        events = sorted(self.bundle.ledger, key=lambda r: (
            r.get("ДатаОперации") or "",
            r.get("ПозицияДокумента") or "",
            r.get("НомерПроводкиВДокументе") or "",
            r.get("ledger_event_id") or "",
        ))
        for row in events:
            pid = (row.get("payment_id") or "").strip()
            if pid:
                if pid in processed:
                    continue
                processed.add(pid)
                self._process_payment(pid)
                continue
            self._process_nonpayment(row)
        self._conflict_pass()
        self._check_invariants()

    def _index(self) -> None:
        self.payments_by_id = {(p.get("payment_id") or "").strip(): p for p in self.bundle.payments}
        self.parts_by_payment: dict[str, list[dict[str, str]]] = {}
        for row in self.bundle.ledger:
            pid = (row.get("payment_id") or "").strip()
            if pid:
                self.parts_by_payment.setdefault(pid, []).append(row)
        for pid, parts in self.parts_by_payment.items():
            parts.sort(key=lambda r: (r.get("НомерСтрокиВДокументе") or "", r.get("ledger_event_id") or ""))
            pay = self.payments_by_id.get(pid, {})
            self.payment_refs[pid] = extract_text_refs(
                pay.get("НазначениеПлатежа") or "",
                pay.get("СодержаниеПроводки") or "",
                pay.get("Содержание") or "",
                pay.get("ПредставлениеПроводки") or "",
                pay.get("ПервичныйДокумент") or "",
                pay.get("КомментарийДокументаОплаты") or "",
            )
        self._stmt_payment_count: dict[str, int] = {}
        for p in self.bundle.payments:
            pos = p.get("ПозицияДокумента") or ""
            self._stmt_payment_count[pos] = self._stmt_payment_count.get(pos, 0) + 1

    def _next_id(self) -> str:
        self._row_n += 1
        return f"R{self._row_n:05d}"

    def _target(self, payment: dict[str, str]) -> bool:
        return (payment.get("is_target") or "").strip() == "1"

    def _process_nonpayment(self, row: dict[str, str]) -> None:
        source = (row.get("source_document_id") or "").strip()
        attributed = ""
        if source and source in self.ledger.documents and not is_invoice_role(self.ledger.documents[source].row):
            attributed = source
        if not attributed:
            mismatch = self.ledger.apply_unattributed(row)
            if mismatch is not None:
                self.log.remainder_mismatch(mismatch)
            return
        self.ledger.apply_ledger_row(row, attributed_doc_id=attributed)
        self._try_high_close(attributed)

    def _process_payment(self, pid: str) -> None:
        parts = self.parts_by_payment.get(pid) or []
        payment = self.payments_by_id.get(pid) or parts[0]
        befores: list[int] = []
        keys: list[AnalyticsKey] = []
        for part in parts:
            key = analytics_key(part)
            keys.append(key)
            befores.append(self.ledger.debt_before(key))
        foreign_credit = self._foreign_credit(payment, keys)
        for part in parts:
            self.ledger.apply_ledger_row(part, attributed_doc_id="")
        for i, part in enumerate(parts):
            amount = kop(part, "СуммаКоп")
            debt_part = min(amount, befores[i])
            advance_part = amount - debt_part
            if debt_part:
                self._match_debt(payment, part, keys[i], debt_part, i, foreign_credit)
            if advance_part:
                self._open_advance(payment, part, keys[i], advance_part, i)

    def _foreign_credit(self, payment: dict[str, str], keys: list[AnalyticsKey]) -> bool:
        cid = (payment.get("КонтрагентID") or keys[0][0] if keys else "").strip()
        pay_set = set(keys)
        for key, st in self.ledger.analytics.items():
            if key[0] != cid or key in pay_set:
                continue
            if st.signed_balance < 0:
                return True
        return False

    def _match_debt(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        key: AnalyticsKey,
        amount: int,
        corr_index: int,
        foreign_credit: bool,
    ) -> None:
        pid = (payment.get("payment_id") or part.get("payment_id") or "").strip()
        cands = self._debt_candidates(part, payment, amount)
        chosen = None
        bd = ScoreBreakdown()
        if cands:
            scored = [(d, self._score(payment, part, d.row, amount, debt=True)) for d in cands]
            scored.sort(key=lambda x: -x[1].total)
            unique_remainder = [d for d in cands if d.remaining_for_debt() == amount]
            unique_chain = [d for d in cands if self._invoice_chain(pid, d.row) or self._direct_ref(pid, d.row)]
            if len(unique_chain) == 1 and amount <= unique_chain[0].remaining_for_debt():
                chosen = unique_chain[0]
                bd = self._score(payment, part, chosen.row, amount, debt=True)
            elif len(unique_remainder) == 1 and len(cands) == 1:
                chosen = unique_remainder[0]
                bd = self._score(payment, part, chosen.row, amount, debt=True)
            elif len(scored) >= 1:
                best_bd = scored[0][1]
                second = scored[1][1].total if len(scored) > 1 else None
                if second == best_bd.total:
                    chosen = None
                elif classify_confidence(best_bd.total, second, self.cfg, best_bd) == "HIGH":
                    if len(scored) == 1 or (second is not None and best_bd.total > second):
                        chosen = scored[0][0]
                        bd = best_bd
        conf = classify_confidence(bd.total, 0, self.cfg, bd) if chosen else ""
        if chosen is not None and conf == "HIGH":
            take = min(amount, chosen.remaining_for_debt())
            self.ledger.consume_debt(chosen.document_id, take)
            reason = self._debt_reason(payment, part, key, corr_index, foreign_credit)
            match_type = "EXACT_REMAINDER"
            if bd.invoice_link and take == amount:
                match_type = "INVOICE_BASE_EXACT"
            if self._target(payment):
                self._emit_result(
                    payment, part, "DEBT", chosen.row, take, match_type, "HIGH", reason,
                )
                self._emit_match(pid, chosen.row, take, match_type, "HIGH", bd, self._match_reason(bd, match_type))
            return
        if self._target(payment):
            reason = "NO_DOCUMENT_IN_HISTORY"
            self._emit_result(payment, part, "DEBT_UNRESOLVED", None, amount, "DEBT_UNRESOLVED", "LOW", reason)
            self.unresolved.append({"payment_id": pid, "amount_kopecks": str(amount), "reason": reason})

    def _open_advance(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        key: AnalyticsKey,
        amount: int,
        corr_index: int,
    ) -> None:
        pid = (payment.get("payment_id") or "").strip()
        reason = self._advance_open_reason(payment, part, corr_index)
        idx = None
        if self._target(payment):
            self._emit_result(payment, part, "ADVANCE", None, amount, "ADVANCE_OPEN", "HIGH", reason)
            idx = len(self.results) - 1
        self.open_advances.append(
            OpenAdvance(
                payment_id=pid,
                payment=payment,
                part=part,
                analytics=key,
                amount=amount,
                refs=self.payment_refs.get(pid, []),
                result_index=idx,
                corr_index=corr_index,
            )
        )

    def _try_high_close(self, doc_id: str) -> None:
        doc_state = self.ledger.documents.get(doc_id)
        if doc_state is None or is_invoice_role(doc_state.row):
            return
        doc = doc_state.row
        cid = (doc.get("КонтрагентID") or "").strip()
        linked: list[OpenAdvance] = []
        for adv in self.open_advances:
            if adv.amount <= 0 or adv.matched_doc_id:
                continue
            if adv.analytics[0] != cid:
                continue
            doc_contract = (doc.get("ДоговорID") or "").strip()
            doc_vid = (doc.get("ВидРасчетовID") or "").strip()
            if adv.analytics[2] and doc_contract and adv.analytics[2] != doc_contract:
                continue
            if adv.analytics[3] and doc_vid and adv.analytics[3] != doc_vid:
                continue
            if self._invoice_chain(adv.payment_id, doc) or self._direct_ref(adv.payment_id, doc):
                linked.append(adv)
        if len(linked) != 1:
            return
        adv = linked[0]
        if not self._unique_invoice_target(adv, doc):
            return
        take = min(adv.amount, doc_state.available_for_advance())
        if take <= 0:
            return
        bd = self._score(adv.payment, adv.part, doc, take, debt=False)
        if classify_confidence(bd.total, 0, self.cfg, bd) != "HIGH":
            return
        self._close_advance(adv, doc_state, take, bd, immediate=True)

    def _unique_invoice_target(self, adv: OpenAdvance, doc: dict[str, str]) -> bool:
        """Цепочка уникальна среди уже существующих реализаций."""
        peers = 0
        for other in self.ledger.documents.values():
            row = other.row
            if is_invoice_role(row) or not is_active_doc(row):
                continue
            if (row.get("КонтрагентID") or "") != (doc.get("КонтрагентID") or ""):
                continue
            if not exists_at(row, doc.get("ДатаДокумента") or "", doc.get("ПозицияДокумента") or ""):
                continue
            if self._invoice_chain(adv.payment_id, row) or self._direct_ref(adv.payment_id, row):
                peers += 1
        return peers == 1

    def _conflict_pass(self) -> None:
        horizon = (self.bundle.manifest.get("match_horizon_end") or "").strip()
        for adv in self.open_advances:
            if adv.amount <= 0 or adv.matched_doc_id:
                continue
            pay_date = adv.payment.get("ДатаОперации") or adv.part.get("ДатаОперации") or ""
            pay_pos = adv.payment.get("ПозицияДокумента") or adv.part.get("ПозицияДокумента") or ""
            q_end = quarter_end(pay_date)
            scored: list[tuple[object, ScoreBreakdown, str]] = []
            cid = (adv.part.get("КонтрагентID") or "").strip()
            for doc_state in self.ledger.documents.values():
                doc = doc_state.row
                if is_invoice_role(doc) or not is_active_doc(doc):
                    continue
                if (doc.get("КонтрагентID") or "").strip() != cid:
                    continue
                if not after_payment(doc, pay_date, pay_pos):
                    continue
                doc_date = doc.get("ДатаДокумента") or ""
                if horizon and doc_date > horizon:
                    continue
                avail = doc_state.available_for_advance()
                if avail <= 0:
                    continue
                window = "TAX" if doc_date <= q_end else "EXTENDED"
                take = min(adv.amount, avail)
                bd = self._score(adv.payment, adv.part, doc, take, debt=False)
                scored.append((doc_state, bd, window))
            scored, capped = cap_candidates(scored, self.cfg)
            if capped:
                self.log.info(f"search cap payment_id={adv.payment_id}")
            if not scored:
                continue
            scored.sort(key=lambda x: -x[1].total)
            best_state, best_bd, window = scored[0]
            second = scored[1][1].total if len(scored) > 1 else None
            if second is not None and second == best_bd.total:
                self._mark_ambiguous(adv)
                continue
            conf = classify_confidence(best_bd.total, second, self.cfg, best_bd)
            if conf != "HIGH":
                if len(scored) > 1:
                    self._mark_ambiguous(adv)
                continue
            take = min(adv.amount, best_state.available_for_advance())
            self._close_advance(adv, best_state, take, best_bd, immediate=False, window=window)

    def _mark_ambiguous(self, adv: OpenAdvance) -> None:
        if adv.result_index is None:
            return
        row = self.results[adv.result_index]
        row["match_type"] = "ADVANCE_OPEN"
        row["confidence"] = "LOW"
        row["reason"] = "AMBIGUOUS_EQUAL_SHIPMENTS_NO_HIGH"
        row["matched_document_id"] = ""

    def _close_advance(
        self,
        adv: OpenAdvance,
        doc_state,
        amount: int,
        bd: ScoreBreakdown,
        *,
        immediate: bool,
        window: str = "",
    ) -> None:
        doc = doc_state.row
        pay_date = adv.payment.get("ДатаОперации") or adv.part.get("ДатаОперации") or ""
        q_end = quarter_end(pay_date)
        doc_date = doc.get("ДатаДокумента") or ""
        if not window:
            window = "TAX" if doc_date <= q_end else "EXTENDED"
        pre_vat = pay_date < VAT_START
        if window == "EXTENDED":
            match_type = "ADVANCE_EXTENDED"
            if bd.invoice_link or immediate:
                result_reason = "EXTENDED_MATCH_NO_TAX_RECALC_Q4"
            elif pre_vat:
                result_reason = "PRE_VAT_ADVANCE;NO_CALCULATED_5_105"
            else:
                result_reason = "EXTENDED_MATCH_NO_TAX_RECALC_Q4"
        else:
            match_type = "ADVANCE_INVOICE_BASE" if bd.invoice_link else "ADVANCE_TAX_WINDOW_AMOUNT"
            result_reason = "HIGH_INVOICE_CHAIN" if bd.invoice_link else "ADVANCE_TAX_WINDOW"
        self.ledger.consume_advance(doc_state.document_id, amount)
        self.ledger.state(adv.analytics).high_advance_closed = True
        adv.amount -= amount
        adv.matched_doc_id = doc_state.document_id
        if adv.result_index is not None:
            row = self.results[adv.result_index]
            name = adv.payment.get("Контрагент") or adv.part.get("Контрагент") or ""
            contract_name = (doc.get("Договор") or doc.get("НомерДоговора") or "").strip()
            row["matched_document_id"] = doc_state.document_id
            row["contract_id"] = (doc.get("ДоговорID") or "").strip()
            row["match_type"] = match_type
            row["confidence"] = "HIGH"
            row["reason"] = result_reason
            row["СодержаниеЗаписи"] = content_line("ADVANCE", name, doc, contract_name)
        self._emit_match(
            adv.payment_id,
            doc,
            amount,
            match_type,
            "HIGH",
            bd,
            self._match_reason(bd, match_type, pre_vat=pre_vat),
        )

    def _debt_candidates(self, part: dict[str, str], payment: dict[str, str], amount: int):
        cid = (part.get("КонтрагентID") or "").strip()
        pay_date = payment.get("ДатаОперации") or part.get("ДатаОперации") or ""
        pay_pos = payment.get("ПозицияДокумента") or part.get("ПозицияДокумента") or ""
        out = []
        for doc_state in self.ledger.documents.values():
            doc = doc_state.row
            if is_invoice_role(doc) or not is_active_doc(doc):
                continue
            if (doc.get("КонтрагентID") or "").strip() != cid:
                continue
            if not exists_at(doc, pay_date, pay_pos):
                continue
            if doc_state.remaining_for_debt() <= 0:
                continue
            out.append(doc_state)
        return out

    def _invoice_chain(self, pid: str, doc: dict[str, str]) -> bool:
        for ref in self.payment_refs.get(pid, []):
            if ref.kind != "INVOICE":
                continue
            if document_is_based_on_invoice(doc, ref.number, ref.date):
                return True
            for inv in self.bundle.documents:
                if not is_invoice_role(inv):
                    continue
                if (inv.get("КонтрагентID") or "") != (doc.get("КонтрагентID") or ""):
                    continue
                if not numbers_equal(inv.get("НомерДокумента") or "", ref.number):
                    continue
                if ref.date and inv.get("ДатаДокумента") and inv.get("ДатаДокумента") != ref.date:
                    continue
                if document_is_based_on_invoice(doc, inv.get("НомерДокумента") or "", inv.get("ДатаДокумента") or ""):
                    return True
        return False

    def _direct_ref(self, pid: str, doc: dict[str, str]) -> bool:
        number = doc.get("НомерДокумента") or ""
        for ref in self.payment_refs.get(pid, []):
            if ref.kind in ("GOODS", "SERVICE", "DOCUMENT") and numbers_equal(ref.number, number):
                return True
        pay = self.payments_by_id.get(pid, {})
        if (pay.get("PaymentKind") or "").upper() == "CASH":
            from kudir.base_document_links import parse_base_link

            link = parse_base_link(pay.get("КомментарийДокументаОплаты") or "")
            if link is not None and numbers_equal(link.base_number, number):
                return True
        return False

    def _score(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        doc: dict[str, str],
        amount: int,
        *,
        debt: bool,
    ) -> ScoreBreakdown:
        bd = ScoreBreakdown()
        pid = (payment.get("payment_id") or "").strip()
        if self._direct_ref(pid, doc):
            bd.direct_reference = self.cfg.w("direct_exact")
        if self._invoice_chain(pid, doc):
            bd.invoice_link = self.cfg.w("invoice_base_exact")
        doc_id = (doc.get("document_id") or "").strip()
        state = self.ledger.documents[doc_id]
        remaining = state.remaining_for_debt() if debt else state.available_for_advance()
        if amount == remaining and remaining > 0:
            bd.amount = self.cfg.w("exact_remainder")
        elif amount == state.full_amount:
            bd.amount = self.cfg.w("exact_full_amount")
        pay_c = (part.get("ДоговорID") or "").strip()
        doc_c = (doc.get("ДоговорID") or "").strip()
        if pay_c and doc_c and pay_c == doc_c:
            bd.contract = self.cfg.w("same_contract")
        elif pay_c and doc_c:
            bd.contract = self.cfg.w("different_contract")
        else:
            bd.contract = self.cfg.w("unknown_contract")
        pay_v = (part.get("ВидРасчетовID") or "").strip()
        doc_v = (doc.get("ВидРасчетовID") or "").strip()
        if pay_v and doc_v and pay_v == doc_v:
            bd.calculation_type = self.cfg.w("same_calculation_type")
        pay_text = " ".join(
            [
                payment.get("НазначениеПлатежа") or "",
                payment.get("Содержание") or "",
            ]
        )
        doc_text = " ".join([doc.get("Комментарий") or "", doc.get("Содержание") or ""])
        bd.semantic = semantic_score(pay_text, doc_text, self.cfg.w("semantic_max"))
        return bd

    def _debt_reason(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        key: AnalyticsKey,
        corr_index: int,
        foreign_credit: bool,
    ) -> str:
        tagged = self._posting_tag(payment, part, corr_index, debt=True)
        if tagged:
            return tagged
        parts = self.parts_by_payment.get(payment.get("payment_id") or "", [])
        contract_name = (part.get("Договор") or part.get("НомерДоговора") or "").strip()
        if len(parts) > 1 and contract_name:
            return f"ANALYTICS_{contract_name}_DEBT"
        if foreign_credit:
            return "NO_NETTING_WITH_B_CREDIT"
        if self.ledger.state(key).high_advance_closed:
            return "REMAINDER_AFTER_HIGH_ADVANCE_CLOSE"
        return "EXACT_REMAINDER"

    def _advance_open_reason(self, payment: dict[str, str], part: dict[str, str], corr_index: int) -> str:
        tagged = self._posting_tag(payment, part, corr_index, debt=False)
        if tagged:
            return tagged
        parts = self.parts_by_payment.get(payment.get("payment_id") or "", [])
        contract_name = (part.get("Договор") or part.get("НомерДоговора") or "").strip()
        if len(parts) > 1 and contract_name:
            return f"ANALYTICS_{contract_name}_NO_DEBT"
        return "ADVANCE_OPEN"

    def _posting_tag(self, payment: dict[str, str], part: dict[str, str], corr_index: int, debt: bool) -> str:
        pos = payment.get("ПозицияДокумента") or part.get("ПозицияДокумента") or ""
        posting = (part.get("НомерПроводкиВДокументе") or payment.get("НомерПроводкиВДокументе") or "").strip()
        parts = self.parts_by_payment.get(payment.get("payment_id") or "", [])
        multi_stmt = self._stmt_payment_count.get(pos, 0) > 1
        if not posting or not multi_stmt:
            return ""
        if len(parts) > 1:
            return f"POSTING_{posting}_CORR_{corr_index + 1}"
        return f"POSTING_{posting}_SEPARATE_PAYMENT"

    def _match_reason(self, bd: ScoreBreakdown, match_type: str, pre_vat: bool = False) -> str:
        if match_type == "ADVANCE_EXTENDED" and not bd.invoice_link:
            return "PRE_VAT_ADVANCE" if pre_vat else "ADVANCE_EXTENDED_MATCH"
        if match_type == "ADVANCE_EXTENDED" and bd.invoice_link:
            return "ADVANCE_EXTENDED_MATCH"
        bits: list[str] = []
        if bd.invoice_link:
            bits.append("INVOICE_BASE_EXACT")
        if bd.amount:
            bits.append("EXACT_REMAINDER")
        return "+".join(bits) if bits else match_type

    def _emit_result(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        row_type: str,
        matched: dict[str, str] | None,
        amount: int,
        match_type: str,
        confidence: str,
        reason: str,
    ) -> None:
        if matched is not None:
            contract_id = (matched.get("ДоговорID") or "").strip()
            contract_name = (matched.get("Договор") or matched.get("НомерДоговора") or "").strip()
        else:
            contract_id = (part.get("ДоговорID") or "").strip()
            contract_name = (part.get("Договор") or part.get("НомерДоговора") or "").strip()
        name = (payment.get("Контрагент") or part.get("Контрагент") or "").strip()
        self.results.append(
            result_row(
                run_id=self.bundle.manifest.get("run_id") or "",
                result_row_id=self._next_id(),
                payment_id=(payment.get("payment_id") or "").strip(),
                row_type=row_type,
                matched=matched,
                contract_id=contract_id,
                amount=amount,
                match_type=match_type,
                confidence=confidence,
                reason=reason,
                counterparty_name=name,
                contract_name=contract_name,
            )
        )

    def _emit_match(
        self,
        pid: str,
        doc: dict[str, str],
        amount: int,
        match_type: str,
        confidence: str,
        bd: ScoreBreakdown,
        reason: str,
    ) -> None:
        row = {
            "payment_id": pid,
            "document_id": (doc.get("document_id") or "").strip(),
            "matched_amount_kopecks": str(amount),
            "match_type": match_type,
            "confidence": confidence,
            "reason": reason,
        }
        row.update(bd.as_csv())
        self.matches.append(row)

    def _check_invariants(self) -> None:
        from collections import defaultdict

        sums: dict[str, int] = defaultdict(int)
        for row in self.results:
            sums[row["payment_id"]] += int(row["amount_kopecks"] or "0")
            if int(row["amount_kopecks"] or "0") < 0:
                raise MatchError("отрицательная сумма в результате")
        for pay in self.bundle.payments:
            if not self._target(pay):
                continue
            pid = (pay.get("payment_id") or "").strip()
            expected = kop(pay, "СуммаКоп")
            parts = self.parts_by_payment.get(pid) or []
            if parts:
                expected = sum(kop(p, "СуммаКоп") for p in parts)
            got = sums.get(pid, 0)
            if got != expected:
                raise MatchError(f"инвариант суммы {pid}: {got} != {expected}")
