"""Локальный разбор явных ссылок в тексте оплаты. Это не новый NLP-parser."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kudir.base_document_links import normalize_number, parse_base_link


@dataclass(frozen=True)
class TextRef:
    kind: str  # INVOICE / GOODS / SERVICE / DOCUMENT
    number: str
    date: str


_INVOICE_RE = re.compile(
    r"сч[её]т[ауе]?\s*(?:на\s+оплату)?\s*№\s*(?P<num>[A-Za-zА-Яа-я0-9\-]+)",
    re.I,
)
_GOODS_RE = re.compile(
    r"(?:накладн\w*|отгрузк\w*)\s*№\s*(?P<num>[A-Za-zА-Яа-я0-9\-]+)",
    re.I,
)
_ACT_RE = re.compile(
    r"акт[ауе]?\s*№\s*(?P<num>[A-Za-zА-Яа-я0-9\-]+)",
    re.I,
)


def _date_near(text: str, start: int) -> str:
    tail = text[start : start + 40]
    m = re.search(r"от\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4})", tail)
    if not m:
        return ""
    raw = m.group(1).replace("/", ".")
    parts = raw.split(".")
    if len(parts) != 3:
        return ""
    d, mo, y = int(parts[0]), int(parts[1]), int(parts[2])
    if y < 100:
        y += 2000
    return f"{y:04d}-{mo:02d}-{d:02d}"


def extract_text_refs(*parts: str) -> list[TextRef]:
    text = " ".join(p for p in parts if p)
    refs: list[TextRef] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, number: str, date: str) -> None:
        number = normalize_number(number)
        key = (kind, number)
        if not number or key in seen:
            return
        seen.add(key)
        refs.append(TextRef(kind=kind, number=number, date=date))

    for m in _INVOICE_RE.finditer(text):
        add("INVOICE", m.group("num"), _date_near(text, m.end()))
    for m in _GOODS_RE.finditer(text):
        add("GOODS", m.group("num"), _date_near(text, m.end()))
    for m in _ACT_RE.finditer(text):
        add("SERVICE", m.group("num"), _date_near(text, m.end()))

    base = parse_base_link(text)
    if base is not None:
        kind = "INVOICE" if base.is_invoice() else "DOCUMENT"
        add(kind, base.base_number, base.base_date)
    return refs
