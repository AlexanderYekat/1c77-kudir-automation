from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kudir.engine import RunError, run_directory, write_failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="КУДиР matcher: читает CSV выгрузки 1С и пишет kudir_result.csv"
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="Каталог обмена exchange/<run_id>/",
    )
    parser.add_argument(
        "--scoring",
        default="",
        help="Путь к scoring.yaml (по умолчанию config/scoring.yaml)",
    )
    args = parser.parse_args(argv)

    exchange = Path(args.dir)
    project_root = Path(__file__).resolve().parents[2]
    scoring = Path(args.scoring) if args.scoring else project_root / "config" / "scoring.yaml"

    try:
        status = run_directory(exchange, scoring)
        print(status)
        return 0
    except RunError as exc:
        write_failed(exchange, str(exc))
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        write_failed(exchange, str(exc))
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
