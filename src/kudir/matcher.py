"""Matcher цикла 1: разрез по аналитике, HIGH сразу, conflict pass для хвостов.

Не наследует kudir_proto.engine.stub_match. FIFO нет.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

from kudir.base_document_links import document_is_based_on_invoice, numbers_equal, parse_base_link
from kudir.combinations import cap_candidates, search_combination
from kudir.diagnostics import AnalysisLog
from kudir.document_reference_parser import TextRef, extract_text_refs
from kudir.kudir import is_goods, is_service, result_row
from kudir.ledger import AnalyticsKey, Ledger, analytics_key, format_analytics, kop
from kudir.legacy_parser_adapter import LegacyParserAdapter, merge_refs, refs_from_parse
from kudir.loaders import ExchangeBundle
from kudir.scoring import ScoreBreakdown, ScoringConfig, classify_confidence, pick_high
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


def posting_key(posting: str) -> tuple[int, int, str]:
    """НомерПроводкиВДокументе — число, не строка: 1,2,10,11 а не 1,10,11,2."""
    text = (posting or "").strip()
    if not text:
        return (0, 0, "")
    if text.isdigit() or (text[0] in "+-" and text[1:].isdigit()):
        return (0, int(text), "")
    digits: list[str] = []
    for ch in text:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    if digits:
        return (1, int("".join(digits)), text)
    return (2, 0, text)


def sort_tuple(date_s: str, pos: str, posting: str = "") -> tuple[str, str, tuple[int, int, str]]:
    return (date_s or "", pos or "", posting_key(posting))


def is_invoice_role(row: dict[str, str]) -> bool:
    return (row.get("DocumentRole") or "").strip().upper() == "INVOICE"


def is_active_doc(row: dict[str, str]) -> bool:
    if (row.get("ПомеченНаУдаление") or "0").strip() == "1":
        return False
    if (row.get("Проведен") or "1").strip() == "0":
        return False
    return True


def exists_at(doc: dict[str, str], date_s: str, pos: str) -> bool:
    return sort_tuple(doc.get("ДатаДокумента") or "", doc.get("ПозицияДокумента") or "") <= sort_tuple(date_s, pos)


def after_payment(doc: dict[str, str], date_s: str, pos: str) -> bool:
    return sort_tuple(doc.get("ДатаДокумента") or "", doc.get("ПозицияДокумента") or "") > sort_tuple(date_s, pos)


def _ordinal(iso: str) -> int:
    text = (iso or "").strip()
    if len(text) < 10:
        return 0
    try:
        y, m, d = int(text[:4]), int(text[5:7]), int(text[8:10])
        return date(y, m, d).toordinal()
    except ValueError:
        return 0


def _pos_int(pos: str) -> int:
    text = (pos or "").strip()
    return int(text) if text.isdigit() else 0


def chrono_distance(pay_date: str, pay_pos: str, doc: dict[str, str], *, debt: bool) -> int:
    """Меньше — ближе к оплате. DEBT: ближайший предшествующий. ADVANCE: ближайший следующий."""
    doc_ord = _ordinal(doc.get("ДатаДокумента") or "")
    pay_ord = _ordinal(pay_date)
    doc_pos = _pos_int(doc.get("ПозицияДокумента") or "")
    ppos = _pos_int(pay_pos)
    if debt:
        days = max(0, pay_ord - doc_ord)
        pos_gap = max(0, ppos - doc_pos) if days == 0 else 0
    else:
        days = max(0, doc_ord - pay_ord)
        pos_gap = max(0, doc_pos - ppos) if days == 0 else 0
    return days * 1_000_000 + pos_gap


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
    open_reason: str = ""
    matched_doc_id: str = ""


@dataclass
class Engine:
    bundle: ExchangeBundle
    cfg: ScoringConfig
    log: AnalysisLog
    parser: LegacyParserAdapter | None = None
    ledger: Ledger = field(default_factory=Ledger)
    results: list[dict[str, str]] = field(default_factory=list)
    matches: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    open_advances: list[OpenAdvance] = field(default_factory=list)
    payment_refs: dict[str, list[TextRef]] = field(default_factory=dict)
    parser_failures: int = 0
    _row_n: int = 0
    historical_unresolved: list[dict[str, str]] = field(default_factory=list)
    uncertain_analytics: set[AnalyticsKey] = field(default_factory=set)
    _cp_deadline: dict[str, float] = field(default_factory=dict)

    def run(self) -> None:
        self._index()
        for row in self.bundle.openings:
            self.ledger.apply_opening(row)
        for doc in self.bundle.documents:
            self.ledger.register_document(doc)
        processed: set[str] = set()
        events = sorted(
            self.bundle.ledger,
            key=lambda r: (
                *sort_tuple(
                    r.get("ДатаОперации") or "",
                    r.get("ПозицияДокумента") or "",
                    r.get("НомерПроводкиВДокументе") or "",
                ),
                r.get("ledger_event_id") or "",
            ),
        )
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
            parts.sort(
                key=lambda r: (
                    posting_key(r.get("НомерПроводкиВДокументе") or ""),
                    r.get("ledger_event_id") or "",
                )
            )
            pay = self.payments_by_id.get(pid, {})
            local = extract_text_refs(
                pay.get("НазначениеПлатежа") or "",
                pay.get("СодержаниеПроводки") or "",
                pay.get("Содержание") or "",
                pay.get("ПредставлениеПроводки") or "",
                pay.get("ПервичныйДокумент") or "",
                pay.get("КомментарийДокументаОплаты") or "",
            )
            legacy: list[TextRef] = []
            if self.parser is not None and self.parser.available:
                text = " ".join(
                    [
                        pay.get("НазначениеПлатежа") or "",
                        pay.get("СодержаниеПроводки") or "",
                        pay.get("Содержание") or "",
                        pay.get("ПредставлениеПроводки") or "",
                        pay.get("ПервичныйДокумент") or "",
                        pay.get("КомментарийДокументаОплаты") or "",
                    ]
                ).strip()
                if text:
                    parsed = self.parser.parse(text)
                    if parsed is None:
                        self.parser_failures += 1
                    else:
                        legacy = refs_from_parse(parsed)
            self.payment_refs[pid] = merge_refs(local, legacy)
        self._stmt_payment_count: dict[str, int] = {}
        for p in self.bundle.payments:
            pos = p.get("ПозицияДокумента") or ""
            self._stmt_payment_count[pos] = self._stmt_payment_count.get(pos, 0) + 1

    def _next_id(self) -> str:
        self._row_n += 1
        return f"R{self._row_n:05d}"

    def _target(self, payment: dict[str, str]) -> bool:
        return (payment.get("is_target") or "").strip() == "1"

    def _timeout_deadline(self, cid: str) -> float:
        if cid not in self._cp_deadline:
            self._cp_deadline[cid] = time.monotonic() + float(self.cfg.s("timeout_seconds_per_counterparty"))
        return self._cp_deadline[cid]

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
        remaining_debt: dict[AnalyticsKey, int] = {}
        keys: list[AnalyticsKey] = []
        for part in parts:
            key = analytics_key(part)
            keys.append(key)
            if key not in remaining_debt:
                remaining_debt[key] = self.ledger.debt_before(key)
        foreign_credit = self._foreign_credit(payment, keys)
        for part in parts:
            self.ledger.apply_ledger_row(part, attributed_doc_id="")
        for i, part in enumerate(parts):
            amount = kop(part, "СуммаКоп")
            key = keys[i]
            debt_part = min(amount, remaining_debt[key])
            remaining_debt[key] -= debt_part
            advance_part = amount - debt_part
            if debt_part:
                self._match_debt(payment, part, key, debt_part, i, foreign_credit)
            if advance_part:
                self._open_advance(payment, part, key, advance_part, i)

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
        unique_direct = sum(1 for d in cands if self._direct_ref(pid, d.row)) == 1
        unique_invoice = sum(1 for d in cands if self._invoice_chain(pid, d.row)) == 1
        scored: list[tuple[object, ScoreBreakdown]] = []
        for d in cands:
            bd = self._score(
                payment,
                part,
                d.row,
                min(amount, d.remaining_for_debt()),
                debt=True,
                unique_direct=unique_direct if self._direct_ref(pid, d.row) else None,
                unique_invoice=unique_invoice if self._invoice_chain(pid, d.row) else None,
            )
            scored.append((d, bd))
        pay_date = payment.get("ДатаОперации") or part.get("ДатаОперации") or ""
        pay_pos = payment.get("ПозицияДокумента") or part.get("ПозицияДокумента") or ""

        chosen = None
        bd = ScoreBreakdown()
        unique_remainder = [d for d in cands if d.remaining_for_debt() == amount]
        unique_chain = [d for d in cands if self._invoice_chain(pid, d.row) or self._direct_ref(pid, d.row)]
        if len(unique_chain) == 1 and amount <= unique_chain[0].remaining_for_debt():
            chosen = unique_chain[0]
            bd = self._score(
                payment, part, chosen.row, amount, debt=True,
                unique_direct=True if self._direct_ref(pid, chosen.row) else None,
                unique_invoice=True if self._invoice_chain(pid, chosen.row) else None,
            )
        elif len(unique_remainder) == 1 and len(cands) == 1:
            chosen = unique_remainder[0]
            bd = next(
                (s[1] for s in scored if s[0] is chosen),
                self._score(payment, part, chosen.row, amount, debt=True),
            )
        else:
            scored, capped = cap_candidates(scored, self.cfg)
            if capped:
                self.log.info(f"SEARCH_CAP payment_id={pid}")
                self._record_unresolved(payment, part, key, amount, "SEARCH_CAP")
                return
            self._rerank_semantic(payment, scored)
            distances = [chrono_distance(pay_date, pay_pos, item[0].row, debt=True) for item in scored]
            picked = pick_high(scored, distances, self.cfg)
            if picked is not None:
                chosen, bd = picked

        if chosen is not None:
            conf = classify_confidence(bd.selection_total, 0, self.cfg, bd)
            if conf == "HIGH" or chosen in unique_chain or (
                len(unique_remainder) == 1 and len(cands) == 1 and chosen is unique_remainder[0]
            ):
                take = min(amount, chosen.remaining_for_debt())
                self.ledger.consume_debt(chosen.document_id, take)
                reason = self._debt_reason(payment, part, key, corr_index, foreign_credit)
                match_type = self._debt_match_type(payment, chosen.row, bd, take, amount)
                if self._target(payment):
                    self._emit_result(
                        payment, part, "DEBT", chosen.row, take, match_type, "HIGH", reason,
                    )
                    self._emit_match(pid, chosen.row, take, match_type, "HIGH", bd, self._match_reason(bd, match_type))
                leftover = amount - take
                if leftover > 0:
                    self._record_unresolved(payment, part, key, leftover, "DEBT_TAIL")
                return

        combo_applied = self._try_debt_combination(payment, part, key, amount, corr_index, foreign_credit, scored)
        if combo_applied:
            return
        self._record_unresolved(payment, part, key, amount, "NO_DOCUMENT_IN_HISTORY")

    def _try_debt_combination(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        key: AnalyticsKey,
        amount: int,
        corr_index: int,
        foreign_credit: bool,
        scored: list,
    ) -> bool:
        pid = (payment.get("payment_id") or "").strip()
        items = []
        for doc_state, _bd in scored:
            take = min(amount, doc_state.remaining_for_debt())
            if take <= 0:
                continue
            items.append((doc_state, take))
        chosen, timed_out = search_combination(
            items, amount, deadline=self._timeout_deadline((part.get("КонтрагентID") or "").strip()), exact=True, min_parts=2,
        )
        if timed_out:
            self.log.info(f"search timeout payment_id={pid}")
            return False
        if not chosen:
            return False
        bds: list[ScoreBreakdown] = []
        for doc_state in chosen:
            take = min(amount, doc_state.remaining_for_debt())
            bd = self._score(payment, part, doc_state.row, take, debt=True, in_combination=True)
            bds.append(bd)
        min_bd = min(bds, key=lambda x: x.total)
        if classify_confidence(min_bd.selection_total, None, self.cfg, min_bd) != "HIGH":
            return False
        reason = self._debt_reason(payment, part, key, corr_index, foreign_credit)
        for doc_state, bd in zip(chosen, bds):
            take = min(amount, doc_state.remaining_for_debt())
            if take <= 0:
                continue
            self.ledger.consume_debt(doc_state.document_id, take)
            if self._target(payment):
                self._emit_result(
                    payment, part, "DEBT", doc_state.row, take, "EXACT_COMBINATION", "HIGH", reason,
                )
                self._emit_match(
                    pid, doc_state.row, take, "EXACT_COMBINATION", "HIGH", bd, "EXACT_COMBINATION",
                )
        return True

    def _debt_match_type(self, payment: dict[str, str], doc: dict[str, str], bd: ScoreBreakdown, take: int, amount: int) -> str:
        pid = (payment.get("payment_id") or "").strip()
        if self._is_cash_base(pid, doc):
            return "CASH_BASE_EXACT" if take == amount else "HIGH_PARTIAL_DIRECT"
        if self._direct_ref(pid, doc) and (
            bd.direct_reference >= self.cfg.w("direct_exact") or bd.direct_reference >= self.cfg.w("cash_base_exact")
        ):
            return "DIRECT_DOCUMENT_EXACT" if take == amount else "HIGH_PARTIAL_DIRECT"
        if bd.invoice_link and take == amount:
            return "INVOICE_BASE_EXACT"
        return "EXACT_REMAINDER"

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
                open_reason=reason,
            )
        )

    def _try_high_close(self, doc_id: str) -> None:
        doc_state = self.ledger.documents.get(doc_id)
        if doc_state is None or is_invoice_role(doc_state.row):
            return
        doc = doc_state.row
        if not is_active_doc(doc):
            return
        if doc_state.available_for_advance() <= 0:
            return
        cid = (doc.get("КонтрагентID") or "").strip()
        linked: list[OpenAdvance] = []
        for adv in self.open_advances:
            if adv.amount <= 0:
                continue
            if adv.analytics[0] != cid:
                continue
            pay_date = adv.payment.get("ДатаОперации") or adv.part.get("ДатаОперации") or ""
            pay_pos = adv.payment.get("ПозицияДокумента") or adv.part.get("ПозицияДокумента") or ""
            if not after_payment(doc, pay_date, pay_pos):
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
        bd = self._score(adv.payment, adv.part, doc, take, debt=False, unique_direct=True, unique_invoice=True)
        if classify_confidence(bd.selection_total, 0, self.cfg, bd) != "HIGH":
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
        progress = True
        while progress:
            progress = False
            for adv in self.open_advances:
                if adv.amount <= 0:
                    continue
                if self._resolve_open_advance(adv, horizon):
                    progress = True

    def _resolve_open_advance(self, adv: OpenAdvance, horizon: str) -> bool:
        pay_date = adv.payment.get("ДатаОперации") or adv.part.get("ДатаОперации") or ""
        pay_pos = adv.payment.get("ПозицияДокумента") or adv.part.get("ПозицияДокумента") or ""
        q_end = quarter_end(pay_date)
        scored: list[tuple[object, ScoreBreakdown, str]] = []
        cid = (adv.part.get("КонтрагентID") or "").strip()
        pid = adv.payment_id
        raw_docs = []
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
            raw_docs.append(doc_state)
        unique_direct = sum(1 for d in raw_docs if self._direct_ref(pid, d.row)) == 1
        unique_invoice = sum(1 for d in raw_docs if self._invoice_chain(pid, d.row)) == 1
        for doc_state in raw_docs:
            doc = doc_state.row
            window = "TAX" if (doc.get("ДатаДокумента") or "") <= q_end else "EXTENDED"
            take = min(adv.amount, doc_state.available_for_advance())
            bd = self._score(
                adv.payment,
                adv.part,
                doc,
                take,
                debt=False,
                unique_direct=unique_direct if self._direct_ref(pid, doc) else None,
                unique_invoice=unique_invoice if self._invoice_chain(pid, doc) else None,
            )
            scored.append((doc_state, bd, window))
        if not scored:
            return False
        pairs = [(state, bd) for state, bd, _window in scored]
        pairs, capped = cap_candidates(pairs, self.cfg)
        if capped:
            self.log.info(f"SEARCH_CAP payment_id={adv.payment_id}")
            return False
        self._rerank_semantic(adv.payment, pairs)
        distances = [chrono_distance(pay_date, pay_pos, item[0].row, debt=False) for item in pairs]
        picked = pick_high(pairs, distances, self.cfg)
        window_by_id = {(state.document_id): window for state, _bd, window in scored}
        if picked is not None:
            best_state, best_bd = picked
            window = window_by_id.get(best_state.document_id, "TAX")
            take = min(adv.amount, best_state.available_for_advance())
            self._close_advance(adv, best_state, take, best_bd, immediate=False, window=window)
            return True
        combo_scored = [(state, bd, window_by_id.get(state.document_id, "TAX")) for state, bd in pairs]
        combo = self._try_advance_combination(adv, combo_scored, "TAX")
        if combo:
            return True
        if len(pairs) > 1:
            self._mark_ambiguous(adv)
        return False

    def _try_advance_combination(self, adv: OpenAdvance, scored: list, default_window: str) -> bool:
        items = []
        for doc_state, _bd, _window in scored:
            take = min(adv.amount, doc_state.available_for_advance())
            if take <= 0:
                continue
            items.append((doc_state, take))
        chosen, timed_out = search_combination(
            items, adv.amount, deadline=self._timeout_deadline((adv.part.get("КонтрагентID") or "").strip()), exact=False, min_parts=2,
        )
        if timed_out:
            self.log.info(f"search timeout payment_id={adv.payment_id}")
            return False
        if not chosen:
            return False
        bds = []
        for doc_state in chosen:
            take = min(adv.amount, doc_state.available_for_advance())
            bd = self._score(adv.payment, adv.part, doc_state.row, take, debt=False, in_combination=True)
            bds.append(bd)
        min_bd = min(bds, key=lambda x: x.total)
        if classify_confidence(min_bd.selection_total, None, self.cfg, min_bd) != "HIGH":
            return False
        pay_date = adv.payment.get("ДатаОперации") or adv.part.get("ДатаОперации") or ""
        q_end = quarter_end(pay_date)
        for doc_state, bd in zip(chosen, bds):
            take = min(adv.amount, doc_state.available_for_advance())
            if take <= 0:
                break
            doc_date = doc_state.row.get("ДатаДокумента") or ""
            window = "TAX" if doc_date <= q_end else "EXTENDED"
            self._close_advance(adv, doc_state, take, bd, immediate=False, window=window)
        return True

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
            match_type = "ADVANCE_EXTENDED_MATCH"
            if bd.invoice_link or immediate:
                result_reason = "EXTENDED_MATCH_NO_TAX_RECALC_Q4"
            elif pre_vat:
                result_reason = "PRE_VAT_ADVANCE;NO_CALCULATED_5_105"
            else:
                result_reason = "EXTENDED_MATCH_NO_TAX_RECALC_Q4"
        else:
            if bd.invoice_link:
                match_type = "ADVANCE_INVOICE_BASE"
            elif bd.direct_reference >= self.cfg.w("direct_exact") or bd.direct_reference >= self.cfg.w("cash_base_exact"):
                match_type = "ADVANCE_TAX_WINDOW_DIRECT"
            else:
                match_type = "ADVANCE_TAX_WINDOW_AMOUNT"
            result_reason = "HIGH_INVOICE_CHAIN" if bd.invoice_link else "ADVANCE_TAX_WINDOW"
        self.ledger.consume_advance(doc_state.document_id, amount)
        self.ledger.state(adv.analytics).high_advance_closed = True
        leftover = adv.amount - amount
        adv.amount = leftover
        adv.matched_doc_id = doc_state.document_id
        if adv.result_index is not None:
            self._rewrite_result(
                adv.result_index,
                payment=adv.payment,
                part=adv.part,
                row_type="ADVANCE",
                matched=doc,
                amount=amount,
                match_type=match_type,
                confidence="HIGH",
                reason=result_reason,
            )
            if leftover > 0:
                self._emit_result(
                    adv.payment,
                    adv.part,
                    "ADVANCE",
                    None,
                    leftover,
                    "ADVANCE_OPEN",
                    "HIGH",
                    adv.open_reason or "ADVANCE_OPEN",
                )
                adv.result_index = len(self.results) - 1
        self._emit_match(
            adv.payment_id,
            doc,
            amount,
            match_type,
            "HIGH",
            bd,
            self._match_reason(bd, match_type, pre_vat=pre_vat),
        )

    def _rewrite_result(
        self,
        index: int,
        *,
        payment: dict[str, str],
        part: dict[str, str],
        row_type: str,
        matched: dict[str, str] | None,
        amount: int,
        match_type: str,
        confidence: str,
        reason: str,
    ) -> None:
        old = self.results[index]
        if matched is not None:
            contract_id = (matched.get("ДоговорID") or "").strip()
            contract_name = (matched.get("Договор") or matched.get("НомерДоговора") or "").strip()
        else:
            contract_id = (part.get("ДоговорID") or "").strip()
            contract_name = (part.get("Договор") or part.get("НомерДоговора") or "").strip()
        name = (payment.get("Контрагент") or part.get("Контрагент") or "").strip()
        rebuilt = result_row(
            run_id=old.get("run_id") or self.bundle.manifest.get("run_id") or "",
            result_row_id=old.get("result_row_id") or self._next_id(),
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
        self.results[index] = rebuilt

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
        if is_invoice_role(doc):
            return False
        number = doc.get("НомерДокумента") or ""
        doc_date = doc.get("ДатаДокумента") or ""
        for ref in self.payment_refs.get(pid, []):
            if ref.kind not in ("GOODS", "SERVICE", "DOCUMENT"):
                continue
            if not numbers_equal(ref.number, number):
                continue
            if ref.date and doc_date and ref.date != doc_date:
                continue
            if ref.kind == "SERVICE" and not is_service(doc):
                continue
            if ref.kind == "GOODS" and not is_goods(doc):
                continue
            return True
        return self._is_cash_base(pid, doc)

    def _is_cash_base(self, pid: str, doc: dict[str, str]) -> bool:
        pay = self.payments_by_id.get(pid, {})
        if (pay.get("PaymentKind") or "").upper() != "CASH":
            return False
        if is_invoice_role(doc):
            return False
        link = parse_base_link(pay.get("КомментарийДокументаОплаты") or "")
        if link is None:
            return False
        kind = link.receivable_kind()
        if kind is None:
            return False
        if not numbers_equal(link.base_number, doc.get("НомерДокумента") or ""):
            return False
        doc_date = doc.get("ДатаДокумента") or ""
        if link.base_date and doc_date and link.base_date != doc_date:
            return False
        if kind == "SERVICE" and not is_service(doc):
            return False
        if kind == "GOODS" and not is_goods(doc):
            return False
        return True

    def _same_invoice_weak(self, pid: str, doc: dict[str, str]) -> bool:
        comment = (doc.get("Комментарий") or "") + " " + (doc.get("Содержание") or "")
        for ref in self.payment_refs.get(pid, []):
            if ref.kind != "INVOICE" or not ref.number:
                continue
            if numbers_equal(ref.number, doc.get("НомерДокумента") or ""):
                return True
            if ref.number and ref.number in comment.replace("№", " "):
                return True
        return False

    def _goods_or_service_affinity(self, pid: str, doc: dict[str, str]) -> bool:
        kinds = {ref.kind for ref in self.payment_refs.get(pid, [])}
        if "SERVICE" in kinds and is_service(doc):
            return True
        if "GOODS" in kinds and is_goods(doc):
            return True
        return False

    def _rerank_semantic(self, payment: dict[str, str], scored: list) -> None:
        """Semantic только для неоднозначного shortlist, не внутри _score."""
        if not scored:
            return
        pay_text = " ".join(
            [
                payment.get("НазначениеПлатежа") or "",
                payment.get("Содержание") or "",
            ]
        )
        for item in scored:
            doc = item[0].row
            bd = item[1]
            if bd.direct_reference >= self.cfg.w("direct_exact"):
                continue
            if bd.invoice_link >= self.cfg.w("invoice_base_exact"):
                continue
            doc_text = " ".join([doc.get("Комментарий") or "", doc.get("Содержание") or ""])
            bd.semantic = semantic_score(pay_text, doc_text, self.cfg.w("semantic_max"))

    def _score(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        doc: dict[str, str],
        amount: int,
        *,
        debt: bool,
        unique_direct: bool | None = None,
        unique_invoice: bool | None = None,
        in_combination: bool = False,
    ) -> ScoreBreakdown:
        bd = ScoreBreakdown()
        pid = (payment.get("payment_id") or "").strip()
        if self._is_cash_base(pid, doc):
            bd.direct_reference = self.cfg.w("cash_base_exact")
        elif self._direct_ref(pid, doc):
            if unique_direct is False:
                bd.direct_reference = self.cfg.w("direct_hint")
            else:
                bd.direct_reference = self.cfg.w("direct_exact")
        elif self._goods_or_service_affinity(pid, doc):
            bd.direct_reference = self.cfg.w("goods_or_service_match")
        if self._invoice_chain(pid, doc):
            if unique_invoice is False:
                bd.invoice_link = self.cfg.w("same_invoice")
            else:
                bd.invoice_link = self.cfg.w("invoice_base_exact")
        elif self._same_invoice_weak(pid, doc):
            bd.invoice_link = self.cfg.w("same_invoice")
        doc_id = (doc.get("document_id") or "").strip()
        state = self.ledger.documents[doc_id]
        remaining = state.remaining_for_debt() if debt else state.available_for_advance()
        if in_combination:
            bd.amount = self.cfg.w("exact_combination")
        elif amount == remaining and remaining > 0:
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
        if match_type == "ADVANCE_EXTENDED_MATCH" and not bd.invoice_link:
            return "PRE_VAT_ADVANCE" if pre_vat else "ADVANCE_EXTENDED_MATCH"
        if match_type == "ADVANCE_EXTENDED_MATCH" and bd.invoice_link:
            return "ADVANCE_EXTENDED_MATCH"
        bits: list[str] = []
        if bd.invoice_link:
            bits.append("INVOICE_BASE_EXACT")
        if bd.amount:
            bits.append("EXACT_REMAINDER" if not match_type.startswith("EXACT_COMBINATION") else "EXACT_COMBINATION")
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
                ledger_sum = sum(kop(p, "СуммаКоп") for p in parts)
                if ledger_sum != expected:
                    raise MatchError(f"инвариант payments↔ledger {pid}: {ledger_sum} != {expected}")
            got = sums.get(pid, 0)
            if got != expected:
                raise MatchError(f"инвариант суммы {pid}: {got} != {expected}")

    def _record_unresolved(
        self,
        payment: dict[str, str],
        part: dict[str, str],
        key: AnalyticsKey,
        amount: int,
        reason: str,
    ) -> None:
        pid = (payment.get("payment_id") or part.get("payment_id") or "").strip()
        item = {"payment_id": pid, "amount_kopecks": str(amount), "reason": reason}
        if self._target(payment):
            self._emit_result(payment, part, "DEBT_UNRESOLVED", None, amount, "DEBT_UNRESOLVED", "LOW", reason)
            self.unresolved.append(item)
            return
        self.historical_unresolved.append(item)
        self.uncertain_analytics.add(key)
        self.log.info(
            f"HISTORICAL_UNRESOLVED payment_id={pid} amount_kopecks={amount} "
            f"analytics={format_analytics(key)} reason={reason}"
        )
