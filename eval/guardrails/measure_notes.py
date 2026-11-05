"""Note measure for the D-56 trigger families (G2 fix design, sections 4.3 to 4.5).

    .venv\\Scripts\\python eval\\guardrails\\measure_notes.py

For every question, works out the notes the question itself triggers, as `generate()` would show
them on any status, with no model call: the pre-check notes (scope, other Act, reference) and the
question-triggered disclosures (DQ-12 and anchor rows). DQ-12's retrieval trigger reads the passages
retrieved under the live retrieval configuration (`config_v2.json`, 6 passages); a question refused
at pre-check retrieves nothing, as in `generate()`.

Sets: the 20 dev questions, G01 to G05, all 96 adversarial_v1 cases (both parts, read through
`run_adversarial.load_adversarial`; D-52) and the 16 controls among them, plus the probe fixtures in
tests/data/note_probes.json (18 that should fire, 16 near misses that must not), which are matched on
the question alone.

Reports, per set, required notes missed and trigger notes (scope, other Act, DQ-12) not in the
case's `required_notes`, with case ids and labels. Whether a note that was not required is a false
trigger is the reviewer's judgement (D-57). Prints ids and labels only, never question text.
Query vectors come from the git-ignored cache; an uncached question costs one embedding call.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from generation.disclosure import disclose, load_policy  # noqa: E402
from generation.generate import DbCorpusStore, retrieval_config  # noqa: E402
from generation.guardrails import load_guardrails_policy, note_labels, pre_check  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402

PROBES = ROOT / "tests" / "data" / "note_probes.json"


def _load(name: str, path: Path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


run_adversarial = _load("run_adversarial", HERE / "run_adversarial.py")


def question_notes(question: str, gpolicy, policy, corpus, store, retrieved: list[str] | None) -> list[str]:
    """Labels of the notes `question` triggers (pre-check notes, reference refusal, question disclosures)."""
    check = pre_check(question, gpolicy, corpus)
    if check.refusal is not None:
        retrieved = []
    rows = store.register_rows([], sorted(policy.anchor_terms))
    disclosures = disclose([], rows, question, policy, {}, retrieved=retrieved or [])
    return note_labels(check.notes, check.refusal, disclosures)


def cases() -> list[dict]:
    rows = [{"id": q["id"], "source": "dev", "question": q["question"], "required_notes": []}
            for q in run_adversarial.run_eval.load_questions("dev")]
    rows += [{"id": q["id"], "source": "exit_check", "question": q["question"], "required_notes": []}
             for q in run_adversarial.run_exit_check.load_exit_questions() if q["source"] == "new"]
    for part in ("tune", "holdout"):
        rows += [{"id": r["id"], "source": "adversarial", "category": r["category"], "split": part,
                  "question": r["question"], "required_notes": r.get("required_notes") or []}
                 for r in run_adversarial.load_adversarial("v1", part)]
    return rows


def main() -> int:
    gpolicy, policy = load_guardrails_policy(), load_policy()
    config = retrieval_config()
    cache = run_adversarial.run_eval.QueryCache(config["model"])
    rows = cases()
    with connect() as conn:
        store = DbCorpusStore(conn)
        corpus = store.sections()
        retriever = Retriever(conn, config)
        for row in rows:
            retrieved = [r.chunk_id for r in retriever.retrieve(row["question"], 5,
                                                                 query_vector=cache.embed(row["question"]))]
            row["notes_shown"] = question_notes(row["question"], gpolicy, policy, corpus, store, retrieved)
        probes = json.loads(PROBES.read_text(encoding="utf-8"))
        for group in ("fire", "near_miss"):
            for p in probes[group]:
                p["notes_shown"] = question_notes(p["question"], gpolicy, policy, corpus, store, None)

    sets = {
        "20 dev questions": [r for r in rows if r["source"] == "dev"],
        "G01 to G05": [r for r in rows if r["source"] == "exit_check"],
        "v1, all 96 cases": [r for r in rows if r["source"] == "adversarial"],
        "of which the 16 controls": [r for r in rows if r.get("category") == "C"],
    }
    print(f"Note measure ({len(rows)} questions; retrieval {config['name']}; "
          f"policies {gpolicy.sha256[:12]}... and disclosure policy)\n")
    print("| Set | Cases | Required notes missed | Notes not required |")
    print("|---|---|---|---|")
    for name, members in sets.items():
        missed = {r["id"]: run_adversarial.required_notes_missed(r) for r in members}
        extra = {r["id"]: run_adversarial.notes_not_required(r) for r in members}
        missed = {k: v for k, v in missed.items() if v}
        extra = {k: v for k, v in extra.items() if v}
        required = sum(bool(r["required_notes"]) for r in members)
        cell_missed = (f"{len(missed)}: " + "; ".join(f"{k} {', '.join(v)}" for k, v in missed.items())
                       if missed else ("0" if required else "none required"))
        cell_extra = (f"{len(extra)}: " + "; ".join(f"{k} {', '.join(v)}" for k, v in extra.items())
                      if extra else "0")
        print(f"| {name} | {len(members)} | {cell_missed} | {cell_extra} |")
    fire_missed = [p["id"] for p in probes["fire"] if not set(p["expect"]) <= set(p["notes_shown"])]
    near_fired = [(p["id"], [n for n in p["notes_shown"] if run_adversarial._TRIGGER_NOTE.match(n)])
                  for p in probes["near_miss"]]
    near_fired = [(i, n) for i, n in near_fired if n]
    print(f"| Probes that should fire ({len(probes['fire'])}) | {len(probes['fire'])} | "
          f"{len(fire_missed)} missed{': ' + ', '.join(fire_missed) if fire_missed else ''} | |")
    print(f"| Probes, near misses ({len(probes['near_miss'])}) | {len(probes['near_miss'])} | | "
          f"{len(near_fired)}{': ' + '; '.join(f'{i} {', '.join(n)}' for i, n in near_fired) if near_fired else ''} |")
    print(f"\nquery embeddings: {cache.hits} from cache, {cache.misses} embedded, {cache.tokens_used} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
