"""Run the 180-day raw-evidence retention job once (Task 4, step 6).

    RETENTION_DATABASE_URL=postgresql://<retention login>@host/db \\
        uv run python scripts/run_retention.py [--batch-size 500] [--max-batches 100]
    uv run python scripts/run_retention.py --dry-run   # count due rows, change nothing

RETENTION_DATABASE_URL must name a login that holds ONLY evidence_retention; the job
refuses a login that can read raw evidence content. Prints one JSON line with counts
(never row content or digests). Exit codes for a scheduler:

    0  complete: every row due at the cutoff is now a tombstone
    3  incomplete: stopped at --max-batches or skipped rows locked by another run;
       safe to run again
    1  failed: refused, or a database error; batches committed before it are kept and
       the job is safe to retry
    2  not configured

This script is an entry point only. Nothing schedules it; see
docs/operations/evidence_retention_runbook.md.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.config import get_settings
from nyc_apartment_agent.storage.db import make_engine
from nyc_apartment_agent.storage.retention import (
    COMPLETE,
    FAILED,
    INCOMPLETE,
    count_due,
    run_retention,
    sanitized_error,
)

EXIT = {COMPLETE: 0, INCOMPLETE: 3}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    secret = get_settings().retention_database_url
    if secret is None:
        print(
            json.dumps({"status": "not_configured", "error": "RETENTION_DATABASE_URL is not set"})
        )
        return 2
    engine = make_engine(secret.get_secret_value())
    try:
        if args.dry_run:
            try:
                print(json.dumps({"status": "dry_run", "due": count_due(engine)}))
            except DBAPIError as exc:
                print(json.dumps({"status": FAILED, "error": sanitized_error(exc)}))
                return 1
            return 0
        report = run_retention(engine, batch_size=args.batch_size, max_batches=args.max_batches)
    finally:
        engine.dispose()
    print(json.dumps(report.as_log()))
    return EXIT.get(report.status, 1)


if __name__ == "__main__":
    sys.exit(main())
