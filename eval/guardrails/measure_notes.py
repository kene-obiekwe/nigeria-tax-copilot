"""Note measure for the trigger notes (G2 fix design, sections 4.3 to 4.5; G2 fix design 2,
sections 2.1 to 2.4 and 2.7 item 5).

    .venv\\Scripts\\python eval\\guardrails\\measure_notes.py                 # patterns only, no model call
    .venv\\Scripts\\python eval\\guardrails\\measure_notes.py --tagger        # also the majority tagger (3 calls each)
    .venv\\Scripts\\python eval\\guardrails\\measure_notes.py --labels-in labels.json   # replay saved tagger labels

For every question, works out the notes the question itself triggers, as `generate()` shows them on
any status: the pre-check notes (scope, other Act, reference), the question-triggered disclosures
(DQ-12 and anchor rows) and, with the tagger, the tagger's notes (the live note tagger: 3 parallel
calls per question, a label shown when at least 2 successful calls give it, D-88). DQ-12's retrieval trigger reads
the passages retrieved under the live retrieval configuration (`config_v2.json`, 6 passages); a
question refused at pre-check retrieves nothing, as in `generate()`.

Questions (257): the 88 note-recall probes (eval/guardrails/note_recall_probes.jsonl, read only,
D-76), the 20 dev questions, G01 to G05, all 96 adversarial_v1 cases and all 48 adversarial_v2
cases (both spent and tuning data, D-52 and D-73, read through `run_adversarial.load_adversarial`).
The old probe fixtures in tests/data/note_probes.json are reported on the patterns.

Options measured (design 2.4):

- **patterns**: the pattern notes (W, D-77) and the pattern DQ-12 triggers;
- **tagger**: the tagger's scope notes and its DQ-12 label (under the D-77 Act rule), with the
  pattern Act and reference notes, which the tagger does not handle;
- **both**: the union, as `generate()` shows it.

The probes are split with seed 20261107 (design 2.2): per family in file order,
`random.Random(seed).sample` picks 3 of the 6 fire ids, then 1 of the 2 near-miss ids, for the tune
half; the rest form the check half. Fire recall counts the fire probes that show every expected
note; "near misses with a note" counts the near misses that show any scope, Act or DQ-12 note.

The D-57 table counts the questions that show a scope, Act or DQ-12 note not in `required_notes`;
whether such a note is false is the reviewer's judgement. Prints ids and labels only, never question
text. Query vectors come from the git-ignored cache; an uncached question costs one embedding call.
With `--tagger` the run stops at the first API error (D-69) and exits with code 3.
`--labels-out` saves the tagger's result per question (ids and labels only: the final labels, status
and reason, and each call's status and labels; no reasoning or question text) under the live tag
prompt version; `--labels-in` replays such a file with no call.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from generation.disclosure import disclose, is_ntaa_commencement_question, load_policy, names_only_other_acts  # noqa: E402
from generation.generate import DbCorpusStore, retrieval_config  # noqa: E402
from generation.guardrails import load_guardrails_policy, note_labels, pre_check  # noqa: E402
from generation.llm import AnthropicLLMClient  # noqa: E402
from generation.note_tagger import TAGGER_MODEL, TAGGER_THRESHOLD, TAGGER_VOTES, tag_question  # noqa: E402
from generation.prompt import TAG_PROMPT_VERSION  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402

PROBES = ROOT / "tests" / "data" / "note_probes.json"
RECALL_PROBES = HERE / "note_recall_probes.jsonl"
RECALL_PROBES_SHA256 = "ea5a18425be0985e030aea446772b50a573e986058bdb198a358f207fd3fd443"  # D-76
SPLIT_SEED = 20261107
MODES = ("patterns", "tagger", "both")
_SCOPE, _DQ12 = "scope:", "DQ-12"


def _load(name: str, path: Path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


run_adversarial = _load("run_adversarial", HERE / "run_adversarial.py")


# Probes and the split --------------------------------------------------------------------------


def load_recall_probes(path: Path = RECALL_PROBES) -> list[dict]:
    """The 88 probes, checked against their sha256 (D-76)."""
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != RECALL_PROBES_SHA256:
        raise SystemExit(f"{path.name} does not match its sha256 (D-76)")
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def probe_split(rows: list[dict], seed: int = SPLIT_SEED) -> dict[str, list[str]]:
    """Design 2.2: per family in file order, 3 of 6 fire ids then 1 of 2 near-miss ids for tune."""
    rng = random.Random(seed)
    tune, check = [], []
    for family in dict.fromkeys(r["family"] for r in rows):
        for kind, n in (("fire", 3), ("near_miss", 1)):
            ids = sorted(r["id"] for r in rows if r["family"] == family and r["kind"] == kind)
            pick = sorted(rng.sample(ids, n))
            tune += pick
            check += [i for i in ids if i not in pick]
    return {"tune": sorted(tune), "check": sorted(check)}


# Notes per option ------------------------------------------------------------------------------


def ok_labels(tagged: dict | None) -> list[str]:
    """The tagger's labels when its call succeeded, else none (pattern notes only)."""
    return list(tagged.get("labels") or []) if tagged and tagged.get("status") == "ok" else []


