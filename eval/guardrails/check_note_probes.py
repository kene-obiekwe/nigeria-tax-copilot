"""Check eval/guardrails/note_recall_probes.jsonl.

Prints ids, counts and PASS or FAIL only. Never prints question text.

Checks: byte format (UTF-8, no BOM, LF), field order and values, ids, family and
kind counts, note labels, near-miss chunk ids, banned characters and names, no
six-word overlap between probes, and no six-word overlap or substring match with
the other question sets (adversarial v1 to v3, the 50 retrieval questions, G01 to G05).

Run from the repo root:  .venv\\Scripts\\python eval\\guardrails\\check_note_probes.py
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBES = ROOT / "eval" / "guardrails" / "note_recall_probes.jsonl"
CHUNKS = ROOT / "data" / "derived" / "chunks" / "v1_chunks.jsonl"
GUARD_DIR = ROOT / "eval" / "guardrails"
GEN_DIR = ROOT / "eval" / "generation"
RETRIEVAL = ROOT / "eval" / "retrieval" / "questions_v1.jsonl"

FIELDS = ["id", "family", "kind", "question", "expected_notes", "rationale"]
SCOPE_TOPICS = [
    "personal_income_tax", "stamp_duties", "petroleum_taxes", "state_local_taxes",
    "surcharge", "economic_development_incentives", "subsidiary_legislation",
    "customs_and_excise", "pre_2026_periods",
]
FAMILIES = ["scope:" + t for t in SCOPE_TOPICS] + ["act", "DQ-12"]
FIRE_PER_FAMILY = 6
MISS_PER_FAMILY = 2
MIN_AVOID = 3  # not machine-checkable; reported as a reminder only
NGRAM = 6

BANNED_CHARS = ["§"]
# Built from fragments so this file does not itself contain the banned names.
_BANNED = [("cla", "ude"), ("gem", "ini"), ("op", "us"), ("son", "net"), ("hai", "ku"),
           ("anthro", "pic"), ("ai ass", "istant"), ("chat", "gpt")]
BANNED_NAMES = re.compile("|".join(a + b for a, b in _BANNED), re.I)
ACT_LABEL = re.compile(r"^act:[a-z0-9]+(_[a-z0-9]+)*$")
CHUNK_ID = re.compile(r"ntaa?2025:[A-Za-z0-9_:().\-]+")

errors = []


def fail(msg):
    errors.append(msg)


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def ngrams(ws, n=NGRAM):
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def read_jsonl(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def walk(obj):
    """Yield every dict inside a JSON value."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def question_texts(path):
    """Question strings from one file: the 'question' field of every object."""
    texts = []
    if path.suffix == ".jsonl":
        values = read_jsonl(path)
    else:
        values = [json.loads(path.read_text(encoding="utf-8"))]
    for v in values:
        for d in walk(v):
            q = d.get("question")
            if isinstance(q, str):
                texts.append(q)
    return texts


def g_questions():
    """Questions of objects with id G01 to G05 in the .json and .jsonl files directly under
    eval/guardrails and eval/generation (not their results folders)."""
    found = {}
    for path in sorted(list(GUARD_DIR.glob("*.json*")) + list(GEN_DIR.glob("*.json*"))):
        if path.name == PROBES.name:
            continue
        try:
            values = read_jsonl(path) if path.suffix == ".jsonl" else [json.loads(path.read_text(encoding="utf-8"))]
        except (ValueError, UnicodeDecodeError):
            continue
        for v in values:
            for d in walk(v):
                i = d.get("id")
                q = d.get("question")
                if isinstance(i, str) and re.fullmatch(r"G0[1-5]", i) and isinstance(q, str):
                    found[i] = q
    return found


