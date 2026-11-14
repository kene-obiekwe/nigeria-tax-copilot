r"""Run one SQL file on the project database as the connected role (deployment design Section 2.5).

    .venv\Scripts\python eval\deploy\with_hosted.py owner deploy\apply_sql.py deploy\supabase\0001_hosted_roles.sql

The connection comes from `check_pgvector.connect()`: `DATABASE_URL` when set (which `with_hosted.py` sets for
the child process from the named line of `.env.hosted`), else the local `POSTGRES_*` settings. The file runs as
one script in one transaction, so it applies completely or not at all; the files in `deploy/supabase/` are
idempotent, so a second run changes nothing. Nothing is printed except the file name and "applied". No
connection string, password or statement text is printed or logged.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))


def apply_sql(conn, text: str) -> None:
    """Run `text` (one or more statements, no parameters) in the connection's current transaction."""
    conn.execute(text)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: apply_sql.py <file.sql>")
        return 2
    path = Path(args[0])
    if not path.is_file() or path.suffix != ".sql":
        print("apply_sql.py: not a .sql file")
        return 2
    text = path.read_text(encoding="utf-8")
    from check_pgvector import connect  # noqa: PLC0415

    try:
        with connect() as conn:
            with conn.transaction():
                apply_sql(conn, text)
    except Exception as exc:  # noqa: BLE001 - the type only: a message can carry a statement or a host
        print(f"apply_sql.py: {path.name} failed ({type(exc).__name__}); nothing was changed")
        return 1
    print(f"applied {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
