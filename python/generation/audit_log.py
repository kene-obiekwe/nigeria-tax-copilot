"""Generation log (D-28; generation design, section 9).

One JSON object per model call, including failed calls, appended to
logs/generation/YYYY-MM-DD.jsonl (UTC date). logs/ is git-ignored. Schema `gen-log/1`; its fields
map onto the Phase 9 audit table (timestamp, question, retrieved chunk ids, generated answer,
verification outcome, refusal flag). Records never hold the API key, request headers or a
provider error message.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "generation"
SCHEMA = "gen-log/1"
FIELDS = ("schema", "record_id", "timestamp", "question", "corpus", "retrieval", "request", "response",
          "parsed", "disclosures", "rendered", "refusal_flag", "verification", "error")


def log_path(timestamp: datetime, log_dir: Path = LOG_DIR) -> Path:
    return log_dir / f"{timestamp.strftime('%Y-%m-%d')}.jsonl"


def write_record(record: dict, timestamp: datetime, log_dir: Path = LOG_DIR) -> Path:
    """Append one record; every field in FIELDS must be present (null allowed)."""
    missing = [name for name in FIELDS if name not in record]
    if missing:
        raise ValueError(f"log record is missing {missing}")
    path = log_path(timestamp, log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({name: record[name] for name in FIELDS}, ensure_ascii=False) + "\n")
    return path


def read_records(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
