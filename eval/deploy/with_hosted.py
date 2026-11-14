r"""Run one script with one hosted connection line from `.env.hosted` (deployment design Sections 2.4 and 2.7, D-196).

    .venv\Scripts\python eval\deploy\with_hosted.py owner  eval\reproduce\reproduce.py --profile clean --only A4,A5,A6
    .venv\Scripts\python eval\deploy\with_hosted.py app    eval\deploy\compare_retrieval.py
    .venv\Scripts\python eval\deploy\with_hosted.py review eval\deploy\check_live_start.py --commit <sha>

`.env.hosted` is at the repository root. It is git-ignored (the `.env.*` rule), never enters the image
(allow-list) and holds `DATABASE_URL_OWNER`, `DATABASE_URL_APP` and `REVIEW_DATABASE_URL`, which Kene types.

The child process gets exactly one of them, under the name its scripts read:

    owner   DATABASE_URL_OWNER     as DATABASE_URL
    app     DATABASE_URL_APP       as DATABASE_URL
    review  REVIEW_DATABASE_URL    as REVIEW_DATABASE_URL

The other lines never reach the child. Nothing is printed except the child's own output; no value is shown,
and an error names the variable, never its value. The child's exit code is this script's exit code.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env.hosted"
# role -> (line in .env.hosted, name the child reads)
ROLES = {
    "owner": ("DATABASE_URL_OWNER", "DATABASE_URL"),
    "app": ("DATABASE_URL_APP", "DATABASE_URL"),
    "review": ("REVIEW_DATABASE_URL", "REVIEW_DATABASE_URL"),
}


class HostedEnvError(RuntimeError):
    """The file or the line is missing; the message never contains a value."""


def parse_env_text(text: str) -> dict[str, str]:
    """`NAME=value` lines; blank lines and `#` comments are skipped; one pair of matching quotes is removed."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def child_environment(role: str, env_text: str, base: dict[str, str]) -> dict[str, str]:
    """`base` plus the one connection line this role needs. Raises HostedEnvError if it is missing or empty."""
    if role not in ROLES:
        raise HostedEnvError(f"role must be one of {', '.join(ROLES)}")
    line, target = ROLES[role]
    value = parse_env_text(env_text).get(line, "")
    if not value:
        raise HostedEnvError(f"{line} is missing or empty in .env.hosted")
    env = dict(base)
    for _, name in ROLES.values():  # a connection the parent already holds must not leak into the child
        env.pop(name, None)
    env[target] = value
    return env


def main(argv: list[str] | None = None, *, env_file: Path = ENV_FILE, base: dict[str, str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2 or args[0] not in ROLES:
        print(f"usage: with_hosted.py <{'|'.join(ROLES)}> <script.py> [args ...]")
        return 2
    role, script, rest = args[0], Path(args[1]), args[2:]
    if not script.is_file():
        print(f"with_hosted.py: {script} is not a file")
        return 2
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        print("with_hosted.py: .env.hosted is missing (copy the three lines from your password manager into it)")
        return 2
    try:
        env = child_environment(role, text, dict(os.environ if base is None else base))
    except HostedEnvError as exc:
        print(f"with_hosted.py: {exc}")
        return 2
    return subprocess.run([sys.executable, str(script), *rest], env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
