"""Download the release assets listed in eval/reproduce/assets.json and verify them (Phase 10, D-172, D-184).

    .venv\\Scripts\\python eval\\reproduce\\fetch_assets.py                 # install under the repository
    .venv\\Scripts\\python eval\\reproduce\\fetch_assets.py --dest <folder>  # download into <folder> only

For each asset:
  - already at its target with the right sha256 and size: report "present";
  - at its target with the wrong sha256 or size: error, never overwritten;
  - absent: stream the URL to <target>.part with a running sha256 and byte count (the same
    `fetch_raw.download`), and rename it to the target only when size and sha256 both match.

Without --dest each asset goes to its `install_as` path under the repository (the two embedding
caches under data/cache/, the NRS scan under data/raw/). With --dest each asset goes to
<dest>/<release name>, flat, which is how the release is checked after upload without touching the
repository. The NRS scan's sha256 and size must also equal data/manifest.csv; a mismatch is an error
before any download starts. Exit code 0 only when every selected asset is present and verified.
Free: no API key and no paid call.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from ingest.fetch_raw import SIZE_CAP_FACTOR, download, sha256_of  # noqa: E402

ASSETS = HERE / "assets.json"
MANIFEST = ROOT / "data" / "manifest.csv"


def load_assets(path: Path = ASSETS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_mismatches(assets: list[dict], manifest: Path = MANIFEST) -> list[str]:
    """Problems between the assets that name a manifest row and data/manifest.csv (sha256 and bytes)."""
    with manifest.open(newline="", encoding="utf-8") as fh:
        rows = {row["file"]: row for row in csv.DictReader(fh)}
    problems = []
    for asset in assets:
        key = asset.get("manifest_file")
        if not key:
            continue
        row = rows.get(key)
        if row is None:
            problems.append(f"{asset['name']}: {key} is not in the manifest")
            continue
        if row["sha256"] != asset["sha256"]:
            problems.append(f"{asset['name']}: sha256 differs from the manifest")
        if int(row["bytes"]) != int(asset["bytes"]):
            problems.append(f"{asset['name']}: size differs from the manifest")
    return problems


def target_for(asset: dict, *, base: Path, dest: Path | None) -> Path:
    return (dest / asset["name"]) if dest is not None else (base / asset["install_as"])


def fetch_asset(asset: dict, target: Path, *, fetch=download) -> tuple[bool, str]:
    """Return (ok, message) for one asset. `fetch` is `fetch_raw.download` (tests pass a fake)."""
    name, want_hash, want_size = asset["name"], asset["sha256"], int(asset["bytes"])
    if target.exists():
        actual, size = sha256_of(target), target.stat().st_size
        if actual == want_hash and size == want_size:
            return True, f"present {name}"
        return False, (f"ERROR {name}: existing file does not match assets.json; not overwritten\n"
                       f"  expected {want_size} bytes, sha256 {want_hash}\n"
                       f"  actual   {size} bytes, sha256 {actual}")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        size, actual = fetch(asset["url"], part, int(want_size * SIZE_CAP_FACTOR))
    except Exception as exc:  # network error, HTTP error, size cap
        part.unlink(missing_ok=True)
        return False, f"FAIL {name}: {asset['url']}: {type(exc).__name__}: {exc}"
    if size == want_size and actual == want_hash:
        part.replace(target)
        return True, f"downloaded {name} ({size} bytes)"
    part.unlink(missing_ok=True)
    return False, (f"FAIL {name}: content mismatch\n  expected {want_size} bytes, sha256 {want_hash}\n"
                   f"  actual   {size} bytes, sha256 {actual}")


def fetch_all(assets: list[dict], *, base: Path = ROOT, dest: Path | None = None, only: list[str] | None = None,
              manifest: Path = MANIFEST, fetch=download, log=print) -> bool:
    problems = manifest_mismatches(assets, manifest)
    for problem in problems:
        log(f"ERROR {problem}")
    if problems:
        return False
    selected = [a for a in assets if not only or any(token in a["name"] for token in only)]
    if not selected:
        log("no asset matches --only")
        return False
    ok_all = True
    for asset in selected:
        ok, message = fetch_asset(asset, target_for(asset, base=base, dest=dest), fetch=fetch)
        log(message)
        ok_all = ok_all and ok
    return ok_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assets", type=Path, default=ASSETS)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--dest", type=Path, default=None, help="download flat into this folder only")
    parser.add_argument("--only", action="append", help="only assets whose name contains this text (repeatable)")
    args = parser.parse_args(argv)
    spec = load_assets(args.assets)
    ok = fetch_all(spec["assets"], dest=args.dest, only=args.only, manifest=args.manifest)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