def is_trigger(label: str) -> bool:
    return label.startswith((_SCOPE, "act:")) or label == _DQ12


def option_labels(question: str, gpolicy, policy, corpus, store, retrieved: list[str] | None,
                  tagged: dict | None) -> dict[str, list[str]]:
    """Labels shown under each option. `tagged` is the tagger's result ({"status", "labels"}) or
    None (no tagger); a failed tagger call leaves the pattern notes only, as in `generate()`."""
    check = pre_check(question, gpolicy, corpus)
    if check.refusal is not None:
        retrieved = []
    rows = store.register_rows([], sorted(policy.anchor_terms))
    shown = note_labels(check.notes, check.refusal, disclose([], rows, question, policy, {}, retrieved=retrieved or []))
    base = [label for label in shown if not label.startswith(_SCOPE) and label != _DQ12]
    pattern = list(shown)
    labels = ok_labels(tagged)
    tag_scope = [f"{_SCOPE}{t}" for t in labels if t != "ntaa_commencement"]
    tag_dq12 = "ntaa_commencement" in labels and not names_only_other_acts(question, policy)
    tagger = base + tag_scope + ([_DQ12] if tag_dq12 else [])
    both = list(dict.fromkeys(pattern + tag_scope + ([_DQ12] if tag_dq12 and _DQ12 not in pattern else [])))
    return {"patterns": pattern, "tagger": tagger, "both": both}


def dq12_without_act_rule(question: str, policy, retrieved, tagged: dict | None) -> bool:
    """DQ-12 under the both option with the D-44 Act rule (only the Nigeria Tax Act excluded)."""
    old = dataclasses.replace(policy, other_acts=())
    labels = ok_labels(tagged)
    return is_ntaa_commencement_question(question, old, retrieved, tagged="ntaa_commencement" in labels)


# Questions -------------------------------------------------------------------------------------


def questions() -> list[dict]:
    rows = [{"id": p["id"], "source": "probe", "family": p["family"], "kind": p["kind"], "question": p["question"],
             "expected": p["expected_notes"], "required_notes": []} for p in load_recall_probes()]
    rows += [{"id": q["id"], "source": "dev", "question": q["question"], "required_notes": []}
             for q in run_adversarial.run_eval.load_questions("dev")]
    rows += [{"id": q["id"], "source": "exit_check", "question": q["question"], "required_notes": []}
             for q in run_adversarial.run_exit_check.load_exit_questions() if q["source"] == "new"]
    for set_name, parts in (("v1", ("tune", "holdout")), ("v2", ("holdout",))):
        for part in parts:
            rows += [{"id": r["id"], "source": "adversarial", "set": set_name, "category": r["category"],
                      "question": r["question"], "required_notes": r.get("required_notes") or []}
                     for r in run_adversarial.load_adversarial(set_name, part)]
    return rows


def run_tagger(rows: list[dict], workers: int = 2) -> tuple[dict[str, dict], dict | None, float]:
    """The majority tagger on every question (D-88: 3 parallel calls each, so `workers` questions
    make up to 3 x `workers` calls at once), stopping at the first API error (D-69). Returns results
    by id ({"status", "labels", "reason", "latency_ms", "cost_usd", "calls"}), the stop record (None
    when complete) and the cost."""
    stop = run_adversarial.APIStop()
    client = run_adversarial.StopOnAPIError(AnthropicLLMClient(model=TAGGER_MODEL), stop)
    out: dict[str, dict] = {}
    cost = [0.0]

    def one(row):
        if stop.stopped:
            return
        result = tag_question(row["question"], client)
        out[row["id"]] = {"status": result.status, "labels": result.labels, "reason": result.reason,
                          "latency_ms": result.latency_ms, "cost_usd": round(result.cost_usd, 7),
                          "calls": [{"status": c.status, "labels": c.labels, "reason": c.reason,
                                     "latency_ms": c.latency_ms} for c in result.calls]}
        cost[0] += result.cost_usd

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, rows))
    return out, (stop.error.as_log() if stop.stopped else None), cost[0]


# Tables ----------------------------------------------------------------------------------------


