"""Fetch the raw source files that are git-ignored (manifest `committed` starting "no").

For each such file in data/manifest.csv:
  - present with the right sha256: report "present";
  - present with the wrong sha256: error, never overwritten;
  - absent: stream the source URL to <target>.part, hashing while writing, and rename it to the
    target only if size and sha256 match the manifest (otherwise delete .part and fail).

Exit code 0 only when every non-committed file is present and verified. Files are only hashed,
never opened any other way.

Usage: python python/ingest/fetch_raw.py [--manifest FILE] [--dest DIR] [--mirror URL]
  --dest DIR    download into DIR instead of data/ (files land in DIR/raw/...)
  --mirror URL  fallback URL tried only if the source URL fails (Phase 10 GitHub Release)
"""
import argparse
import csv
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "data" / "manifest.csv"
TIMEOUT_SECONDS = 30
SIZE_CAP_FACTOR = 1.5
BLOCK = 1 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def load_rows(manifest: Path) -> list[dict]:
    with manifest.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def download(url: str, part: Path, size_cap: int) -> tuple[int, str]:
    """Stream url to part, return (bytes written, sha256). Raises on network or cap errors."""
    digest = hashlib.sha256()
    written = 0
    request = urllib.request.Request(url, headers={"User-Agent": "nigeria-tax-copilot-fetch/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, part.open("wb") as out:
        for block in iter(lambda: response.read(BLOCK), b""):
            written += len(block)
            if written > size_cap:
                raise ValueError(f"download exceeds size cap of {size_cap} bytes")
            digest.update(block)
            out.write(block)
    return written, digest.hexdigest()


def fetch_row(row: dict, base: Path, mirror: str | None) -> tuple[bool, str]:
    """Return (ok, message) for one manifest row."""
    name = row["file"]
    target = base / name
    expected_hash = row["sha256"]
    expected_size = int(row["bytes"])

    if target.exists():
        actual = sha256_of(target)
        if actual == expected_hash:
            return True, f"present {name}"
        return False, (
            f"ERROR {name}: existing file does not match the manifest; not overwritten\n"
            f"  expected sha256 {expected_hash}\n  actual   sha256 {actual}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    urls = [row["source_url"]] + ([mirror] if mirror else [])
    failures = []
    for url in urls:
        try:
            size, actual = download(url, part, int(expected_size * SIZE_CAP_FACTOR))
        except Exception as exc:  # network error, HTTP error, size cap
            failures.append(f"  {url}: {type(exc).__name__}: {exc}")
            part.unlink(missing_ok=True)
            continue
        if size == expected_size and actual == expected_hash:
            part.replace(target)
            return True, f"downloaded {name} ({size} bytes) from {url}"
        part.unlink(missing_ok=True)
        failures.append(
            f"  {url}: content mismatch\n"
            f"    expected {expected_size} bytes, sha256 {expected_hash}\n"
            f"    actual   {size} bytes, sha256 {actual}"
        )
    return False, f"FAIL {name}: could not fetch a verified copy\n" + "\n".join(failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dest", type=Path, default=None)
    parser.add_argument("--mirror", default=None)
    args = parser.parse_args(argv)

    base = args.dest if args.dest else args.manifest.parent
    rows = [r for r in load_rows(args.manifest) if r["committed"].strip().lower().startswith("no")]
    if not rows:
        print("no non-committed files listed in the manifest")
        return 0
    ok_all = True
    for row in rows:
        ok, message = fetch_row(row, base, args.mirror)
        print(message)
        ok_all = ok_all and ok
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
