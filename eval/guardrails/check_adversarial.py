"""Structure and overlap check for an adversarial set (design section 5.2; D-29, D-36).

Usage (from repo root):
    .venv\\Scripts\\python eval\\guardrails\\check_adversarial.py eval\\guardrails\\adversarial_v1.jsonl
        [--split eval\\guardrails\\split_v1.json] [--policy data\\curated\\disclosure_policy.yml]

Hard checks (exit code 1 on any failure): fields and values, category counts, chunk ids named in
rationales, out-of-v1 sections really absent from the corpus, the `split` field left null, exact or
substring matches with the retrieval and exit questions, and, with --split, the split file.

Reported only: keyword overlaps with the 50 retrieval questions and G01 to G05, and A9 questions that
match the DQ-12 patterns of the policy file given (the patterns belong to the build session and may
change, so they never fail the set).

Prints ids, counts and scores only. Question text is never printed, so test-split text stays hidden
(D-29) and held-out questions stay out of logs.
"""

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

FIELDS = ["id", "category", "question", "expected", "expected_reason", "required_notes", "must_not",
          "rationale", "split"]
EXPECTED = {"refuse", "flag", "answer_supported"}
REASONS = {None, "invalid_question", "no_such_section", "section_outside_v1", "low_relevance"}
CATEGORIES = [f"A{i}" for i in range(1, 11)] + ["C"]
PER_CATEGORY = {c: 8 for c in CATEGORIES[:-1]} | {"C": 16}
TUNE_PER_CATEGORY = {c: 4 for c in CATEGORIES[:-1]} | {"C": 8}

# Function words plus words common to almost every question in this domain; without the second group
# every section-reference question overlaps every other one.
STOP = set("""a an the of to in on for and or is are was were be been it its my our we i me us you your do
does did what which who whom when where why how can could should would will shall must may this that
these those there their they with from by at as if any all about under than then so not no yes have has
had get got up out into over also just only section nigeria nigerian tax act administration ntaa nta 2025
company companie business""".split())
JACCARD_MIN = 0.4
SHARED_MIN = 4
CHUNK_ID = re.compile(r"ntaa?2025:[^\s,;]+")
A2_REF = re.compile(r"section (\d+) of the (Nigeria Tax Administration Act|Tax Administration Act|Nigeria Tax Act)")


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


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


def check_rows(rows, chunks):
    problems = []
    ids = [r.get("id") for r in rows]
    for dup in sorted(i for i, n in collections.Counter(ids).items() if n > 1):
        problems.append(f"duplicate id {dup}")
    for r in rows:
        rid = r.get("id")
        if list(r) != FIELDS:
            problems.append(f"{rid}: fields {list(r)}")
            continue
        if not re.fullmatch(r"ADV-\d{3}", rid):
            problems.append(f"{rid}: id format")
        if r["category"] not in CATEGORIES:
            problems.append(f"{rid}: category {r['category']}")
        if r["expected"] not in EXPECTED:
            problems.append(f"{rid}: expected {r['expected']}")
        if r["expected_reason"] not in REASONS:
            problems.append(f"{rid}: expected_reason {r['expected_reason']}")
        if not isinstance(r["required_notes"], list) or not isinstance(r["must_not"], list):
            problems.append(f"{rid}: required_notes and must_not must be lists")
        if not r["question"].strip() or not r["rationale"].strip():
            problems.append(f"{rid}: empty question or rationale")
        if r["split"] is not None:
            problems.append(f"{rid}: split must stay null (the split file is authoritative)")
        if "§" in json.dumps(r, ensure_ascii=False):
            problems.append(f"{rid}: section symbol")
        named = CHUNK_ID.findall(r["rationale"])
        for token in named:
            cid = resolve_chunk_id(token, chunks)
            if cid not in chunks and f"no chunk {cid}" not in r["rationale"]:
                problems.append(f"{rid}: rationale names {cid}, which is not a chunk id")
        a2_ref = A2_REF.search(r["question"]) if r["category"] == "A2" else None
        if (r["category"] == "C" or a2_ref) and not named:
            problems.append(f"{rid}: rationale names no chunk id")
        if a2_ref:
            act = "ntaa2025" if "Administration" in a2_ref.group(2) else "nta2025"
            prefix = f"{act}:s{a2_ref.group(1)}"
            if any(c == prefix or c.startswith((prefix + "(", prefix + ":")) for c in chunks):
                problems.append(f"{rid}: {prefix} is in the v1 corpus")
    counts = collections.Counter(r.get("category") for r in rows)
    for c, n in PER_CATEGORY.items():
        if counts[c] != n:
            problems.append(f"category {c}: {counts[c]} rows, expected {n}")
    return problems, counts


