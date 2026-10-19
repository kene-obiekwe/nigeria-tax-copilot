"""Build the v1 chunk set (chunking design, section 5) and the section map.

    .venv\\Scripts\\python python\\ingest\\build_chunks.py

Writes data/derived/section_map.csv, data/derived/chunks/v1_chunks.jsonl and
data/derived/chunks/build_manifest.json. Neither file records a time, so two builds from the
same inputs are byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import platform
import statistics
import sys
from collections import Counter
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from ingest import chunker, section_map  # noqa: E402

OUT_DIR = ROOT / "data" / "derived" / "chunks"
CHUNKS = OUT_DIR / "v1_chunks.jsonl"
BUILD_MANIFEST = OUT_DIR / "build_manifest.json"
LIBRARIES = ["pdfplumber", "pdfminer.six", "pypdfium2", "tiktoken", "tokenizers", "PyYAML"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_files() -> list[Path]:
    data = ROOT / "data"
    files = [
        data / "raw" / chunker.NTA_GAZETTE,
        data / "raw" / section_map.NTA_NASS,
        data / "raw" / section_map.NTAA_GOMBE,
        data / "raw" / section_map.NTAA_NASS,
        data / "derived" / "ocr" / "ntaa_nrs" / "ocr_manifest.json",
        chunker.ALIGNMENT,
        chunker.PROOF_DIR / "index.csv",
        *sorted(chunker.PROOF_DIR.glob("s[0-9][0-9][0-9].txt")),
        chunker.TABLES,
        chunker.BGE_TOKENIZER,
        data / "manifest.csv",
    ]
    if chunker.REGISTER.is_file():
        files.append(chunker.REGISTER)
    return files


def stats(values: list[int]) -> dict:
    values = sorted(values)
    return {"min": values[0], "median": statistics.median(values),
            "p90": values[int(0.9 * (len(values) - 1))], "max": values[-1]}


def manifest_for(chunks: list[dict], notes: list[str]) -> dict:
    return {
        "corpus_snapshot": chunker.CORPUS_SNAPSHOT,
        "cap_tokens_with_header_cl100k": chunker.CAP,
        "inputs": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p) for p in input_files()},
        "python": platform.python_version(),
        "libraries": {name: version(name) for name in LIBRARIES},
        "chunks": len(chunks),
        "by_act_and_unit_type": dict(sorted(Counter(f"{c['act_id']}/{c['unit_type']}" for c in chunks).items())),
        "by_text_source": dict(sorted(Counter(c["text_source"] for c in chunks).items())),
        "by_heading_source": dict(sorted(Counter(c["heading_source"] for c in chunks).items())),
        "tokens_with_header_cl100k": stats([c["tokens_with_header"] for c in chunks]),
        "tokens_with_header_bge": stats([c["tokens_bge_with_header"] for c in chunks]),
        "jsonl_sha256": hashlib.sha256(chunker.to_jsonl(chunks)).hexdigest(),
        "notes": notes,
    }


def build() -> tuple[bytes, bytes, list[dict]]:
    """Return (jsonl bytes, manifest bytes, chunks) without writing anything."""
    chunks, notes = chunker.build()
    jsonl = chunker.to_jsonl(chunks)
    manifest = (json.dumps(manifest_for(chunks, notes), indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    return jsonl, manifest, chunks


def main() -> int:
    section_map.write(section_map.build())
    jsonl, manifest, chunks = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS.write_bytes(jsonl)
    BUILD_MANIFEST.write_bytes(manifest)
    m = json.loads(manifest)
    print(f"{m['chunks']} chunks -> {CHUNKS.relative_to(ROOT)}")
    for key, n in m["by_act_and_unit_type"].items():
        print(f"  {key}: {n}")
    print(f"tokens with header (cl100k): {m['tokens_with_header_cl100k']}")
    print(f"tokens with header (bge):    {m['tokens_with_header_bge']}")
    print(f"v1_chunks.jsonl sha256: {m['jsonl_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
