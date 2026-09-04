"""Состояние взаиморасчётов по аналитике ledger62.

payments.csv деньги второй раз не двигает. Неатрибутированный 62
меняет только signed_balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field


AnalyticsKey = tuple[str, str, str, str]


def analytics_key(row: dict[str, str]) -> AnalyticsKey:
    return (
        (row.get("КонтрагентID") or "").strip(),
        (row.get("Счет62") or "").strip(),
        (row.get("ДоговорID") or "").strip(),
        (row.get("ВидРасчетовID") or "").strip(),
    )


def format_analytics(key: AnalyticsKey) -> str:
    return "+".join(key)


def kop(row: dict[str, str], field_name: str) -> int:
    raw = (row.get(field_name) or "0").strip() or "0"
    return int(raw)


@dataclass
class DocumentState:
    row: dict[str, str]
    remaining: int
    confirmed_debt: int = 0
    confirmed_advance: int = 0

    @property
    def document_id(self) -> str:
        return (self.row.get("document_id") or "").strip()

    @property
    def full_amount(self) -> int:
        return kop(self.row, "СуммаКоп")

    def available_for_advance(self) -> int:
        return max(0, self.full_amount - self.confirmed_debt - self.confirmed_advance)

    def remaining_for_debt(self) -> int:
        return max(0, self.remaining)


@dataclass
class AnalyticsState:
    key: AnalyticsKey
    signed_balance: int = 0
    high_advance_closed: bool = False


@dataclass
class RemainderMismatch:
    key: AnalyticsKey
    signed_balance: int
    document_remaining_sum: int


@dataclass
class Ledger:
    analytics: dict[AnalyticsKey, AnalyticsState] = field(default_factory=dict)
    documents: dict[str, DocumentState] = field(default_factory=dict)
    mismatches: list[RemainderMismatch] = field(default_factory=list)

    def state(self, key: AnalyticsKey) -> AnalyticsState:
        if key not in self.analytics:
            self.analytics[key] = AnalyticsState(key=key)
        return self.analytics[key]

    def debt_before(self, key: AnalyticsKey) -> int:
        return max(0, self.state(key).signed_balance)

    def apply_opening(self, row: dict[str, str]) -> None:
        key = analytics_key(
            {
                "КонтрагентID": row.get("КонтрагентID") or "",
                "Счет62": row.get("Счет62") or "",
                "ДоговорID": row.get("ДоговорID") or "",
                "ВидРасчетовID": row.get("ВидРасчетовID") or "",
            }
        )
        dt = kop(row, "ОстатокДтКоп")
        kt = kop(row, "ОстатокКтКоп")
        self.state(key).signed_balance = dt - kt

    def register_document(self, row: dict[str, str]) -> DocumentState:
        doc_id = (row.get("document_id") or "").strip()
        amount = kop(row, "СуммаКоп")
        if doc_id not in self.documents:
            self.documents[doc_id] = DocumentState(row=row, remaining=amount)
        return self.documents[doc_id]

    def apply_ledger_row(self, row: dict[str, str], *, attributed_doc_id: str = "") -> None:
        """Движение 62. attributed_doc_id — только однозначная ссылка на реализацию."""
        key = analytics_key(row)
        amount = kop(row, "СуммаКоп")
        side = (row.get("Сторона62") or "").strip().upper()
        if side == "D":
            self.state(key).signed_balance += amount
        elif side == "K":
            self.state(key).signed_balance -= amount
        else:
            debit = (row.get("СчетДебета") or "").startswith("62")
            credit = (row.get("СчетКредита") or "").startswith("62")
            if debit and not credit:
                self.state(key).signed_balance += amount
            elif credit and not debit:
                self.state(key).signed_balance -= amount

        if attributed_doc_id:
            doc = self.documents.get(attributed_doc_id)
            if doc is None:
                return
            # Сторно/корректировка этой реализации: remaining двигается.
            # Первичная отгрузка remaining уже задан из documents.csv — не удваивать.
            content = (row.get("СодержаниеПроводки") or "") + (row.get("ВидДокумента") or "")
            is_adjust = "сторно" in content.lower() or "корректир" in content.lower()
            if is_adjust:
                if side == "K":
                    doc.remaining = max(0, doc.remaining - amount)
                elif side == "D":
                    doc.remaining += amount

    def apply_unattributed(self, row: dict[str, str]) -> RemainderMismatch | None:
        key = analytics_key(row)
        self.apply_ledger_row(row, attributed_doc_id="")
        remaining_sum = self.document_remaining_sum(key)
        signed = self.state(key).signed_balance
        if remaining_sum != max(signed, 0) and remaining_sum != abs(signed):
            mismatch = RemainderMismatch(
                key=key,
                signed_balance=signed,
                document_remaining_sum=remaining_sum,
            )
            self.mismatches.append(mismatch)
            return mismatch
        if remaining_sum != max(signed, 0):
            mismatch = RemainderMismatch(
                key=key,
                signed_balance=signed,
                document_remaining_sum=remaining_sum,
            )
            self.mismatches.append(mismatch)
            return mismatch
        return None

    def document_remaining_sum(self, key: AnalyticsKey) -> int:
        total = 0
        cid, acc, dog, vid = key
        for doc in self.documents.values():
            row = doc.row
            if (row.get("КонтрагентID") or "").strip() != cid:
                continue
            if dog and (row.get("ДоговорID") or "").strip() != dog:
                continue
            if vid and (row.get("ВидРасчетовID") or "").strip() != vid:
                continue
            role = (row.get("DocumentRole") or "").strip().upper()
            if role == "INVOICE":
                continue
            total += doc.remaining
        return total

    def consume_debt(self, doc_id: str, amount: int) -> None:
        doc = self.documents[doc_id]
        doc.remaining = max(0, doc.remaining - amount)
        doc.confirmed_debt += amount

    def consume_advance(self, doc_id: str, amount: int) -> None:
        doc = self.documents[doc_id]
        take = min(amount, doc.available_for_advance())
        doc.confirmed_advance += take
        doc.remaining = max(0, doc.remaining - take)

    def split_payment_part(self, key: AnalyticsKey, part_amount: int) -> tuple[int, int, int]:
        """Вернуть debt_before, debt_part, advance_part. Неттинга между аналитиками нет."""
        before = self.debt_before(key)
        debt_part = min(part_amount, before)
        advance_part = part_amount - debt_part
        return before, debt_part, advance_part