def check_split(split_path, set_path, rows):
    problems = []
    split = json.loads(Path(split_path).read_text(encoding="utf-8"))
    digest = hashlib.sha256(Path(set_path).read_bytes()).hexdigest()
    if split.get("set_sha256") != digest:
        problems.append("split: set_sha256 does not match the set file")
    tune, hold = set(split.get("tune", [])), set(split.get("holdout", []))
    every = {r["id"] for r in rows}
    if tune & hold:
        problems.append(f"split: {len(tune & hold)} ids in both parts")
    if tune | hold != every:
        problems.append("split: tune and holdout do not cover the set exactly")
    by_cat = collections.defaultdict(collections.Counter)
    for r in rows:
        by_cat[r["category"]]["tune" if r["id"] in tune else "holdout"] += 1
    for c, k in TUNE_PER_CATEGORY.items():
        if by_cat[c]["tune"] != k or by_cat[c]["holdout"] != PER_CATEGORY[c] - k:
            problems.append(f"split: category {c} has {dict(by_cat[c])}")
    return problems, split, len(tune), len(hold)


def overlap(rows, refs):
    hits, exact = [], []
    for r in rows:
        wa, na = words(r["question"]), normalise(r["question"])
        for rid, part, text in refs:
            wq = words(text)
            if wa and wq:
                shared = len(wa & wq)
                score = shared / len(wa | wq)
                if score >= JACCARD_MIN or shared >= SHARED_MIN:
                    hits.append((r["id"], rid, part, round(score, 2), shared))
            nq = normalise(text)
            if nq and (nq in na or na in nq):
                exact.append((r["id"], rid, part))
    return sorted(hits, key=lambda h: (-h[3], h[0])), exact


def dq12_matches(rows, policy_path):
    import yaml  # only needed for this report

    policy = yaml.safe_load(Path(policy_path).read_text(encoding="utf-8"))
    patterns = [re.compile(p, re.I) for p in policy["notes"]["DQ-12"]["patterns"]]
    return [r["id"] for r in rows if r["category"] == "A9" and any(p.search(r["question"]) for p in patterns)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("set_path")
    ap.add_argument("--split", help="split file to check against the set")
    ap.add_argument("--policy", help="disclosure policy whose DQ-12 patterns are reported against A9")
    ap.add_argument("--chunks", default="data/derived/chunks/v1_chunks.jsonl")
    ap.add_argument("--retrieval", default="eval/retrieval/questions_v1.jsonl")
    ap.add_argument("--exit-questions", default="eval/generation/exit_questions.jsonl")
    args = ap.parse_args()

    rows = load_jsonl(args.set_path)
    chunks = {r["chunk_id"] for r in load_jsonl(args.chunks)}
    print(f"set: {args.set_path}")
    print(f"sha256: {hashlib.sha256(Path(args.set_path).read_bytes()).hexdigest()}")

    problems, counts = check_rows(rows, chunks)
    print(f"rows: {len(rows)}; per category: " + ", ".join(f"{c} {counts[c]}" for c in CATEGORIES))
    expected = collections.defaultdict(collections.Counter)
    for r in rows:
        expected[r["category"]][r["expected"]] += 1
    for c in CATEGORIES:
        print(f"  {c}: " + ", ".join(f"{k} {v}" for k, v in sorted(expected[c].items())))

    if args.split:
        split_problems, split, n_tune, n_hold = check_split(args.split, args.set_path, rows)
        problems += split_problems
        print(f"split: {args.split}; sha256 {hashlib.sha256(Path(args.split).read_bytes()).hexdigest()}; "
              f"seed {split.get('seed')}; tune {n_tune}, holdout {n_hold}")

    refs = [(r["id"], r["split"], r["question"]) for r in load_jsonl(args.retrieval)]
    refs += [(r["id"], "exit", r["question"]) for r in load_jsonl(args.exit_questions) if r["id"].startswith("G")]
    hits, exact = overlap(rows, refs)
    print(f"overlap: compared {len(rows)} x {len(refs)} questions; "
          f"report at Jaccard >= {JACCARD_MIN} or >= {SHARED_MIN} shared content words")
    print(f"  exact or substring matches: {len(exact)}")
    for e in exact:
        print(f"    {e}")
        problems.append(f"{e[0]}: exact or substring match with {e[1]}")
    print(f"  keyword overlaps (reported, not failed): {len(hits)}")
    for h in hits:
        print(f"    {h}")

    if args.policy:
        matched = dq12_matches(rows, args.policy)
        print(f"A9 questions matching DQ-12 patterns in {args.policy} (reported, not failed): "
              f"{len(matched)} {matched}")

    print("RESULT: " + ("FAIL" if problems else "PASS"))
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
