"""No-secret image check (deployment design Section 1.7; D-193).

Builds the container image and fails on any of these:

1. a file under /srv/taxy that is not in `git ls-files` at the commit, or not matched by the allow-list
   in `.dockerignore`;
2. a forbidden path (`.env*`, `secrets.toml` other than the link, the agent files, `.git`, `logs`,
   `data/raw`, `data/cache`, `tests`, `docs`, `eval/e2e`, `app/review.py`);
3. a secret-shaped string anywhere under /srv/taxy (API-key prefixes of both providers, PEM private-key
   headers, a `postgresql://user:password@` URL, `client_secret`);
4. an ENV, ARG or LABEL name added by this build (from `docker history --no-trunc` or `docker inspect`)
   that contains KEY, SECRET, PASSWORD or DATABASE_URL;
5. `Config.Env` holding anything beyond the base image's variables and the ones the Dockerfile sets
   (Section 1.4).

Findings name a path, a line number and a pattern name. A matched value is never printed. The script needs
Docker and takes about 3 minutes, so it is opt-in and not part of the default suite. It prints the image
size and the file count, and removes the image unless `--keep` is given (the base image is left alone, and is pulled if the local store lacks it).

    python eval/deploy/check_image.py                 # clean checkout of HEAD (git archive)
    python eval/deploy/check_image.py --rev <commit>  # a named commit
    python eval/deploy/check_image.py --worktree      # the working tree, before anything is committed

Exit code 0: clean. 1: findings. 2: a Docker or git command failed.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_PARENT = "/srv"
APP_NAME = "taxy"
APP_DIR = f"{APP_PARENT}/{APP_NAME}"
SECRETS_LINK = ".streamlit/secrets.toml"
SECRETS_LINK_TARGET = "/etc/secrets/secrets.toml"
DEFAULT_TAG = "taxy-image-check:latest"

# Section 1.4: the variables the Dockerfile sets on top of the base image.
EXPECTED_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PIP_NO_CACHE_DIR": "1",
    "HOME": "/tmp",
    "TAXY_DEPLOYMENT": "hosted",
}
SECRET_NAME_PARTS = ("KEY", "SECRET", "PASSWORD", "DATABASE_URL")

FORBIDDEN_PREFIXES = ("logs", "data/raw", "data/cache", "tests", "docs", "eval/e2e", "app/review.py")
FORBIDDEN_NAMES = {"CLAUDE.md", "CLAUDE.local.md", "GEMINI.md", ".claude", ".agents", ".gemini", ".git"}

SECRET_PATTERNS = {
    "anthropic-key": re.compile(rb"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai-key": re.compile(rb"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"),
    "pem-private-key": re.compile(rb"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    "postgres-url-with-password": re.compile(rb"postgres(?:ql)?(?:\+\w+)?://[^\s:/@'\"]+:[^\s@'\"]+@"),
    "client_secret": re.compile(rb"client_secret", re.IGNORECASE),
}


# ---------- allow-list ----------

def parse_dockerignore(text: str) -> list[tuple[bool, str]]:
    """Rules in file order as (is_exception, pattern); comments and blank lines dropped."""
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        rules.append((negate, line[1:] if negate else line))
    return rules


def _matches(path: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.startswith("**/"):
        name = pattern[3:]
        return name in path.split("/")
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return path == pattern or path.startswith(pattern + "/")


def allowed(path: str, rules: list[tuple[bool, str]]) -> bool:
    """True if the last rule that matches `path` is an exception (a `!` line). No rule matches: not allowed
    only when the list starts with `*`, which this project's file does; a list without it allows."""
    state = True
    for negate, pattern in rules:
        if _matches(path, pattern):
            state = negate
    return state


# ---------- checks on the image's file tree (one tar stream) ----------

def forbidden_reason(path: str, *, is_link_to: str | None = None) -> str | None:
    """Why `path` (relative to /srv/taxy) must not be in the image, or None."""
    if path == SECRETS_LINK and is_link_to == SECRETS_LINK_TARGET:
        return None
    parts = path.split("/")
    for part in parts:
        if part == ".env" or part.startswith(".env."):
            return "env file"
        if part == "secrets.toml":
            return "secrets.toml (not the link)"
        if part in FORBIDDEN_NAMES:
            return f"forbidden name {part}"
    for prefix in FORBIDDEN_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return f"forbidden path {prefix}"
    return None


