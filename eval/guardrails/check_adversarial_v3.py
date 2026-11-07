"""Structure, overlap and hygiene check for adversarial_v3 (design sections 5.1 and 5.2; D-29, D-40, D-58, D-73).

Usage (from repo root):
    .venv\\Scripts\\python eval\\guardrails\\check_adversarial_v3.py
        [--set eval\\guardrails\\adversarial_v3.jsonl]

adversarial_v3 is all held out: 48 cases (4 per category A1 to A10, 8 controls C) and no split file.

Hard checks (exit code 1 on any failure):
- fields, field order and values; ids ADV3-001 onward; category and total counts;
- `required_notes` labels in the design section 4.5 forms; `expected_reason` only on `refuse` rows;
- every chunk id named in a rationale exists (or is written as "no chunk <id>"); every row except A10
  names at least one chunk id, and A10 rationales state that no chunk covers the topic;
- section and Schedule references in the question: A1 refusals name only provisions that do not exist,
  an A1 subsection case names a subsection its section does not have, A2 names real sections outside
  v1, and controls name only sections in the v1 corpus (data/derived/section_map.csv and the chunks);
- overlap: no question shares 6 or more consecutive words with any adversarial_v1 or adversarial_v2
  question, any note-recall probe (note_recall_probes.jsonl, if present), any of the
  50 retrieval questions, or any exit-check question (G01 to G05), and none is an exact or substring
  match of one; no two v3 questions share 6 consecutive words;
- hygiene: UTF-8 without BOM, LF line ends, one object per line, `split` null, no section symbol, no
  duplicate questions, no question over 2,000 characters, no split file for v3, and no agent or model
  names in the file.

Reported only: keyword overlaps (Jaccard, as in check_adversarial.py) with the same reference questions.

Prints ids, counts and scores only. Question text is never printed, so test-split text stays hidden
(D-29) and held-out questions stay out of logs. The file is not hashed here: the sha256 is pinned only
after review and approval.
"""

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

FIELDS = ["id", "category", "question", "expected", "expected_reason", "required_notes", "must_not",
          "rationale", "split"]
EXPECTED = {"refuse", "flag", "answer_supported"}
REASONS = {None, "invalid_question", "no_such_section", "section_outside_v1", "low_relevance"}
CATEGORIES = [f"A{i}" for i in range(1, 11)] + ["C"]
PER_CATEGORY = {c: 4 for c in CATEGORIES[:-1]} | {"C": 8}
TOTAL = sum(PER_CATEGORY.values())
SCOPE_TOPICS = {"personal_income_tax", "stamp_duties", "petroleum_taxes", "state_local_taxes", "surcharge",
                "economic_development_incentives", "subsidiary_legislation", "pre_2026_periods",
                "customs_and_excise"}
NOTE_LABEL = re.compile(r"scope:[a-z0-9_]+|act:[a-z0-9_]+_act|reference:(no_such_section|section_outside_v1)"
                        r"|DQ-\d{2}|DR-\d{3}")
NTA_SCHEDULES = 14
NGRAM = 6
MAX_CHARS = 2000
FORBIDDEN = re.compile(r"claude|gemini|\bopus\b|\bsonnet\b|ai assistant", re.I)

# Keyword report, same settings as check_adversarial.py.
STOP = set("""a an the of to in on for and or is are was were be been it its my our we i me us you your do
does did what which who whom when where why how can could should would will shall must may this that
these those there their they with from by at as if any all about under than then so not no yes have has
had get got up out into over also just only section nigeria nigerian tax act administration ntaa nta 2025
company companie business""".split())
JACCARD_MIN = 0.4
SHARED_MIN = 4

CHUNK_ID = re.compile(r"ntaa?2025:[^\s,;]+")
ACT = r"(Nigeria Tax Administration Act|Tax Administration Act|NTAA|Nigeria Tax Act|NTA)\b"
SECTION_REF = re.compile(r"\bsections? (\d+)([A-Z])?((?:\(\d+\))*)(?: of the " + ACT + ")?")
ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
            "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
            "eighteenth", "nineteenth", "twentieth"]
SCHEDULE_REF = re.compile(r"\b(" + "|".join(ORDINALS) + r") Schedule|\bSchedule (\d+)", re.I)


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def tokens(text):
    return re.findall(r"[a-z0-9₦]+", text.lower())


def ngrams(text, n=NGRAM):
    t = tokens(text)
    return {tuple(t[i:i + n]) for i in range(len(t) - n + 1)}


def words(text):
    found = re.findall(r"[a-z0-9₦]+", text.lower())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in found if w not in STOP and len(w) > 1}


def normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def resolve_chunk_id(token, chunks):
    """Strip trailing punctuation and unbalanced closing brackets until the token is a chunk id."""
    tok = token.rstrip(".,;:")
    while tok not in chunks and tok.endswith(")") and tok.count("(") < tok.count(")"):
        tok = tok[:-1].rstrip(".,;:")
    return tok


def load_section_map(path):
    sections = collections.defaultdict(set)
    for row in csv.DictReader(Path(path).open(encoding="utf-8")):
        if row["cited_section"].isdigit():
            sections[row["act_id"]].add(int(row["cited_section"]))
    return sections


def in_corpus(act, number, chunk_ids):
    prefix = f"{act}:s{number}"
    return any(c == prefix or c.startswith((prefix + "(", prefix + ":")) for c in chunk_ids)


def section_refs(question, act_length):
    """(act or None, number, letter, subsections) for each section reference; act from the design 4.3 rules."""
    refs = []
    for m in SECTION_REF.finditer(question):
        number, letter, subs, act_name = int(m.group(1)), m.group(2), m.group(3), m.group(4)
        if act_name:
            act = "ntaa2025" if act_name in ("Nigeria Tax Administration Act", "Tax Administration Act",
                                             "NTAA") else "nta2025"
        elif act_length["ntaa2025"] < number <= act_length["nta2025"]:
            act = "nta2025"
        else:
            act = None  # beyond both Acts, or a number both Acts have
        refs.append((act, number, letter, re.findall(r"\((\d+)\)", subs)))
    return refs


def schedule_refs(question):
    out = []
    for m in SCHEDULE_REF.finditer(question):
        out.append(ORDINALS.index(m.group(1).lower()) + 1 if m.group(1) else int(m.group(2)))
    return out


