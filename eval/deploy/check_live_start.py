r"""After a deploy: the latest hosted start-up record is clean and names the approved commit
(deployment design Section 3.2; free; run as the reviewer).

    .venv\Scripts\python eval\deploy\with_hosted.py review eval\deploy\check_live_start.py --commit <sha>
    .venv\Scripts\python eval\deploy\with_hosted.py review eval\deploy\check_live_start.py --commit <sha> --url <live url>

The newest `app_starts` row with `deployment = 'hosted'` must have `outcome = 'ok'`, empty `problems`,
`config_sha256` equal to the pinned Phase 7 value and `git_commit` equal to the approved commit (a full hash, or
a prefix of at least 7 characters). A start row is written only when the first browser session runs the script,
so with `--url` this opens the live URL once in the default browser and then looks for the row for up to
`--wait` seconds (default 180), every 5 seconds. Without `--url` it reads once.

Exit 0: the row is clean and matches. 1: a row was found and something differs (each difference is named). 2: no
matching row yet, or the connection failed. Nothing is printed except the checks: no connection string, no
password and no question text. It reads only `app_starts` (the reviewer may).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

P7_CONFIG_SHA256 = "e2f61fe680759c0778719c01dfc30d2a463d6f484f54e8f7d7989e5a02ceec25"  # = interface.startup
MIN_COMMIT_CHARS = 7
POLL_SECONDS = 5.0
DEFAULT_WAIT_SECONDS = 180.0
_HEX = re.compile(r"^[0-9a-f]+$")


@dataclass(frozen=True)
class Start:
    start_id: int
    started_at: object
    outcome: str
    problems: list
    config_sha256: str
    git_commit: str | None
    app_version: str | None = None
    python_version: str | None = None
    streamlit_version: str | None = None


def commit_matches(recorded: str | None, approved: str) -> bool:
    approved = approved.strip().lower()
    if not recorded or len(approved) < MIN_COMMIT_CHARS or not _HEX.match(approved):
        return False
    return recorded.lower().startswith(approved)


def latest_hosted_start(conn, schema: str = "audit") -> Start | None:
    from psycopg import sql  # noqa: PLC0415

    row = conn.execute(
        sql.SQL("SELECT start_id, started_at, outcome, problems, config_sha256, git_commit, app_version, python_version, "
                "streamlit_version FROM {} WHERE deployment = 'hosted' ORDER BY start_id DESC LIMIT 1").format(
            sql.Identifier(schema, "app_starts"))).fetchone()
    return None if row is None else Start(row[0], row[1], row[2], list(row[3] or []), row[4], row[5], row[6], row[7], row[8])


def problems_of(start: Start, commit: str) -> list[str]:
    """What differs between the latest hosted start and what a clean deploy of `commit` records."""
    out = []
    if start.outcome != "ok":
        out.append(f"outcome is {start.outcome}, expected ok")
    if start.problems:
        out.append("problems recorded: " + ", ".join(start.problems))
    if start.config_sha256 != P7_CONFIG_SHA256:
        out.append("config_sha256 is not the pinned Phase 7 value")
    if not commit_matches(start.git_commit, commit):
        out.append("git_commit does not match the approved commit")
    return out


def find_start(conn, commit: str, *, wait: float = 0.0, sleep=time.sleep, clock=time.monotonic,
               poll: float = POLL_SECONDS) -> Start | None:
    """The newest hosted row recording `commit`; polls until `wait` seconds have passed. A newer hosted row for
    another commit is not a match: the one for this commit is what the deploy must have written."""
    deadline = clock() + wait
    while True:
        start = latest_hosted_start(conn)
        if start is not None and commit_matches(start.git_commit, commit):
            return start
        if clock() >= deadline:
            return start
        sleep(poll)


def main(argv: list[str] | None = None, *, connect=None, open_url=webbrowser.open, sleep=time.sleep,
         clock=time.monotonic) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", required=True, help="the approved commit (full hash or at least 7 characters)")
    parser.add_argument("--url", help="open this live URL once so the first session writes its start row")
    parser.add_argument("--wait", type=float, help="seconds to look for the row (default 180 with --url, else 0)")
    args = parser.parse_args(argv)
    wait = args.wait if args.wait is not None else (DEFAULT_WAIT_SECONDS if args.url else 0.0)
    if len(args.commit.strip()) < MIN_COMMIT_CHARS or not _HEX.match(args.commit.strip().lower()):
        print(f"--commit must be a hexadecimal commit of at least {MIN_COMMIT_CHARS} characters")
        return 2
    if connect is None:
        from audit.report import connect_review  # noqa: PLC0415

        def connect():
            conn, read_only = connect_review()
            if not read_only:
                conn.close()
                raise RuntimeError("REVIEW_DATABASE_URL is not set: run this through with_hosted.py review")
            return conn
    if args.url:
        open_url(args.url)
        print("opened the live URL once; waiting for the start row")
    try:
        with connect() as conn:
            start = find_start(conn, args.commit, wait=wait, sleep=sleep, clock=clock)
    except Exception as exc:  # noqa: BLE001 - the type only: a message can carry a host or a user name
        print(f"check_live_start.py: could not read the start record ({type(exc).__name__})")
        return 2
    if start is None:
        print("no hosted start row yet: open the live URL in a browser once, then run this again")
        return 2
    if not commit_matches(start.git_commit, args.commit):
        print(f"the latest hosted start (id {start.start_id}) records a different commit: the approved deploy has not "
              "started yet, or the wrong commit is live")
        return 2
    found = problems_of(start, args.commit)
    print(f"hosted start id {start.start_id}: outcome {start.outcome}, python {start.python_version}, streamlit "
          f"{start.streamlit_version}, commit {(start.git_commit or '')[:12]}")
    if found:
        for line in found:
            print("  PROBLEM: " + line)
        return 1
    print("clean: ok, no problems, pinned config, approved commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
