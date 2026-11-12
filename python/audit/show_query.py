"""Print the reviewer's report on one query (interface design, Section 5.1).

    .venv\\Scripts\\python python\\audit\\show_query.py Q-1a2b3c4d           # Markdown
    .venv\\Scripts\\python python\\audit\\show_query.py Q-1a2b3c4d --json    # the same report as JSON

The reference is `Q-` plus the first characters of the query id (a prefix is refused if it matches more than
one row), a full query id, or a record id. The report is the one the local viewer (`app/review.py`) shows:
see `python/audit/report.py`. With `REVIEW_DATABASE_URL` set it connects read-only as the reviewer; without
it, it uses the project connection (the owner, locally) and says so on the first line of stderr. Nothing
here prints a connection string or a key.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from audit.migrate import DEFAULT_SCHEMA  # noqa: E402
from audit.report import (ReferenceError_, build_report, connect_review, fetch_query, render_markdown,  # noqa: E402
                          report_json, resolve_reference)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("reference", help="Q-xxxxxxxx, a query id or a record id")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    conn, read_only = connect_review()
    try:
        if not read_only:
            print("note: REVIEW_DATABASE_URL is not set; using the project connection (not read-only)",
                  file=sys.stderr)
        try:
            query_id = resolve_reference(conn, args.reference, args.schema)
        except ReferenceError_ as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        report = build_report(fetch_query(conn, query_id, args.schema))
        sys.stdout.reconfigure(encoding="utf-8")
        print(report_json(report) if args.json else render_markdown(report))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
