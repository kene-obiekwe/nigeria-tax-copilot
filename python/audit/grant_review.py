r"""The reviewer's SELECT on the corpus tables: a separate, owner-run step (D-169).

    .venv\Scripts\python python\audit\grant_review.py            # dry run: prints the statements, changes nothing
    .venv\Scripts\python python\audit\grant_review.py --apply    # runs them as the owner, once

The audit migrations (0001 to 0003) touch only the audit schema, so the `taxy_review` role can read every
audit table but not the corpus tables in `public`. The viewer needs `corpus_chunks` for the chunk text of
each passage, so interface design Section 4.6 gives the reviewer SELECT on the corpus tables. D-169 keeps
that grant out of the migrations: this script is the one place that issues it, run by the owner (Kene)
when the viewer is first set up, and no test applies it to the real database. The tests apply the same
statements to a throwaway schema and a throwaway role only.

SELECT only: the reviewer gets no other privilege on these tables, and nothing on any other table. The
login user that joins `taxy_review`, and its password, are created by Kene; this script creates no role
and sets no password. It prints no connection string.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from psycopg import sql  # noqa: E402

from audit.migrate import DEFAULT_REVIEW_ROLE  # noqa: E402

CORPUS_TABLES = ("corpus_chunks", "chunk_embeddings", "discrepancy_register", "corpus_loads")


def grant_statements(review_role: str = DEFAULT_REVIEW_ROLE, schema: str = "public",
                     tables: tuple[str, ...] = CORPUS_TABLES) -> list[sql.Composed]:
    role = sql.Identifier(review_role)
    out = [sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), role)]
    out += [sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(sql.Identifier(schema, t), role) for t in tables]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="run the statements as the owner (default: print only)")
    parser.add_argument("--role", default=DEFAULT_REVIEW_ROLE)
    args = parser.parse_args(argv)
    statements = grant_statements(args.role)
    if not args.apply:
        print("Dry run. These statements would be run as the owner (use --apply to run them):")
        for s in statements:
            print(f"  {s.as_string(None)};")
        return 0
    from check_pgvector import connect
    with connect() as conn:
        for s in statements:
            conn.execute(s)
        for table in CORPUS_TABLES:
            ok = conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (args.role, f"public.{table}")).fetchone()[0]
            print(f"{args.role} can SELECT public.{table}: {ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
