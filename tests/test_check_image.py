"""The no-secret image check and the image definition files (deployment design Sections 1.1 to 1.7; D-193).

Free; no Docker. The functions of `eval/deploy/check_image.py` run on fakes (an in-memory tar, a made-up
`.dockerignore`, lists of ENV lines); the script itself, which builds the image, is opt-in and was run by hand.
The static cases pin the Dockerfile and `.dockerignore` to the design."""
import importlib.util
import io
import re
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_image", ROOT / "eval" / "deploy" / "check_image.py")
ci = importlib.util.module_from_spec(_spec)
sys.modules["check_image"] = ci
_spec.loader.exec_module(ci)

RULES = ci.parse_dockerignore((ROOT / ".dockerignore").read_text(encoding="utf-8"))


def make_tar(members: dict[str, bytes | tuple[str, str]]) -> bytes:
    """A tar like `tar -C /srv taxy`: str/bytes value = file contents; ("link", target) = symlink."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        root = tarfile.TarInfo("taxy")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for path, content in members.items():
            info = tarfile.TarInfo(f"taxy/{path}")
            if isinstance(content, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, content[1]
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


# ---------- the allow-list ----------

@pytest.mark.parametrize("path", [
    "requirements.txt", "requirements.lock.txt", ".streamlit/config.toml", "app/main.py", "python/embed/load.py",
    "data/curated/rates.yml", "data/derived/chunks/chunks.jsonl", "data/derived/section_map.csv",
    "eval/tools/run_exit_check.py", "eval/tools/config_p7.json", "eval/guardrails/config_g2_v4.json",
    "eval/retrieval/config_v3.json", "eval/retrieval/config_final.json", "eval/retrieval/run_eval.py"])
def test_files_the_start_up_check_needs_are_allowed(path):
    assert ci.allowed(path, RULES)


@pytest.mark.parametrize("path", [
    ".env", ".env.example", ".streamlit/secrets.toml", "CLAUDE.md", ".agents/notes.md", ".git/config",
    "docs/README.md", "tests/test_app.py", "logs/x.log", "data/raw/a.pdf", "data/cache/embeddings/x",
    "data/derived/ocr/a.json", "data/derived/chunks_extra.csv", "eval/e2e/e2e_v1.jsonl", "eval/retrieval/questions_v1.jsonl",
    "eval/retrieval/configs/openai_vector.json", "app/review.py", "python/ingest/fetch_raw.py",
    "data/curated/ntaa_nrs_proofread/p1.yml", "app/__pycache__/main.cpython-312.pyc", "README.md", "Dockerfile"])
def test_files_outside_the_list_are_not_allowed(path):
    assert not ci.allowed(path, RULES)


def test_dockerignore_starts_with_a_star_and_names_config_v3():
    rules = [p for _, p in RULES]
    assert RULES[0] == (False, "*")
    assert "eval/retrieval/config_v3.json" in rules  # the gap the scratch build found (Section 1.3)


def test_tracked_files_the_list_allows_all_exist_in_git_or_the_tree():
    """Every allow-listed exact path exists, so a rename cannot silently empty the image."""
    for negate, pattern in RULES:
        if negate and not pattern.endswith("/"):
            assert (ROOT / pattern).exists(), pattern


# ---------- check 2: forbidden paths ----------

@pytest.mark.parametrize("path,reason", [
    (".env", "env"), (".env.hosted", "env"), (".streamlit/secrets.toml", "secrets.toml"),
    ("deploy/secrets.toml", "secrets.toml"), ("CLAUDE.md", "forbidden name"), (".claude/settings.json", "forbidden name"),
    (".git/HEAD", "forbidden name"), ("logs/a", "forbidden path"), ("data/raw/a", "forbidden path"),
    ("data/cache/a", "forbidden path"), ("tests/test_x.py", "forbidden path"), ("docs/a.md", "forbidden path"),
    ("eval/e2e/a.json", "forbidden path"), ("app/review.py", "forbidden path")])
def test_forbidden_paths_are_named(path, reason):
    assert reason in ci.forbidden_reason(path)


def test_the_secrets_link_is_the_one_allowed_secrets_file():
    assert ci.forbidden_reason(".streamlit/secrets.toml", is_link_to="/etc/secrets/secrets.toml") is None
    assert ci.forbidden_reason(".streamlit/secrets.toml", is_link_to="/other") is not None
    assert ci.forbidden_reason("app/main.py") is None
    assert ci.forbidden_reason("data/curated/rates.yml") is None
    assert ci.forbidden_reason(".gitignore") is None  # only the exact name .git is forbidden


# ---------- checks 1 to 3: the file tree ----------

CLEAN = {"app/main.py": b"print('x')\n", ".streamlit/config.toml": b"[browser]\n",
         ".streamlit/secrets.toml": ("link", "/etc/secrets/secrets.toml")}
TRACKED = {"app/main.py", ".streamlit/config.toml"}


def test_a_clean_tree_has_no_findings_and_counts_files():
    findings, count = ci.audit_tar(make_tar(CLEAN), TRACKED, RULES)
    assert findings == []
    assert count == 3  # two files and the link


def test_an_untracked_file_is_found():
    findings, _ = ci.audit_tar(make_tar({**CLEAN, "app/extra.py": b"x=1\n"}), TRACKED, RULES)
    assert findings == ["[1] app/extra.py: not in git ls-files"]


def test_a_file_outside_the_allow_list_is_found():
    findings, _ = ci.audit_tar(make_tar({**CLEAN, "notes.md": b"x\n"}), TRACKED | {"notes.md"}, RULES)
    assert findings == ["[1] notes.md: not matched by the allow-list"]


def test_a_forbidden_file_is_found_with_its_rule():
    findings, _ = ci.audit_tar(make_tar({**CLEAN, ".env": b"A=1\n"}), TRACKED | {".env"}, RULES)
    assert "[2] .env: env file" in findings


def test_another_link_is_found():
    findings, _ = ci.audit_tar(make_tar({**CLEAN, "app/link": ("link", "/etc/passwd")}), TRACKED, RULES)
    assert findings == ["[1] app/link: unexpected link"]


def test_a_wrong_secrets_link_is_found_twice_over():
    findings, _ = ci.audit_tar(make_tar({**CLEAN, ".streamlit/secrets.toml": ("link", "/x")}), TRACKED, RULES)
    assert any(f.startswith("[2] .streamlit/secrets.toml") for f in findings)
    assert any(f.startswith("[1] .streamlit/secrets.toml") for f in findings)


@pytest.mark.parametrize("name,text", [
    ("anthropic-key", "key = 'sk-ant-api03-" + "A" * 30 + "'"),
    ("openai-key", "OPENAI = 'sk-proj-" + "b" * 30 + "'"),
    ("pem-private-key", "-----BEGIN RSA PRIVATE KEY-----"),
    ("pem-private-key", "-----BEGIN PRIVATE KEY-----"),
    ("postgres-url-with-password", "DATABASE_URL = 'postgresql://user:hunter2@host:5432/db'"),
    ("postgres-url-with-password", "postgres://u:p@h/d"),
    ("client_secret", "client_secret = 'x'"),
    ("client_secret", "Client_Secret")])
def test_secret_shaped_strings_are_found_by_name_and_line(name, text):
    hits = ci.scan_secrets(b"line one\n" + text.encode() + b"\n")
    assert (2, name) in hits


def test_a_finding_never_contains_the_matched_value():
    secret = "sk-ant-api03-" + "Z" * 30
    findings, _ = ci.audit_tar(make_tar({**CLEAN, "app/main.py": f"k='{secret}'\n".encode()}), TRACKED, RULES)
    assert findings and all(secret not in f and "ZZZZZ" not in f for f in findings)
    assert findings == ["[3] app/main.py:1: anthropic-key", "[3] app/main.py:1: openai-key"]


@pytest.mark.parametrize("text", ["postgresql://localhost/db", "postgresql://user@host/db", "sk-short",
                                  "the secret is kept elsewhere", "BEGIN PRIVATE", "DATABASE_URL"])
def test_ordinary_text_is_not_flagged(text):
    assert ci.scan_secrets(text.encode()) == []


# ---------- checks 4 and 5: environment and history ----------

BASE_ENV = ["PATH=/usr/local/bin:/usr/bin", "LANG=C.UTF-8", "GPG_KEY=abc", "PYTHON_VERSION=3.12.16"]
GOOD_ENV = BASE_ENV + [f"{k}={v}" for k, v in ci.EXPECTED_ENV.items()]


def test_the_expected_environment_is_clean():
    assert ci.check_env(GOOD_ENV, BASE_ENV) == []


def test_an_extra_variable_is_found():
    assert ci.check_env(GOOD_ENV + ["LLM_API_KEY=x"], BASE_ENV) == ["[5] env LLM_API_KEY: not a base-image or Section 1.4 variable"]


def test_a_changed_or_missing_variable_is_found():
    changed = [e for e in GOOD_ENV if not e.startswith("TAXY_DEPLOYMENT")] + ["TAXY_DEPLOYMENT=local"]
    assert ci.check_env(changed, BASE_ENV) == ["[5] env TAXY_DEPLOYMENT: not the value in Section 1.4"]
    assert ci.check_env(BASE_ENV, BASE_ENV) == [f"[5] env {n}: missing" for n in ci.EXPECTED_ENV]
    assert ci.check_env([e.replace("3.12.16", "3.13") for e in GOOD_ENV], BASE_ENV) == \
        ["[5] env PYTHON_VERSION: differs from the base image"]


def test_history_names_reads_env_arg_and_label_not_run():
    lines = ["ENV PYTHONUNBUFFERED=1 HOME=/tmp", "ARG BUILD_TOKEN", "ARG API_KEY=abc", "LABEL org.example.key=v",
             "RUN /bin/sh -c export SECRET_X=1", "/bin/sh -c #(nop)  ENV LEGACY_PASSWORD=x", "CMD [\"python3\"]"]
    assert ci.history_names(lines) == {"PYTHONUNBUFFERED", "HOME", "BUILD_TOKEN", "API_KEY", "org.example.key",
                                       "LEGACY_PASSWORD"}


def test_secret_looking_names_added_by_the_build_are_found_but_base_names_are_exempt():
    added = {"HOME", "GPG_KEY", "DB_PASSWORD", "app_secret", "DATABASE_URL", "TAXY_DEPLOYMENT"}
    assert ci.check_names(added, {"GPG_KEY"}) == [f"[4] {n}: secret-looking ENV/ARG/LABEL name"
                                                  for n in ("DATABASE_URL", "DB_PASSWORD", "app_secret")]


# ---------- the image definition files ----------

DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_the_base_image_is_pinned_by_digest():
    assert re.search(r"^FROM python:3\.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b$",
                     DOCKERFILE, re.MULTILINE)
    assert ci.base_image_ref(ROOT / "Dockerfile").startswith("python:3.12-slim-trixie@sha256:")


def test_the_dockerfile_follows_section_1_4():
    assert "WORKDIR /srv/taxy" in DOCKERFILE
    assert "groupadd --gid 1000 taxy" in DOCKERFILE and "useradd --uid 1000 --gid 1000" in DOCKERFILE
    assert re.search(r"^USER taxy$", DOCKERFILE, re.MULTILINE)
    assert "ln -s /etc/secrets/secrets.toml /srv/taxy/.streamlit/secrets.toml" in DOCKERFILE
    assert "EXPOSE 10000" in DOCKERFILE
    env_block = re.search(r"^ENV (.*?)\n\n", DOCKERFILE, re.MULTILINE | re.DOTALL).group(1)
    declared = dict(re.findall(r"(\w+)=(\S+)", env_block))
    assert declared == ci.EXPECTED_ENV


def test_the_command_carries_the_hosted_flags_and_nothing_secret():
    cmd = re.search(r"^CMD (\[.*\])$", DOCKERFILE, re.MULTILINE).group(1)
    for flag in ("exec streamlit run app/main.py", "--server.port=${PORT:-10000}", "--server.address=0.0.0.0",
                 "--server.headless=true", "--server.fileWatcherType=none", "--client.showErrorDetails=none"):
        assert flag in cmd, flag
    assert not re.search(r"^(ARG|LABEL) ", DOCKERFILE, re.MULTILINE)  # no build arguments (D-198)


def test_the_streamlit_config_is_shipped_unchanged():
    assert ci.allowed(".streamlit/config.toml", RULES)
    instructions = [line for line in DOCKERFILE.splitlines() if re.match(r"(RUN|COPY|ADD) ", line)]
    assert instructions and not any("config.toml" in line for line in instructions)  # copied with the project, never edited


def test_the_base_image_is_pulled_when_the_local_store_lacks_it(monkeypatch):
    """A build can use its own cache without leaving the base image in the local store (found in B1.2)."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args[1:3])
        if args[1:3] == ["image", "inspect"] and calls.count(["image", "inspect"]) == 1:
            raise RuntimeError("No such image")
        return type("R", (), {"stdout": '[{"Config": {"Env": []}}]'})()

    monkeypatch.setattr(ci, "run", fake_run)
    assert ci.inspect_base("python:3.12-slim-trixie@sha256:abc") == {"Config": {"Env": []}}
    assert calls == [["image", "inspect"], ["pull", "python:3.12-slim-trixie@sha256:abc"], ["image", "inspect"]]
    calls.clear()
    monkeypatch.setattr(ci, "run", lambda args, **kwargs: type("R", (), {"stdout": '[{"x": 1}]'})())
    assert ci.inspect_base("any") == {"x": 1}
