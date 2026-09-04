from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kudir_proto.csv_io import write_kv
from kudir_proto.engine import ProtoError, run_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Прототип КУДиР: читает CSV выгрузки 1С и пишет kudir_result.csv"
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="Каталог обмена (manifest.csv, payments.csv, ...)",
    )
    parser.add_argument(
        "--scoring",
        default="",
        help="Путь к scoring.yaml (по умолчанию config/scoring.yaml рядом с проектом)",
    )
    args = parser.parse_args(argv)

    exchange = Path(args.dir)
    project_root = Path(__file__).resolve().parents[2]
    scoring = Path(args.scoring) if args.scoring else project_root / "config" / "scoring.yaml"

    try:
        status = run_directory(exchange, scoring)
        print(status)
        return 0
    except ProtoError as exc:
        write_kv(
            exchange / "run_status.csv",
            {
                "run_id": "",
                "status": "FAILED",
                "tax_ready": "0",
                "unresolved_debt_kopecks": "0",
                "unresolved_count": "0",
                "parser_available": "0",
                "schema_version": "",
                "scoring_hash": "",
                "error": str(exc),
            },
        )
        result = exchange / "kudir_result.csv"
        if result.exists():
            result.unlink()
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        write_kv(
            exchange / "run_status.csv",
            {
                "run_id": "",
                "status": "FAILED",
                "tax_ready": "0",
                "unresolved_debt_kopecks": "0",
                "unresolved_count": "0",
                "parser_available": "0",
                "schema_version": "",
                "scoring_hash": "",
                "error": str(exc),
            },
        )
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