def main():
    raw = PROBES.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        fail("file has a BOM")
    if b"\r" in raw:
        fail("file has CR characters (need LF only)")
    if not raw.endswith(b"\n"):
        fail("file does not end with a newline")
    text = raw.decode("utf-8")
    for ch in BANNED_CHARS:
        if ch in text:
            fail("banned character present: U+%04X" % ord(ch))
    if BANNED_NAMES.search(text):
        fail("banned agent or model name present")

    rows = []
    for ln, line in enumerate(text.split("\n")[:-1], 1):
        try:
            rows.append(json.loads(line))
        except ValueError:
            fail("line %d is not valid JSON" % ln)
    expected_n = len(FAMILIES) * (FIRE_PER_FAMILY + MISS_PER_FAMILY)
    if len(rows) != expected_n:
        fail("row count %d, expected %d" % (len(rows), expected_n))

    chunk_ids = {json.loads(l)["chunk_id"] for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()}

    counts = Counter()
    seen_ids = set()
    for idx, r in enumerate(rows, 1):
        rid = r.get("id", "line%d" % idx)
        if list(r.keys()) != FIELDS:
            fail("%s: field order or names wrong" % rid)
            continue
        if rid != "NP-%03d" % idx:
            fail("%s: id not sequential (expected NP-%03d)" % (rid, idx))
        if rid in seen_ids:
            fail("%s: duplicate id" % rid)
        seen_ids.add(rid)
        fam, kind, q, notes, rat = r["family"], r["kind"], r["question"], r["expected_notes"], r["rationale"]
        if fam not in FAMILIES:
            fail("%s: unknown family" % rid)
        if kind not in ("fire", "near_miss"):
            fail("%s: bad kind" % rid)
        counts[(fam, kind)] += 1
        if not isinstance(q, str) or not q.strip() or not q.rstrip().endswith("?"):
            fail("%s: question missing or does not end with ?" % rid)
        if not isinstance(rat, str) or not rat.strip():
            fail("%s: empty rationale" % rid)
        if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
            fail("%s: expected_notes not a list of strings" % rid)
            continue
        if len(set(notes)) != len(notes):
            fail("%s: duplicate labels in expected_notes" % rid)
        for n in notes:
            ok = (n in FAMILIES and n != "act") or bool(ACT_LABEL.match(n))
            if not ok:
                fail("%s: invalid label %r" % (rid, n))
        if kind == "fire":
            if not notes:
                fail("%s: fire probe has no expected notes" % rid)
            if fam == "act":
                if not any(n.startswith("act:") for n in notes):
                    fail("%s: act fire probe lacks an act: label" % rid)
            elif fam not in notes:
                fail("%s: family label not in expected_notes" % rid)
        else:
            if notes:
                fail("%s: near_miss must have empty expected_notes" % rid)
            ids = [t.rstrip(".,;:") for t in CHUNK_ID.findall(rat)]
            if not ids:
                fail("%s: near_miss rationale names no chunk id" % rid)
            for cid in ids:
                if cid not in chunk_ids:
                    fail("%s: chunk id not in corpus: %s" % (rid, cid))

    for fam in FAMILIES:
        if counts[(fam, "fire")] != FIRE_PER_FAMILY:
            fail("%s: %d fire probes, expected %d" % (fam, counts[(fam, "fire")], FIRE_PER_FAMILY))
        if counts[(fam, "near_miss")] != MISS_PER_FAMILY:
            fail("%s: %d near_miss probes, expected %d" % (fam, counts[(fam, "near_miss")], MISS_PER_FAMILY))

    # Overlap between probes
    owner = {}
    probe_words = {}
    for r in rows:
        if "question" not in r or "id" not in r:
            continue
        ws = words(r["question"])
        probe_words[r["id"]] = ws
        for g in ngrams(ws):
            if g in owner and owner[g] != r["id"]:
                fail("%s and %s share %d consecutive words" % (owner[g], r["id"], NGRAM))
            owner.setdefault(g, r["id"])

    # Overlap with other question sets
    sources = {}
    for name in ("adversarial_v1.jsonl", "adversarial_v2.jsonl"):
        p = GUARD_DIR / name
        if not p.exists():
            fail("%s not found" % name)
            continue
        sources[name] = question_texts(p)
    for p in sorted(GUARD_DIR.glob("adversarial_v3*.jsonl")):
        sources[p.name] = question_texts(p)
    if RETRIEVAL.exists():
        sources[RETRIEVAL.name] = question_texts(RETRIEVAL)
    else:
        fail("retrieval question file not found")
    g = g_questions()
    if len(g) != 5:
        fail("found %d of 5 G01 to G05 questions" % len(g))
    sources["G01-G05"] = list(g.values())

    summary = []
    for name, qs in sources.items():
        summary.append("%s: %d questions" % (name, len(qs)))
        if not qs:
            fail("%s: no question text found" % name)
        for q in qs:
            qw = words(q)
            qj = " " + " ".join(qw) + " "
            qgrams = ngrams(qw)
            for pid, pw in probe_words.items():
                pj = " " + " ".join(pw) + " "
                if pj in qj or qj in pj:
                    fail("%s: substring match with a question in %s" % (pid, name))
                    continue
                if qgrams & ngrams(pw):
                    fail("%s: shares %d consecutive words with a question in %s" % (pid, NGRAM, name))

    print("rows: %d" % len(rows))
    for fam in FAMILIES:
        print("  %-42s fire=%d near_miss=%d" % (fam, counts[(fam, "fire")], counts[(fam, "near_miss")]))
    print("compared against: " + "; ".join(summary))
    print("reminder: at least %d fire questions per family must avoid the family keyword (checked by hand)" % MIN_AVOID)
    if errors:
        # de-duplicate, keep order
        seen = set()
        for e in errors:
            if e not in seen:
                seen.add(e)
                print("FAIL: " + e)
        print("RESULT: FAIL (%d problems)" % len(seen))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