def scan_secrets(data: bytes) -> list[tuple[int, str]]:
    """(line number, pattern name) for every secret-shaped match. The matched text is not returned."""
    hits = []
    for name, pattern in SECRET_PATTERNS.items():
        for m in pattern.finditer(data):
            hits.append((data.count(b"\n", 0, m.start()) + 1, name))
    return sorted(set(hits))


def audit_tar(tar_bytes: bytes, tracked: set[str], rules: list[tuple[bool, str]]) -> tuple[list[str], int]:
    """Checks 1 to 3 on a tar of the app directory (member names start with the directory name).
    Returns (findings, file count)."""
    findings: list[str] = []
    files = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar:
            path = member.name.split("/", 1)[1] if "/" in member.name else ""
            if not path:
                continue  # the directory itself
            link = member.linkname if member.issym() else None
            reason = forbidden_reason(path, is_link_to=link)
            if reason:
                findings.append(f"[2] {path}: {reason}")
            if member.issym():
                if not (path == SECRETS_LINK and link == SECRETS_LINK_TARGET):
                    findings.append(f"[1] {path}: unexpected link")
                files += 1
                continue
            if member.isdir():
                continue
            files += 1
            if path not in tracked:
                findings.append(f"[1] {path}: not in git ls-files")
            if not allowed(path, rules):
                findings.append(f"[1] {path}: not matched by the allow-list")
            handle = tar.extractfile(member)
            if handle is not None:
                for line, name in scan_secrets(handle.read()):
                    findings.append(f"[3] {path}:{line}: {name}")
    return findings, files


# ---------- checks on the image's configuration ----------

def env_to_dict(env: list[str] | None) -> dict[str, str]:
    return dict(item.split("=", 1) for item in (env or []))


def check_env(image_env: list[str] | None, base_env: list[str] | None) -> list[str]:
    """Check 5: the image's variables are the base image's (unchanged) plus the ones in EXPECTED_ENV."""
    image, base = env_to_dict(image_env), env_to_dict(base_env)
    findings = []
    for name, value in image.items():
        if name in EXPECTED_ENV:
            if value != EXPECTED_ENV[name]:
                findings.append(f"[5] env {name}: not the value in Section 1.4")
        elif name not in base:
            findings.append(f"[5] env {name}: not a base-image or Section 1.4 variable")
        elif name != "PATH" and value != base[name]:
            findings.append(f"[5] env {name}: differs from the base image")
    for name in EXPECTED_ENV:
        if name not in image:
            findings.append(f"[5] env {name}: missing")
    return findings


_HISTORY_KEYWORD = re.compile(r"(?:^|\s)(ENV|ARG|LABEL)\s+(.*)$")
_HISTORY_NAME = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_.\-]*)=")


def history_names(lines: list[str]) -> set[str]:
    """Names set by ENV, ARG and LABEL lines in `docker history --no-trunc`. RUN lines are not read."""
    names: set[str] = set()
    for line in lines:
        m = _HISTORY_KEYWORD.search(line.strip())
        if not m:
            continue
        keyword, rest = m.groups()
        found = _HISTORY_NAME.findall(rest)
        if keyword == "ARG" and not found:
            found = [rest.split()[0]] if rest.split() else []
        names.update(found)
    return names


def check_names(names: set[str], base_names: set[str]) -> list[str]:
    """Check 4: a name this build added that looks secret. Names the base image already carries are exempt
    (the Python image sets GPG_KEY, the public id of the release signing key)."""
    return [f"[4] {name}: secret-looking ENV/ARG/LABEL name"
            for name in sorted(names - base_names)
            if any(part in name.upper() for part in SECRET_NAME_PARTS)]


# ---------- Docker and git plumbing ----------

def run(args: list[str], *, cwd: Path | None = None, binary: bool = False) -> subprocess.CompletedProcess:
    text_args = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    result = subprocess.run(args, cwd=cwd, capture_output=True, **text_args)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", "replace") if binary else result.stderr
        raise RuntimeError(f"`{' '.join(args[:3])}` exited {result.returncode}: {err.strip()[-600:]}")
    return result


