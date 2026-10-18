"""Verify that every raw source file matches its pinned SHA-256 hash in data/manifest.csv.

Exit code 0 when every listed file exists with the recorded size and hash; 1 otherwise.
Files are only read and hashed, never opened by any other tool.

Usage: python python/ingest/verify_manifest.py [path/to/manifest.csv]
"""
import csv
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "data" / "manifest.csv"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_rows(manifest: Path) -> list[dict]:
    with manifest.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def verify(manifest: Path = DEFAULT_MANIFEST) -> list[tuple[str, str]]:
    """Return (file, problem) pairs; an empty list means every file checks out."""
    rows = load_rows(manifest)
    if not rows:
        return [(str(manifest), "manifest has no rows")]
    problems = []
    for row in rows:
        name = row["file"]
        path = manifest.parent / name
        if not path.is_file():
            problems.append((name, f"missing (download from {row['source_url']})"))
            continue
        size = path.stat().st_size
        if size != int(row["bytes"]):
            problems.append((name, f"size {size} bytes, manifest says {row['bytes']}"))
        actual = sha256_of(path)
        if actual != row["sha256"]:
            problems.append((name, f"sha256 {actual}, manifest says {row['sha256']}"))
    return problems


def main() -> int:
    manifest = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MANIFEST
    total = len(load_rows(manifest))
    problems = verify(manifest)
    for name, problem in problems:
        print(f"FAIL {name}: {problem}")
    bad = len({name for name, _ in problems})
    print(f"{total - bad}/{total} files verified")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
