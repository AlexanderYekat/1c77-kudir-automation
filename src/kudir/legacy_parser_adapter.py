"""Адаптер внешнего HTTP-parser. Новый parser в репозиторий не копируется."""

from __future__ import annotations

from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

DEFAULT_URL = "http://127.0.0.1:8765/parse1c"
DEFAULT_HEALTH = "http://127.0.0.1:8765/health"


class LegacyParserAdapter:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 0.2) -> None:
        self.url = url
        self.timeout = timeout
        self.available = False
        self._cache: dict[str, Any] = {}
        self._probed = False

    def probe(self) -> bool:
        if self._probed:
            return self.available
        self._probed = True
        health = self.url.rsplit("/", 1)[0] + "/health"
        try:
            with urlopen(health, timeout=self.timeout) as resp:
                self.available = 200 <= getattr(resp, "status", 200) < 300
        except (URLError, OSError, TimeoutError, ValueError):
            try:
                req = Request(self.url, data=b"{}", method="POST")
                req.add_header("Content-Type", "application/json")
                with urlopen(req, timeout=self.timeout):
                    self.available = True
            except (URLError, OSError, TimeoutError, ValueError):
                self.available = False
        return self.available

    def parse(self, text: str) -> dict[str, Any] | None:
        if not text or not self.probe():
            return None
        if text in self._cache:
            return self._cache[text]
        # Живой формат — как у клиента в доходы-2.txt; для цикла 1 достаточно
        # факта доступности. Если POST не разобран, matcher идёт по суммам.
        self._cache[text] = None
        return None