def check_rows(rows, chunk_text, sections):
    problems = []
    chunk_ids = set(chunk_text)
    act_length = {act: max(nums) for act, nums in sections.items()}
    ids = [r.get("id") for r in rows]
    for dup in sorted(i for i, n in collections.Counter(ids).items() if n > 1):
        problems.append(f"duplicate id {dup}")
    expected_ids = [f"ADV3-{i:03d}" for i in range(1, len(rows) + 1)]
    if ids != expected_ids:
        problems.append("ids are not ADV3-001 onward in order")
    questions = collections.Counter(normalise(r.get("question", "")) for r in rows)
    for r in rows:
        if questions[normalise(r.get("question", ""))] > 1:
            problems.append(f"{r.get('id')}: duplicate question")
    for r in rows:
        rid = r.get("id")
        if list(r) != FIELDS:
            problems.append(f"{rid}: fields {list(r)}")
            continue
        q, cat = r["question"], r["category"]
        if cat not in CATEGORIES:
            problems.append(f"{rid}: category {cat}")
        if r["expected"] not in EXPECTED:
            problems.append(f"{rid}: expected {r['expected']}")
        if r["expected_reason"] not in REASONS:
            problems.append(f"{rid}: expected_reason {r['expected_reason']}")
        if r["expected_reason"] is not None and r["expected"] != "refuse":
            problems.append(f"{rid}: expected_reason set on a {r['expected']} row")
        if not isinstance(r["required_notes"], list) or not isinstance(r["must_not"], list):
            problems.append(f"{rid}: required_notes and must_not must be lists")
            continue
        for label in r["required_notes"]:
            if not isinstance(label, str) or not NOTE_LABEL.fullmatch(label):
                problems.append(f"{rid}: note label {label!r} not in a design section 4.5 form")
            elif label.startswith("scope:") and label[6:] not in SCOPE_TOPICS:
                problems.append(f"{rid}: unknown scope topic {label}")
        if not r["must_not"] or not all(isinstance(m, str) and m.strip() for m in r["must_not"]):
            problems.append(f"{rid}: must_not must list at least one non-empty string")
        if not q.strip() or not re.search(r"[A-Za-z]", q) or len(q) > MAX_CHARS:
            problems.append(f"{rid}: question empty, has no letters, or is over {MAX_CHARS} characters")
        if not r["rationale"].strip():
            problems.append(f"{rid}: empty rationale")
        if r["split"] is not None:
            problems.append(f"{rid}: split must stay null")
        if "§" in json.dumps(r, ensure_ascii=False):
            problems.append(f"{rid}: section symbol")

        # Chunk ids named in the rationale.
        named = []
        for token in CHUNK_ID.findall(r["rationale"]):
            cid = resolve_chunk_id(token, chunk_ids)
            if cid in chunk_ids:
                named.append(cid)
            elif f"no chunk {cid}" not in r["rationale"]:
                problems.append(f"{rid}: rationale names {cid}, which is not a chunk id")
        if cat == "A10":
            if "no chunk" not in r["rationale"].lower():
                problems.append(f"{rid}: A10 rationale must state that no chunk covers the topic")
        elif not named:
            problems.append(f"{rid}: rationale names no existing chunk id")

        # Category-specific expectations.
        if cat == "A9" and "DQ-12" not in r["required_notes"]:
            problems.append(f"{rid}: A9 row without DQ-12 in required_notes")
        if cat == "A3" and not any(n.startswith("scope:") for n in r["required_notes"]):
            problems.append(f"{rid}: A3 row without a scope note")
        if cat in ("C", "A8") and r["expected"] != "answer_supported":
            problems.append(f"{rid}: {cat} row must be answer_supported")
        if cat == "A10" and r["expected"] != "refuse":
            problems.append(f"{rid}: A10 row must be refuse")

        # Section and Schedule references in the question.
        refs, scheds = section_refs(q, act_length), schedule_refs(q)
        if cat == "A1" and r["expected_reason"] == "no_such_section":
            if not refs and not scheds:
                problems.append(f"{rid}: A1 refusal names no section or Schedule")
            for act, number, letter, _ in refs:
                exists = act is not None and number in sections[act] and not letter
                if exists or (act is None and number <= act_length["nta2025"]):
                    problems.append(f"{rid}: A1 refusal names a section that exists ({act} {number})")
            for n in scheds:
                if n <= NTA_SCHEDULES:
                    problems.append(f"{rid}: A1 refusal names Schedule {n}, which exists")
        if cat == "A1" and r["expected"] == "answer_supported":
            subs_refs = [ref for ref in refs if ref[3]]
            if not subs_refs:
                problems.append(f"{rid}: A1 subsection case names no subsection")
            for act, number, _, subs in subs_refs:
                texts = [t for c, t in chunk_text.items()
                         if c == f"{act}:s{number}" or c.startswith((f"{act}:s{number}(", f"{act}:s{number}:"))]
                if not texts:
                    problems.append(f"{rid}: base section {act} {number} not in corpus")
                for s in subs:
                    if any(re.search(rf"(^|\s)\({s}\)\s", t) for t in texts):
                        problems.append(f"{rid}: {act} s{number} has a subsection ({s})")
        if cat == "A2":
            if not refs and not scheds:
                problems.append(f"{rid}: A2 row names no section or Schedule")
            for act, number, _, _ in refs:
                if act is None or number not in sections[act]:
                    problems.append(f"{rid}: A2 section {act} {number} not in section_map.csv")
                elif in_corpus(act, number, chunk_ids):
                    problems.append(f"{rid}: {act}:s{number} is in the v1 corpus")
        if cat == "C":
            for act, number, letter, _ in refs:
                if act is None or letter or not in_corpus(act, number, chunk_ids):
                    problems.append(f"{rid}: control names {act} section {number}, not in the v1 corpus")
    counts = collections.Counter(r.get("category") for r in rows)
    for c, n in PER_CATEGORY.items():
        if counts[c] != n:
            problems.append(f"category {c}: {counts[c]} rows, expected {n}")
    if len(rows) != TOTAL:
        problems.append(f"{len(rows)} rows, expected {TOTAL}")
    return problems, counts


def overlap(rows, refs):
    """6-word runs and exact or substring matches (failures); keyword overlaps (reported)."""
    runs, exact, hits = [], [], []
    for r in rows:
        ga, wa, na = ngrams(r["question"]), words(r["question"]), normalise(r["question"])
        for rid, part, text in refs:
            shared_runs = ga & ngrams(text)
            if shared_runs:
                runs.append((r["id"], rid, part, len(shared_runs)))
            nq = normalise(text)
            if nq and (nq in na or na in nq):
                exact.append((r["id"], rid, part))
            wq = words(text)
            if wa and wq:
                shared = len(wa & wq)
                score = shared / len(wa | wq)
                if score >= JACCARD_MIN or shared >= SHARED_MIN:
                    hits.append((r["id"], rid, part, round(score, 2), shared))
    return runs, exact, sorted(hits, key=lambda h: (-h[3], h[0]))


def internal_runs(rows):
    found = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if ngrams(a["question"]) & ngrams(b["question"]):
                found.append((a["id"], b["id"]))
    return found


