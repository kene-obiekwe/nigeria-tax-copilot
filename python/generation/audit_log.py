"""Generation log (D-28, D-39; generation design, section 9; guardrails design, section 9).

One JSON object per question, including refused and failed ones, appended to
logs/generation/YYYY-MM-DD.jsonl (UTC date). logs/ is git-ignored. New records use schema
`gen-log/2`, which adds the guardrail pre-checks, the refusal and the filled verification field to
`gen-log/1`; Phase 5 records stay as written and still read. The fields map onto the Phase 9
audit table (timestamp, question, retrieved chunk ids, generated answer, verification outcome,
refusal flag). Records never hold the API key or request headers. A failed API call keeps its
error type, HTTP status and the API's own error message, with key-shaped text redacted (D-69,
`llm.APIError`).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "generation"
SCHEMA = "gen-log/2"
FIELDS_V1 = ("schema", "record_id", "timestamp", "question", "corpus", "retrieval", "request", "response",
             "parsed", "disclosures", "rendered", "refusal_flag", "verification", "error")
FIELDS = ("schema", "record_id", "timestamp", "question", "corpus", "guardrails", "retrieval", "request",
          "response", "parsed", "verification", "refusal", "disclosures", "notes", "rendered", "refusal_flag",
          "error")
FIELDS_BY_SCHEMA = {"gen-log/1": FIELDS_V1, "gen-log/2": FIELDS}


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
    """Every record in `path`, of either schema version. Unknown versions raise an error."""
    with path.open(encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    for record in records:
        if record.get("schema") not in FIELDS_BY_SCHEMA:
            raise ValueError(f"unknown log schema {record.get('schema')!r}")
    return records
