"""Запуск matcher по каталогу обмена. Не stub_match прототипа."""

from __future__ import annotations

from pathlib import Path

from kudir.diagnostics import AnalysisLog, write_outputs
from kudir.legacy_parser_adapter import LegacyParserAdapter
from kudir.loaders import LoadError, load_exchange
from kudir.matcher import MatchError, Engine
from kudir.scoring import ScoringError, load_scoring
from kudir_proto.csv_io import RUN_STATUS_KEYS, SCHEMA_VERSION, file_sha256, read_kv, write_kv


class RunError(Exception):
    """Ошибка запуска: FAILED, без kudir_result.csv."""


def run_directory(exchange_dir: Path, scoring_path: Path) -> str:
    exchange_dir = exchange_dir.resolve()
    log = AnalysisLog()
    try:
        cfg = load_scoring(scoring_path)
    except ScoringError as exc:
        raise RunError(str(exc)) from exc
    try:
        bundle = load_exchange(exchange_dir)
    except LoadError as exc:
        raise RunError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise RunError(str(exc)) from exc
    except ValueError as exc:
        raise RunError(str(exc)) from exc

    adapter = LegacyParserAdapter()
    parser_available = "1" if adapter.probe() else "0"
    log.info(f"run_id={bundle.manifest.get('run_id')} parser_available={parser_available}")
    log.info(f"scoring_hash={file_sha256(scoring_path)}")

    engine = Engine(bundle=bundle, cfg=cfg, log=log)
    try:
        engine.run()
    except MatchError as exc:
        raise RunError(str(exc)) from exc

    unresolved_sum = sum(int(r.get("amount_kopecks") or "0") for r in engine.unresolved)
    tax_ready = "0" if engine.unresolved else "1"
    status = "SUCCESS"
    write_outputs(
        exchange_dir,
        results=engine.results,
        matches=engine.matches,
        unresolved=engine.unresolved,
        run_status={
            "run_id": bundle.manifest.get("run_id") or "",
            "status": status,
            "tax_ready": tax_ready,
            "unresolved_debt_kopecks": str(unresolved_sum),
            "unresolved_count": str(len(engine.unresolved)),
            "parser_available": parser_available,
            "schema_version": SCHEMA_VERSION,
            "scoring_hash": file_sha256(scoring_path),
        },
        log=log,
    )
    return status


def write_failed(exchange_dir: Path, error: str) -> None:
    run_id = ""
    manifest = exchange_dir / "manifest.csv"
    if manifest.is_file():
        try:
            run_id = read_kv(manifest).get("run_id") or ""
        except (FileNotFoundError, ValueError, OSError):
            run_id = ""
    write_kv(
        exchange_dir / "run_status.csv",
        {
            "run_id": run_id,
            "status": "FAILED",
            "tax_ready": "0",
            "unresolved_debt_kopecks": "0",
            "unresolved_count": "0",
            "parser_available": "0",
            "schema_version": "",
            "scoring_hash": "",
            "error": error,
        },
        keys=RUN_STATUS_KEYS,
    )
    result = exchange_dir / "kudir_result.csv"
    if result.exists():
        result.unlink()
