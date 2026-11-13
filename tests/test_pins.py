"""Every file hash pinned by the shipped configurations equals the hash of the COMMITTED bytes (D-187, C-14).

A pin taken over a working copy is not portable: a clean clone checks out what git stores, and git can
change line endings. `data/curated/disclosure_policy.yml` was pinned over a CRLF working copy while git
stored LF, so every clean clone failed the start-up check. This test hashes the blob in git (the index,
which is the HEAD blob unless a change is staged), never the working copy.

Pins covered, by shipped configuration:

eval/tools/config_p7.json
  - base                      eval/guardrails/config_g2_v4.json
  - overrides.heading_corrections.sha256   data/curated/heading_corrections.yml
  - tools.calc_rates.sha256   data/curated/calc_rates.yml
  - exit_questions.sha256     eval/tools/exit_questions_v1.jsonl
eval/guardrails/config_g2_v4.json (the base of config_p7.json)
  - guardrails_policy_sha256  data/curated/guardrails_policy.yml
  - disclosure_policy_sha256  data/curated/disclosure_policy.yml
  - retrieval_config_sha256   eval/retrieval/config_v3.json (the live retrieval configuration, D-100)
  - retrieval_policy_sha256   data/curated/retrieval_policy.yml
  - adversarial_set.sha256    eval/guardrails/adversarial_v4.jsonl
  - heading_corrections_sha256 is NOT covered: it is the pre-D-126 value, which the named override in
    config_p7.json replaces (the override's sha256 is covered above).
eval/e2e/config_p8.json
  - base                      eval/tools/config_p7.json
  - system.base               eval/guardrails/config_g2_v4.json
  - system.overrides.heading_corrections.sha256   data/curated/heading_corrections.yml
  - system.tools.calc_rates.sha256                data/curated/calc_rates.yml
  - inputs.questions_v1       eval/retrieval/questions_v1.jsonl
  - inputs.e2e_v1             eval/e2e/e2e_v1.jsonl
  - inputs.labels_test_v1     eval/e2e/labels_test_v1.jsonl
  - inputs.labels_dev_v1      eval/e2e/labels_dev_v1.jsonl
  - inputs.blind_calibration_v1   eval/e2e/blind_calibration_v1.jsonl
  - run_e2e.PINS["config_p8"] pins config_p8.json itself
eval/retrieval/config_final.json
  - run_eval.FINAL_CONFIG_SHA256 pins config_final.json itself (the file holds no pins of its own)

The test also fails if a config gains a pin that is not listed here.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval" / "e2e"))
sys.path.insert(0, str(ROOT / "eval" / "retrieval"))

import run_e2e  # noqa: E402
import run_eval  # noqa: E402

P7 = "eval/tools/config_p7.json"
G2V4 = "eval/guardrails/config_g2_v4.json"
P8 = "eval/e2e/config_p8.json"
FINAL = "eval/retrieval/config_final.json"
DISCLOSURE = "data/curated/disclosure_policy.yml"

# The g2_v4 pins are flat keys; each names the file that run_adversarial.current_config() hashes.
G2V4_FLAT = {
    "guardrails_policy_sha256": "data/curated/guardrails_policy.yml",
    "disclosure_policy_sha256": DISCLOSURE,
    "retrieval_config_sha256": "eval/retrieval/config_v3.json",
    "retrieval_policy_sha256": "data/curated/retrieval_policy.yml",
}
G2V4_SUPERSEDED = {"heading_corrections_sha256"}  # replaced by the D-126 override in config_p7.json

EXPECTED_LABELS = {
    "p7.base", "p7.overrides.heading_corrections", "p7.tools.calc_rates", "p7.exit_questions",
    "g2v4.guardrails_policy_sha256", "g2v4.disclosure_policy_sha256", "g2v4.retrieval_config_sha256",
    "g2v4.retrieval_policy_sha256", "g2v4.adversarial_set",
    "p8.base", "p8.system.base", "p8.system.overrides.heading_corrections", "p8.system.tools.calc_rates",
    "p8.inputs.questions_v1", "p8.inputs.e2e_v1", "p8.inputs.labels_test_v1", "p8.inputs.labels_dev_v1",
    "p8.inputs.blind_calibration_v1", "p8.self", "final.self",
}


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _pair(label: str, block: dict, file_key: str = "file") -> tuple[str, str, str]:
    return label, block[file_key], block["sha256"]


def collect_pins() -> list[tuple[str, str, str]]:
    """(label, repository path, pinned sha256) for every pin of a file, read from the configs and the
    pin constants in code."""
    p7, g2, p8 = _json(P7), _json(G2V4), _json(P8)
    pins = [
        _pair("p7.base", p7["base"]),
        _pair("p7.overrides.heading_corrections", p7["overrides"]["heading_corrections"], "path"),
        _pair("p7.tools.calc_rates", p7["tools"]["calc_rates"], "path"),
        _pair("p7.exit_questions", p7["exit_questions"]),
    ]
    unlisted = {k for k in g2 if k.endswith("_sha256")} - set(G2V4_FLAT) - G2V4_SUPERSEDED
    assert not unlisted, f"config_g2_v4.json has file pins that tests/test_pins.py does not cover: {sorted(unlisted)}"
    pins += [(f"g2v4.{key}", path, g2[key]) for key, path in G2V4_FLAT.items()]
    pins.append(("g2v4.adversarial_set", f"eval/guardrails/adversarial_{g2['adversarial_set']['version']}.jsonl",
                 g2["adversarial_set"]["sha256"]))
    system = p8["system"]
    pins += [
        _pair("p8.base", p8["base"]),
        _pair("p8.system.base", system["base"]),
        _pair("p8.system.overrides.heading_corrections", system["overrides"]["heading_corrections"], "path"),
        _pair("p8.system.tools.calc_rates", system["tools"]["calc_rates"], "path"),
    ]
    pins += [_pair(f"p8.inputs.{name}", block) for name, block in p8["inputs"].items()]
    pins.append(("p8.self", P8, run_e2e.PINS["config_p8"]))
    pins.append(("final.self", FINAL, run_eval.FINAL_CONFIG_SHA256))
    return pins


def git_blob(path: str) -> bytes:
    """The bytes git stores for `path`: the index entry, which equals `git show HEAD:path` unless a change
    to that file is staged. Raw: git show applies no line-ending conversion."""
    result = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True)
    assert result.returncode == 0, f"{path} is not in the git index: {result.stderr.decode(errors='replace')}"
    return result.stdout


def mismatches(read_blob) -> list[str]:
    out = []
    for label, path, pinned in collect_pins():
        actual = hashlib.sha256(read_blob(path)).hexdigest()
        if actual != pinned:
            out.append(f"{label}: {path} is pinned as {pinned} but the committed blob hashes to {actual}")
    return out


@pytest.fixture(scope="module")
def in_git_checkout():
    probe = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=ROOT, capture_output=True)
    if probe.returncode != 0:
        pytest.skip("not a git checkout: the committed blobs cannot be read")


def test_every_pin_is_listed():
    assert {label for label, _, _ in collect_pins()} == EXPECTED_LABELS


def test_every_pinned_file_equals_its_committed_blob(in_git_checkout):
    assert mismatches(git_blob) == []


def test_the_old_lf_blob_of_the_disclosure_policy_fails_the_check(in_git_checkout):
    """In memory only: the LF form of the disclosure policy (the blob that was committed before D-187,
    sha256 b11cd28b...) must be reported as the one mismatch."""
    def old_blob(path: str) -> bytes:
        data = git_blob(path)
        return data.replace(b"\r\n", b"\n") if path == DISCLOSURE else data

    found = mismatches(old_blob)
    assert len(found) == 1 and found[0].startswith("g2v4.disclosure_policy_sha256")
    assert "b11cd28b109ff698d67487ae67429e31bf0e9a6ecfe8163bad26475024c3c6f5" in found[0]


def test_the_disclosure_policy_is_stored_as_crlf_bytes(in_git_checkout):
    blob = git_blob(DISCLOSURE)
    assert blob.count(b"\r\n") == blob.count(b"\n") > 0