def probe_counts(probes: list[dict], mode: str) -> tuple[int, int, int, int]:
    fire = [p for p in probes if p["kind"] == "fire"]
    near = [p for p in probes if p["kind"] == "near_miss"]
    recalled = sum(set(p["expected"]) <= set(p["shown"][mode]) for p in fire)
    noted = sum(any(is_trigger(n) for n in p["shown"][mode]) for p in near)
    return recalled, len(fire), noted, len(near)


def by_half_table(probes: list[dict], split: dict, modes) -> list[str]:
    out = ["| Option | Tune fire | Tune near | Check fire | Check near | All fire | All near |",
           "|---|---|---|---|---|---|---|"]
    halves = {"tune": [p for p in probes if p["id"] in split["tune"]],
              "check": [p for p in probes if p["id"] in split["check"]], "all": probes}
    for mode in modes:
        cells = []
        for half in ("tune", "check", "all"):
            r, f, n, m = probe_counts(halves[half], mode)
            cells += [f"{r}/{f}", f"{n}/{m}"]
        out.append(f"| {mode} | " + " | ".join(cells) + " |")
    return out


def by_family_table(probes: list[dict], modes) -> list[str]:
    out = ["| Family | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    for family in dict.fromkeys(p["family"] for p in probes):
        members = [p for p in probes if p["family"] == family]
        cells = []
        for mode in modes:
            r, f, n, m = probe_counts(members, mode)
            cells.append(f"{r}/{f}, {n}/{m}")
        out.append(f"| {family} | " + " | ".join(cells) + " |")
    return out


def probe_misses(probes: list[dict], mode: str) -> tuple[list[str], list[str]]:
    missed = [p["id"] for p in probes if p["kind"] == "fire" and not set(p["expected"]) <= set(p["shown"][mode])]
    noted = [f"{p['id']} {', '.join(n for n in p['shown'][mode] if is_trigger(n))}" for p in probes
             if p["kind"] == "near_miss" and any(is_trigger(n) for n in p["shown"][mode])]
    return missed, noted


def d57_table(rows: list[dict], modes) -> list[str]:
    sets = {"20 dev": [r for r in rows if r["source"] == "dev"],
            "G01 to G05": [r for r in rows if r["source"] == "exit_check"],
            "16 v1 controls": [r for r in rows if r.get("set") == "v1" and r.get("category") == "C"],
            "8 v2 controls": [r for r in rows if r.get("set") == "v2" and r.get("category") == "C"]}
    out = ["| Option | " + " | ".join(sets) + " | v1 required missed (of 96) | v2 required missed (of 48) |",
           "|---|" + "---|" * (len(sets) + 2)]
    for mode in modes:
        cells = []
        for members in sets.values():
            extra = {r["id"]: [n for n in r["shown"][mode] if is_trigger(n) and n not in r["required_notes"]]
                     for r in members}
            extra = {k: v for k, v in extra.items() if v}
            cells.append(f"{len(extra)}" + (" (" + "; ".join(f"{k} {', '.join(v)}" for k, v in extra.items()) + ")"
                                             if extra else ""))
        for set_name in ("v1", "v2"):
            missed = {r["id"]: [n for n in r["required_notes"] if n not in r["shown"][mode]]
                      for r in rows if r.get("set") == set_name}
            missed = {k: v for k, v in missed.items() if v}
            cells.append(f"{len(missed)}" + (" (" + "; ".join(f"{k} {', '.join(v)}" for k, v in missed.items()) + ")"
                                              if missed else ""))
        out.append(f"| {mode} | " + " | ".join(cells) + " |")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tagger", action="store_true",
                        help="the majority tagger, 3 calls per question (paid, about $0.06)")
    parser.add_argument("--labels-in", type=Path, help="replay tagger labels saved by --labels-out (no calls)")
    parser.add_argument("--labels-out", type=Path, help="save the tagger labels (ids and labels only)")
    args = parser.parse_args(argv)
    if args.tagger and args.labels_in:
        parser.error("--tagger and --labels-in are alternatives")

    gpolicy, policy = load_guardrails_policy(), load_policy()
    config = retrieval_config()
    cache = run_adversarial.run_eval.QueryCache(config["model"])
    rows = questions()
    tagged: dict[str, dict] = {}
    stopped, cost = None, 0.0
    if args.tagger:
        tagged, stopped, cost = run_tagger(rows)
        if stopped:
            print(f"STOPPED at the first API error: {stopped['error_type']} (HTTP {stopped['status_code']}) "
                  f"{stopped['api_error_type'] or ''}: {stopped['message'] or ''}", file=sys.stderr)
            return 3
    elif args.labels_in:
        data = json.loads(args.labels_in.read_text(encoding="utf-8"))
        tagged = data["labels"] if isinstance(data.get("labels"), dict) else {  # or a plain id -> result map
            k: {"status": "ok" if v.get("error") is None and v.get("labels") is not None else "failed",
                "labels": v.get("labels") or [], "reason": v.get("error")} for k, v in data.items()}
    if args.labels_out and tagged:
        args.labels_out.parent.mkdir(parents=True, exist_ok=True)
        source = ({"tag_prompt": TAG_PROMPT_VERSION, "votes": TAGGER_VOTES, "threshold": TAGGER_THRESHOLD}
                  if args.tagger else {"replayed_from": str(args.labels_in)})
        args.labels_out.write_text(json.dumps(source | {"labels": tagged}, indent=1), encoding="utf-8")
    modes = MODES if tagged else ("patterns",)

    with connect() as conn:
        store = DbCorpusStore(conn)
        corpus = store.sections()
        retriever = Retriever(conn, config)
        for row in rows:
            row["retrieved"] = [r.chunk_id for r in retriever.retrieve(row["question"], 5,
                                                                       query_vector=cache.embed(row["question"]))]
            if pre_check(row["question"], gpolicy, corpus).refusal is not None:
                row["retrieved"] = []  # refused at pre-check: nothing is retrieved, as in generate()
            row["shown"] = option_labels(row["question"], gpolicy, policy, corpus, store, row["retrieved"],
                                         tagged.get(row["id"]))
        old = json.loads(PROBES.read_text(encoding="utf-8"))
        for group in ("fire", "near_miss"):
            for p in old[group]:
                p["shown"] = option_labels(p["question"], gpolicy, policy, corpus, store, None, None)

    probes = [r for r in rows if r["source"] == "probe"]
    split = probe_split(probes)
    print(f"Note measure ({len(rows)} questions; retrieval {config['name']}; guardrails policy "
          f"{gpolicy.sha256[:12]}...; tagger {'labels from ' + str(args.labels_in) if args.labels_in else 'called' if args.tagger else 'off'})\n")
    print("By half (fire recall, then near misses with a note):\n")
    print("\n".join(by_half_table(probes, split, modes)) + "\n")
    print("By family (all 88 probes; fire recall, then near misses with a note):\n")
    print("\n".join(by_family_table(probes, modes)) + "\n")
    for mode in modes:
        missed, noted = probe_misses(probes, mode)
        print(f"- {mode}: fire probes missed {', '.join(missed) or 'none'}; near misses with a note "
              f"{'; '.join(noted) or 'none'}")
    print("\nNotes not required (D-57) and required notes missed:\n")
    print("\n".join(d57_table(rows, modes)) + "\n")
    mode = modes[-1]
    removed = [r["id"] for r in rows if dq12_without_act_rule(r["question"], policy, r["retrieved"], tagged.get(r["id"]))
               and _DQ12 not in r["shown"][mode]]
    print(f"DQ-12 Act rule (D-77), option {mode}: removes the DQ-12 note from {', '.join(removed) or 'none'}")
    fire_missed = [p["id"] for p in old["fire"] if not set(p["expect"]) <= set(p["shown"]["patterns"])]
    near_fired = [f"{p['id']} {', '.join(n for n in p['shown']['patterns'] if is_trigger(n))}"
                  for p in old["near_miss"] if any(is_trigger(n) for n in p["shown"]["patterns"])]
    print(f"Old probe fixtures, patterns: {len(fire_missed)} of {len(old['fire'])} fire probes missed"
          f"{' (' + ', '.join(fire_missed) + ')' if fire_missed else ''}; {len(near_fired)} of "
          f"{len(old['near_miss'])} near misses with a note{' (' + '; '.join(near_fired) + ')' if near_fired else ''}")
    if tagged:
        failures = {k: v["reason"] for k, v in tagged.items() if v.get("status") != "ok"}
        lat = sorted(v.get("latency_ms", 0) for v in tagged.values())
        print(f"Tagger: {len(tagged)} questions, failures {len(failures)}"
              f"{' (' + '; '.join(f'{k} {v}' for k, v in failures.items()) + ')' if failures else ''}"
              + (f", cost ${cost:.4f}, latency median {lat[len(lat) // 2]} ms, max {lat[-1]} ms" if args.tagger else ""))
        calls = [c for v in tagged.values() for c in v.get("calls") or []]
        if calls:
            call_lat = sorted(c.get("latency_ms", 0) for c in calls)
            print(f"Tagger calls: {len(calls)}, failed {sum(c.get('status') != 'ok' for c in calls)}"
                  + (f", latency median {call_lat[len(call_lat) // 2]} ms, max {call_lat[-1]} ms" if args.tagger else ""))
    print(f"\nquery embeddings: {cache.hits} from cache, {cache.misses} embedded, {cache.tokens_used} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
