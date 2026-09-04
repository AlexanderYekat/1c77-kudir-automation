"""analysis.log и запись run_status / CSV результата."""

from __future__ import annotations

from pathlib import Path

from kudir.ledger import RemainderMismatch, format_analytics
from kudir_proto.csv_io import (
    KUDIR_RESULT_FIELDS,
    MATCHES_FIELDS,
    RUN_STATUS_KEYS,
    UNRESOLVED_FIELDS,
    write_kv,
    write_rows,
)


class AnalysisLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message.rstrip() + "\n")

    def remainder_mismatch(self, item: RemainderMismatch) -> None:
        self.info(
            "DIAGNOSTIC unattributed_62 remainder_mismatch "
            f"analytics={format_analytics(item.key)} "
            f"signed_balance_kop={item.signed_balance} "
            f"document_remaining_sum_kop={item.document_remaining_sum}"
        )

    def write(self, path: Path) -> None:
        path.write_text("".join(self.lines), encoding="utf-8")


def write_outputs(
    directory: Path,
    *,
    results: list[dict[str, str]],
    matches: list[dict[str, str]],
    unresolved: list[dict[str, str]],
    run_status: dict[str, str],
    log: AnalysisLog,
) -> None:
    write_rows(directory / "kudir_result.csv", KUDIR_RESULT_FIELDS, results)
    write_rows(directory / "matches.csv", MATCHES_FIELDS, matches)
    write_rows(directory / "unresolved.csv", UNRESOLVED_FIELDS, unresolved)
    write_kv(directory / "run_status.csv", run_status, keys=RUN_STATUS_KEYS)
    log.write(directory / "analysis.log")
