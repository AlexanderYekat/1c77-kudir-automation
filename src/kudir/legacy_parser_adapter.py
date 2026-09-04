"""Адаптер внешнего HTTP-parser. Новый parser в репозиторий не копируется.

Формат — как клиент в 1cv77/доходы-2.txt:
POST text/plain; charset=windows-1251 на /parse1c
ответ:
  OK<TAB>СМЫСЛ
  DOC<TAB>ТипДок<TAB>НомерДок<TAB>ДатаДок
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kudir.base_document_links import normalize_number
from kudir.document_reference_parser import TextRef

DEFAULT_URL = "http://127.0.0.1:8765/parse1c"
DEFAULT_TIMEOUT = 2.0


@dataclass
class ParsedDoc:
    kind_raw: str
    number: str
    date: str


@dataclass
class ParseResult:
    sense: str
    docs: list[ParsedDoc] = field(default_factory=list)


def _iso_date(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    parts = text.replace("/", ".").split(".")
    if len(parts) != 3:
        return ""
    try:
        d, mo, y = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return ""
    if y < 100:
        y += 2000
    return f"{y:04d}-{mo:02d}-{d:02d}"


def map_doc_kind(raw: str) -> str:
    t = (raw or "").strip().upper().replace("Ё", "Е")
    if "СЧЕТ" in t or t in {"INVOICE", "СЧЁТ"}:
        return "INVOICE"
    if "НАКЛАД" in t or "ОТГРУЗ" in t or "ТОВАР" in t:
        return "GOODS"
    if "АКТ" in t or "УСЛУГ" in t:
        return "SERVICE"
    return "DOCUMENT"


def parse_response(body: str) -> ParseResult | None:
    """Разобрать текстовый ответ parse1c. None — ERROR или пустой/битый ответ."""
    lines = [ln.strip() for ln in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    first = lines[0].split("\t")
    code = (first[0] or "").strip().upper()
    if code == "ERROR":
        return None
    if code != "OK":
        return None
    sense = (first[1] if len(first) > 1 else "").strip() or "НЕОПРЕДЕЛЕНО"
    docs: list[ParsedDoc] = []
    for line in lines[1:]:
        fields = line.split("\t")
        if not fields or fields[0].strip().upper() != "DOC":
            continue
        kind = fields[1].strip() if len(fields) > 1 else ""
        number = fields[2].strip() if len(fields) > 2 else ""
        date = _iso_date(fields[3] if len(fields) > 3 else "")
        if kind and (number or date):
            docs.append(ParsedDoc(kind_raw=kind, number=normalize_number(number), date=date))
    return ParseResult(sense=sense, docs=docs)


def refs_from_parse(parsed: ParseResult) -> list[TextRef]:
    refs: list[TextRef] = []
    seen: set[tuple[str, str]] = set()
    for doc in parsed.docs:
        kind = map_doc_kind(doc.kind_raw)
        key = (kind, doc.number)
        if not doc.number or key in seen:
            continue
        seen.add(key)
        refs.append(TextRef(kind=kind, number=doc.number, date=doc.date))
    return refs


def merge_refs(*groups: list[TextRef]) -> list[TextRef]:
    seen: dict[tuple[str, str], TextRef] = {}
    for group in groups:
        for ref in group:
            key = (ref.kind, ref.number)
            prev = seen.get(key)
            if prev is None or (ref.date and not prev.date):
                seen[key] = ref
    return list(seen.values())


class LegacyParserAdapter:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.timeout = timeout
        self.available = False
        self._cache: dict[str, ParseResult | None] = {}
        self._probed = False

    def probe(self) -> bool:
        if self._probed:
            return self.available
        self._probed = True
        health = self.url.rsplit("/", 1)[0] + "/health"
        try:
            with urlopen(health, timeout=self.timeout) as resp:
                self.available = 200 <= getattr(resp, "status", 200) < 300
        except (URLError, OSError, TimeoutError, ValueError, HTTPError):
            try:
                req = Request(self.url, data=b" ", method="POST")
                req.add_header("Content-Type", "text/plain; charset=windows-1251")
                req.add_header("Accept", "text/plain")
                with urlopen(req, timeout=self.timeout) as resp:
                    self.available = 200 <= getattr(resp, "status", 200) < 300
            except (URLError, OSError, TimeoutError, ValueError, HTTPError):
                self.available = False
        return self.available

    def parse(self, text: str) -> ParseResult | None:
        if not (text or "").strip():
            return ParseResult(sense="НЕОПРЕДЕЛЕНО", docs=[])
        if not self.probe():
            return None
        if text in self._cache:
            return self._cache[text]
        body = text.encode("cp1251", errors="replace")
        req = Request(self.url, data=body, method="POST")
        req.add_header("Content-Type", "text/plain; charset=windows-1251")
        req.add_header("Accept", "text/plain")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except (URLError, OSError, TimeoutError, ValueError, HTTPError):
            self._cache[text] = None
            return None
        try:
            answer = raw.decode("cp1251")
        except UnicodeDecodeError:
            answer = raw.decode("utf-8", errors="replace")
        parsed = parse_response(answer)
        self._cache[text] = parsed
        return parsed