def base_image_ref(dockerfile: Path) -> str:
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*FROM\s+(\S+)", line, re.IGNORECASE)
        if m:
            return m.group(1)
    raise ValueError("no FROM line in the Dockerfile")


def tracked_files(rev: str | None) -> set[str]:
    """`git ls-files` at a commit, or (rev None) tracked plus untracked-but-not-ignored in the working tree."""
    if rev is None:
        out = run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT).stdout
    else:
        out = run(["git", "ls-tree", "-r", "--name-only", rev], cwd=ROOT).stdout
    return {line for line in out.splitlines() if line}


def export_commit(rev: str, dest: Path) -> None:
    archive = run(["git", "archive", "--format=tar", rev], cwd=ROOT, binary=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def inspect(ref: str) -> dict:
    return json.loads(run(["docker", "image", "inspect", ref]).stdout)[0]


def inspect_base(ref: str) -> dict:
    """Inspect the base image, pulling it first if the local image store does not hold it (a build can use its
    own cache without leaving the base image in the store). A pulled base is left in place."""
    try:
        return inspect(ref)
    except RuntimeError:
        run(["docker", "pull", ref])
        return inspect(ref)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rev", default="HEAD", help="commit to build from a clean checkout (default HEAD)")
    parser.add_argument("--worktree", action="store_true", help="build from the working tree instead of a commit")
    parser.add_argument("--keep", action="store_true", help="keep the built image")
    parser.add_argument("--tag", default=DEFAULT_TAG)
    args = parser.parse_args()

    findings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="taxy-image-check-") as tmp:
        if args.worktree:
            context, tracked, label = ROOT, tracked_files(None), "working tree"
        else:
            context = Path(tmp) / "checkout"
            context.mkdir()
            export_commit(args.rev, context)
            tracked, label = tracked_files(args.rev), f"commit {args.rev}"
        rules = parse_dockerignore((context / ".dockerignore").read_text(encoding="utf-8"))
        base_ref = base_image_ref(context / "Dockerfile")
        print(f"building {args.tag} from the {label} ...", flush=True)
        started = time.monotonic()
        try:
            run(["docker", "build", "--tag", args.tag, str(context)])
        except RuntimeError as exc:
            print(f"BUILD FAILED: {exc}")
            return 2
        build_seconds = time.monotonic() - started
        try:
            info = inspect(args.tag)
            base = inspect_base(base_ref)
            tar_bytes = run(["docker", "run", "--rm", "--entrypoint", "tar", args.tag, "-cf", "-", "-C",
                             APP_PARENT, APP_NAME], binary=True).stdout
            history = run(["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", args.tag]).stdout
            base_history = run(["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", base_ref]).stdout
        except RuntimeError as exc:
            print(f"DOCKER COMMAND FAILED: {exc}")
            if not args.keep:
                subprocess.run(["docker", "rmi", args.tag], capture_output=True)
            return 2

        tree_findings, file_count = audit_tar(tar_bytes, tracked, rules)
        findings += tree_findings
        config = info["Config"]
        base_config = base["Config"]
        names = history_names(history.splitlines()) | set(env_to_dict(config.get("Env"))) | set(config.get("Labels") or {})
        base_names = (history_names(base_history.splitlines()) | set(env_to_dict(base_config.get("Env")))
                      | set(base_config.get("Labels") or {}))
        findings += check_names(names, base_names)
        findings += check_env(config.get("Env"), base_config.get("Env"))

        print(f"image {args.tag}: {info['Size']:,} bytes ({info['Size'] / 1e6:.0f} MB), id {info['Id']}")
        print(f"files under {APP_DIR}: {file_count}; build {build_seconds:.0f} s; base {base_ref}")
        if not args.keep:
            subprocess.run(["docker", "rmi", args.tag], capture_output=True)
            print(f"removed image {args.tag}")

    if findings:
        print(f"\n{len(findings)} finding(s):")
        for finding in findings:
            print("  " + finding)
        return 1
    print("clean: checks 1 to 5 found nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
