"""Запуск matcher по каталогу обмена. Не stub_match прототипа."""

from __future__ import annotations

from pathlib import Path

from kudir.diagnostics import AnalysisLog, write_outputs
from kudir.legacy_parser_adapter import LegacyParserAdapter
from kudir.ledger import analytics_key
from kudir.loaders import LoadError, load_exchange
from kudir.matcher import Engine, MatchError
from kudir.scoring import ScoringError, load_scoring
from kudir_proto.csv_io import RUN_STATUS_KEYS, SCHEMA_VERSION, file_sha256, read_kv, write_kv


class RunError(Exception):
    """Ошибка запуска: FAILED, без kudir_result.csv."""


def run_directory(
    exchange_dir: Path,
    scoring_path: Path,
    *,
    parser: LegacyParserAdapter | None = None,
) -> str:
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

    adapter = parser if parser is not None else LegacyParserAdapter()
    parser_available = "1" if adapter.probe() else "0"
    log.info(f"run_id={bundle.manifest.get('run_id')} parser_available={parser_available}")
    log.info(f"scoring_hash={file_sha256(scoring_path)}")

    engine = Engine(bundle=bundle, cfg=cfg, log=log, parser=adapter)
    try:
        engine.run()
    except MatchError as exc:
        raise RunError(str(exc)) from exc

    unresolved_sum = sum(int(r.get("amount_kopecks") or "0") for r in engine.unresolved)
    hist_sum = sum(int(r.get("amount_kopecks") or "0") for r in engine.historical_unresolved)
    target_keys: set = set()
    for pay in engine.bundle.payments:
        if (pay.get("is_target") or "").strip() != "1":
            continue
        pid = (pay.get("payment_id") or "").strip()
        for part in engine.parts_by_payment.get(pid, []):
            target_keys.add(analytics_key(part))
    hist_affects_target = bool(engine.uncertain_analytics & target_keys)
    tax_ready = "0" if engine.unresolved or hist_affects_target else "1"
    state_uncertain = "1" if engine.historical_unresolved else "0"
    if parser_available != "1" or engine.parser_failures:
        status = "SUCCESS_DEGRADED"
        if parser_available != "1":
            log.info("parser unavailable → SUCCESS_DEGRADED")
        if engine.parser_failures:
            log.info(f"parser failures={engine.parser_failures} → SUCCESS_DEGRADED")
    else:
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
            "state_uncertain": state_uncertain,
            "historical_unresolved_count": str(len(engine.historical_unresolved)),
            "historical_unresolved_kopecks": str(hist_sum),
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
            "state_uncertain": "0",
            "historical_unresolved_count": "0",
            "historical_unresolved_kopecks": "0",
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