def hygiene(path, split_path):
    problems = []
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("file starts with a byte-order mark")
    if b"\r" in raw:
        problems.append("file has CR line ends")
    if not raw.endswith(b"\n"):
        problems.append("file does not end with a newline")
    text = raw.decode("utf-8")
    for n, line in enumerate(text.split("\n")[:-1], 1):
        if not line.strip():
            problems.append(f"line {n}: blank")
        elif not line.startswith("{") or not line.endswith("}"):
            problems.append(f"line {n}: not one JSON object")
    if FORBIDDEN.search(text):
        problems.append("file names an agent or model")
    if Path(split_path).exists():
        problems.append(f"{split_path} exists, but adversarial_v3 is all held out (D-58, D-73)")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", dest="set_path", default="eval/guardrails/adversarial_v3.jsonl")
    ap.add_argument("--v1", default="eval/guardrails/adversarial_v1.jsonl")
    ap.add_argument("--v2", default="eval/guardrails/adversarial_v2.jsonl")
    ap.add_argument("--probes", default="eval/guardrails/note_recall_probes.jsonl")
    ap.add_argument("--chunks", default="data/derived/chunks/v1_chunks.jsonl")
    ap.add_argument("--section-map", default="data/derived/section_map.csv")
    ap.add_argument("--retrieval", default="eval/retrieval/questions_v1.jsonl")
    ap.add_argument("--exit-questions", default="eval/generation/exit_questions.jsonl")
    ap.add_argument("--split-file", default="eval/guardrails/split_v3.json")
    args = ap.parse_args()

    problems = hygiene(args.set_path, args.split_file)
    rows = load_jsonl(args.set_path)
    chunk_text = {r["chunk_id"]: r["text"] for r in load_jsonl(args.chunks)}
    sections = load_section_map(args.section_map)
    print(f"set: {args.set_path} (not hashed: pinned only after review and approval)")

    row_problems, counts = check_rows(rows, chunk_text, sections)
    problems += row_problems
    print(f"rows: {len(rows)} (expected {TOTAL}, all held out); per category: "
          + ", ".join(f"{c} {counts[c]}" for c in CATEGORIES))
    expected = collections.defaultdict(collections.Counter)
    for r in rows:
        expected[r["category"]][r["expected"]] += 1
    for c in CATEGORIES:
        print(f"  {c}: " + ", ".join(f"{k} {v}" for k, v in sorted(expected[c].items())))
    totals = collections.Counter(r["expected"] for r in rows)
    print("expected overall: " + ", ".join(f"{k} {v}" for k, v in sorted(totals.items())))
    notes = collections.Counter(n for r in rows for n in r["required_notes"])
    print("required notes: " + (", ".join(f"{k} {v}" for k, v in sorted(notes.items())) or "none"))

    refs = [(r["id"], "adv_v1", r["question"]) for r in load_jsonl(args.v1)]
    refs += [(r["id"], "adv_v2", r["question"]) for r in load_jsonl(args.v2)]
    if Path(args.probes).exists():
        probes = load_jsonl(args.probes)
        missing = [r.get("id") for r in probes if not isinstance(r.get("question"), str)]
        for pid in missing:
            problems.append(f"probe {pid}: no question field, so it could not be compared")
        refs += [(r.get("id"), "probe", r["question"]) for r in probes if isinstance(r.get("question"), str)]
        print(f"note-recall probes: {len(probes)} rows read, {len(missing)} without a question field")
    else:
        print(f"note-recall probes: {args.probes} not present, not compared")
    refs += [(r["id"], r["split"], r["question"]) for r in load_jsonl(args.retrieval)]
    refs += [(r["id"], "exit", r["question"]) for r in load_jsonl(args.exit_questions) if r["id"].startswith("G")]
    by_part = collections.Counter(part for _, part, _ in refs)
    runs, exact, hits = overlap(rows, refs)
    print(f"overlap: compared {len(rows)} x {len(refs)} questions ("
          + ", ".join(f"{k} {v}" for k, v in sorted(by_part.items())) + ")")
    print(f"  shared runs of {NGRAM}+ consecutive words: {len(runs)}")
    for x in runs:
        print(f"    {x}")
        problems.append(f"{x[0]}: shares {NGRAM} consecutive words with {x[1]}")
    print(f"  exact or substring matches: {len(exact)}")
    for e in exact:
        print(f"    {e}")
        problems.append(f"{e[0]}: exact or substring match with {e[1]}")
    inner = internal_runs(rows)
    print(f"  v3 pairs sharing {NGRAM}+ consecutive words: {len(inner)}")
    for pair in inner:
        print(f"    {pair}")
        problems.append(f"{pair[0]} and {pair[1]} share {NGRAM} consecutive words")
    print(f"  keyword overlaps at Jaccard >= {JACCARD_MIN} or >= {SHARED_MIN} shared content words "
          f"(reported, not failed): {len(hits)}")
    for h in hits:
        print(f"    {h}")

    print("RESULT: " + ("FAIL" if problems else "PASS"))
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
