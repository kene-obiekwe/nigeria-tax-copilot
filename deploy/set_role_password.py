r"""Set the password of one hosted login role, typed by you (deployment design Section 2.5, D-196).

    .venv\Scripts\python eval\deploy\with_hosted.py owner deploy\set_role_password.py app
    .venv\Scripts\python eval\deploy\with_hosted.py owner deploy\set_role_password.py review

Run it in your own terminal, once for each role. It asks for the password with `getpass`, so nothing is
echoed, and asks again to confirm. The password is never an argument, never read from the environment or a
file, never printed, logged or stored. It computes the SCRAM-SHA-256 verifier here (salted, 4096 rounds) and
sends `ALTER ROLE ... PASSWORD 'SCRAM-SHA-256$...'`, so the clear password never reaches the server's
statement log or the SQL editor history. It prints only "password set for <role>".

The connection is the owner's (`with_hosted.py owner` sets it). The roles are created by
`deploy/supabase/0001_hosted_roles.sql`, without a password.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

ROLES = {"app": "taxy_app_login", "review": "taxy_review_login"}
MIN_LENGTH = 16
SCRAM_ITERATIONS = 4096


def scram_verifier(password: str, *, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """The PostgreSQL SCRAM-SHA-256 verifier of `password`:
    `SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>` (RFC 5802 and 7677, as PostgreSQL stores it).
    The password is normalised with NFKC, as SASLprep does for ordinary text."""
    salt = os.urandom(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", unicodedata.normalize("NFKC", password).encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


class PasswordError(ValueError):
    """The password was not accepted; the message never contains it."""


def ask_password(prompt=getpass.getpass) -> str:
    first = prompt("New password (nothing is shown): ")
    if len(first) < MIN_LENGTH:
        raise PasswordError(f"the password must have at least {MIN_LENGTH} characters")
    if prompt("Type it again: ") != first:
        raise PasswordError("the two entries differ")
    return first


def set_password(conn, role: str, password: str) -> None:
    """`ALTER ROLE <role> PASSWORD '<verifier>'`; the clear password is not part of the statement."""
    from psycopg import sql  # noqa: PLC0415

    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    if not exists:
        raise LookupError(f"role {role} does not exist: run deploy/supabase/0001_hosted_roles.sql first")
    statement = sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(scram_verifier(password)))
    conn.execute(statement)


def main(argv: list[str] | None = None, *, prompt=getpass.getpass, connect=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ROLES:
        print("usage: set_role_password.py <app|review>   (the password is asked for; it is never an argument)")
        return 2
    role = ROLES[args[0]]
    try:
        password = ask_password(prompt)
    except PasswordError as exc:
        print(f"not set: {exc}")
        return 2
    if connect is None:
        from check_pgvector import connect  # noqa: PLC0415
    try:
        with connect() as conn:
            conn.autocommit = True
            set_password(conn, role, password)
    except LookupError as exc:
        print(f"not set: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - the type only: a message can carry a statement or a host
        print(f"not set ({type(exc).__name__})")
        return 1
    finally:
        password = ""
    print(f"password set for {role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
