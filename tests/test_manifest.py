import csv
import subprocess
import sys
from pathlib import Path

from ingest.verify_manifest import DEFAULT_MANIFEST, ROOT, verify

SCRIPT = ROOT / "python" / "ingest" / "verify_manifest.py"


def test_every_raw_file_matches_manifest():
    problems = verify()
    assert problems == [], "\n".join(f"{name}: {problem}" for name, problem in problems)


def test_script_exits_zero_on_real_manifest():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_script_exits_non_zero_on_hash_mismatch(tmp_path: Path):
    with DEFAULT_MANIFEST.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames
    # Point at the real files, but corrupt one recorded hash.
    for row in rows:
        row["file"] = str(DEFAULT_MANIFEST.parent / row["file"])
    rows[0]["sha256"] = "0" * 64
    bad = tmp_path / "manifest.csv"
    with bad.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    result = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "sha256" in result.stdout


def test_script_exits_non_zero_on_missing_file(tmp_path: Path):
    bad = tmp_path / "manifest.csv"
    with DEFAULT_MANIFEST.open(newline="", encoding="utf-8") as fh:
        header = fh.readline()
    bad.write_text(header + "raw/absent.pdf,t,b,https://example.invalid,d,c,a," + "0" * 64 + ",1,text,p,r,yes\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "missing" in result.stdout
