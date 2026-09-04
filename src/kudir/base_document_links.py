"""Структурный разбор «Введен на основании», не посимвольное сравнение строки."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def normalize_number(raw: str) -> str:
    text = (raw or "").strip()
    text = text.replace("№", " ").strip()
    text = re.sub(r"^[№N]\s*", "", text, flags=re.I)
    text = text.strip(" .")
    stripped = text.lstrip("0")
    return stripped or "0"


def numbers_equal(a: str, b: str) -> bool:
    return normalize_number(a) == normalize_number(b)


def _parse_date(raw: str) -> str:
    text = (raw or "").strip().rstrip(".")
    text = re.sub(r"\s*г\.?$", "", text, flags=re.I).strip()
    m = re.match(r"^(\d{1,2})[.](\d{1,2})[.](\d{2,4})$", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.match(r"^(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})", text)
    if m:
        month = _MONTHS.get(m.group(2).lower())
        if month:
            return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"
    return ""


@dataclass(frozen=True)
class BaseLink:
    base_type: str
    base_number: str
    base_date: str

    def is_invoice(self) -> bool:
        t = self.base_type.casefold()
        return "счет" in t or "счёт" in t or t in {"invoice", "invoce"}

    def receivable_kind(self) -> str | None:
        """SERVICE / GOODS / DOCUMENT. None — это счёт, не прямая ссылка на реализацию."""
        if self.is_invoice():
            return None
        t = self.base_type.casefold()
        if any(token in t for token in ("акт", "услуг", "service")):
            return "SERVICE"
        if any(token in t for token in ("накладн", "отгрузк", "товар", "goods")):
            return "GOODS"
        return "DOCUMENT"


_BASE_RE = re.compile(
    r"введен\s+на\s+основании\s*:?\s*(?P<type>[^№\d]+?)?\s*№\s*(?P<num>[^\s]+)"
    r"(?:\s+от\s+(?P<date>[^,\n;]+))?",
    re.I | re.S,
)


def parse_base_link(text: str) -> BaseLink | None:
    if not (text or "").strip():
        return None
    m = _BASE_RE.search(text.replace("\r", " ").replace("\n", " "))
    if not m:
        return None
    kind = (m.group("type") or "").strip(" :.")
    number = normalize_number(m.group("num") or "")
    date = _parse_date(m.group("date") or "")
    return BaseLink(base_type=kind or "UNKNOWN", base_number=number, base_date=date)


def document_is_based_on_invoice(doc: dict[str, str], invoice_number: str, invoice_date: str = "") -> bool:
    link = parse_base_link(doc.get("Комментарий") or "")
    if link is None or not link.is_invoice():
        return False
    if not numbers_equal(link.base_number, invoice_number):
        return False
    if invoice_date and link.base_date and link.base_date != invoice_date:
        return False
    return True
