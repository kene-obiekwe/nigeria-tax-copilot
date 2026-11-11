"""Phase 8 end-to-end scorer (D-134, D-137, D-138, D-139; evaluation design, Sections 3 and 4).

    .venv\\Scripts\\python eval\\e2e\\score_e2e.py --run 1            # free: results/e2e_run1_scores.json, once
    .venv\\Scripts\\python eval\\e2e\\score_e2e.py --g3               # free: results/g3_result.json, once
    .venv\\Scripts\\python eval\\e2e\\score_e2e.py --calibration      # free: scores the blind calibration review

Free: no model call, no database. The sealed files are loaded only through `run_e2e.load_sealed`
(sha256-checked, in memory); the console shows counts and ids only.

**Review file** `results/e2e_run<N>_review.json` (written by the run session from the template of
`review_sheet.py`; item keys are `<case>:claim:<no>`, `<case>:gap:<index>`, `<case>:calc:<id>`):

    {"run": 1, "reviewer": "...",
     "claims": {key: {"verdict": supported|partly_supported|unsupported|contradicted, "reason": "...",
                      "support_not_cited": false, "citations": {chunk_id: accurate|inaccurate}}},
     "gaps": {key: {"states_law": true|false, "verdict": <as a claim, needed when states_law>, "reason": "..."}},
     "computations": {key: {"figure_correct": true|false, "reason": "...",
                            "rate_citations": {chunk_id: accurate|inaccurate}}},
     "adjudications": {key: {"reason": "...", <any of verdict, citations, states_law, figure_correct>}}}

A shown item or citation with no verdict makes the scorer refuse (nothing is written). The
adjudication is the overseer's ruling (D-138 point 5); it overlays the reviewer's entry, and both
sets of figures are written.

**Unit rules (Section 3.4, D-139).** Only answers with status `answered` on T and N cases count in
(a) and (b). (a): every shown claim; every kept gap the reviewer classes as stating law; every shown
computed item. A claim is supported when its verdict is `supported`; a gap likewise; a computed item
when checks K1 to K6 passed and its result equals the hand-worked figure of the label (when the label
gives one for the item's scenario) or else the reviewer's own working. (b): every (claim, cited chunk) pair and every (shown
computed item, source chunk) pair. (c): an R case fails when its status is `answered` or any claim or
computed item is shown. (d): a T or N case is a false refusal when its status is `refused`,
`not_covered` or `error`, or it is `answered` with no shown claim or computed item.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import run_e2e  # noqa: E402
from generation import generate as gen  # noqa: E402

RESULTS_DIR = run_e2e.RESULTS_DIR
BOOTSTRAP_SEED = 20261113
BOOTSTRAP_RESAMPLES = 10_000
VERDICTS = ("supported", "partly_supported", "unsupported", "contradicted")
CITE_VERDICTS = ("accurate", "inaccurate")
THRESHOLD_PCT = {"a": 95, "b": 95}
FALSE_REFUSAL_STATUSES = ("refused", "not_covered", "error")
IN_SCOPE = ("T", "N")
CALIBRATION_SOURCE = ROOT / "eval" / "guardrails" / "verifier_calibration_v1.jsonl"
CALIBRATION_TARGETS = {"mutations_flagged_min": 58, "per_type_min": 9, "real_unsupported_flagged": 2,
                       "human_supported_flagged_max": 4}


class ReviewIncomplete(RuntimeError):
    """The review does not cover every shown item and citation, or holds an invalid entry."""


# Statistics --------------------------------------------------------------------------------------


def wilson(x: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = x / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((centre - r) / d, 6), round((centre + r) / d, 6)]


def bootstrap_interval(per_question: list[tuple[int, int]], *, resamples: int = BOOTSTRAP_RESAMPLES,
                       seed: int = BOOTSTRAP_SEED) -> list[float] | None:
    """The 95% percentile interval of supported/total over questions: `resamples` draws, with
    replacement, of the questions with their items, from a seeded generator. A resample with no item
    is skipped."""
    rng = random.Random(seed)
    n = len(per_question)
    rates = []
    for _ in range(resamples):
        s = t = 0
        for _ in range(n):
            a, b = per_question[rng.randrange(n)]
            s, t = s + a, t + b
        if t:
            rates.append(s / t)
    if not rates:
        return None
    rates.sort()
    return [round(rates[int(0.025 * (len(rates) - 1))], 6), round(rates[int(round(0.975 * (len(rates) - 1)))], 6)]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def spread(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p90": None, "max": None}
    return {"n": len(values), "mean": round(statistics.fmean(values), 6), "median": round(statistics.median(values), 6),
            "p90": round(percentile(values, 90), 6), "max": round(max(values), 6)}


def passes_pct(supported: int, total: int, pct: int) -> bool:
    return total > 0 and 100 * supported >= pct * total


# Shown items -------------------------------------------------------------------------------------


def dedupe(items) -> list[str]:
    return list(dict.fromkeys(items))


def shown_items(row: dict) -> list[dict]:
    """The items of an answered case that (a) and (b) count: claims, kept gaps, shown computed items.
    A case that is not `answered` has none."""
    if row["status"] != "answered":
        return []
    case, items = row["id"], []
    for c in row["claims"]:
        items.append({"key": f"{case}:claim:{c['no']}", "kind": "claim", "case": case, "text": c["text"],
                      "cites": dedupe(c["chunk_ids"])})
    for i, gap in enumerate(row["gaps"], 1):
        items.append({"key": f"{case}:gap:{i}", "kind": "gap", "case": case, "text": gap, "cites": []})
    for comp in row["computations"]:
        if comp.get("shown"):
            items.append({"key": f"{case}:calc:{comp['id']}", "kind": "calc", "case": case, "comp": comp,
                          "cites": dedupe(comp.get("sources") or [])})
    return items


def checks_passed(comp: dict) -> bool:
    """K1 to K6 all recorded and none failed."""
    checks = comp.get("checks") or {}
    return len(checks) >= 6 and all(v is not None and v is not False for v in checks.values())


def parse_figure(value) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").replace("₦", "").strip())
    except InvalidOperation:
        return None


def label_calcs(label: dict | None) -> list[dict]:
    """The `expected_calc` entries of a label (a mapping, or a list of mappings), each with a
    `scenario` and the hand-worked `figure` (also read from `result` or `expected_result`)."""
    calc = (label or {}).get("expected_calc")
    return [e for e in (calc if isinstance(calc, list) else [calc]) if isinstance(e, dict)]


def label_figures(label: dict | None, scenario: str | None) -> list[Decimal] | None:
    """The hand-worked figures the label gives for `scenario`, or None when it has no entry for that
    scenario (a second, correct computation on one question, as in Phase 7 exit run 1, E06)."""
    out = []
    for entry in label_calcs(label):
        if entry.get("scenario") != scenario:
            continue
        for key in ("figure", "result", "expected_result"):
            if entry.get(key) is not None:
                figure = parse_figure(entry[key])
                if figure is not None:
                    out.append(figure)
                break
    return out or None


# Review ------------------------------------------------------------------------------------------


def validate_review(items: list[dict], review: dict) -> None:
    """Raises ReviewIncomplete (naming item keys only) unless every shown item, gap and citation of
    `items` has a valid entry, and no entry names an item that is not shown."""
    problems = []
    sections = {"claim": review.get("claims") or {}, "gap": review.get("gaps") or {},
                "calc": review.get("computations") or {}}
    known = {k: {i["key"] for i in items if i["kind"] == k} for k in sections}
    for kind, entries in sections.items():
        problems += [f"{key}: not a shown {kind}" for key in entries if key not in known[kind]]
    for item in items:
        entry = sections[item["kind"]].get(item["key"])
        key = item["key"]
        if not isinstance(entry, dict):
            problems.append(f"{key}: no review entry")
            continue
        if item["kind"] == "claim":
            if entry.get("verdict") not in VERDICTS:
                problems.append(f"{key}: no valid verdict")
            cites = entry.get("citations")
        elif item["kind"] == "gap":
            if not isinstance(entry.get("states_law"), bool):
                problems.append(f"{key}: states_law not recorded")
            elif entry["states_law"] and entry.get("verdict") not in VERDICTS:
                problems.append(f"{key}: no valid verdict for a gap that states law")
            cites = {}
        else:
            if not isinstance(entry.get("figure_correct"), bool):
                problems.append(f"{key}: figure_correct not recorded")
            cites = entry.get("rate_citations")
        cites = cites if isinstance(cites, dict) else {}
        for chunk in item["cites"]:
            if cites.get(chunk) not in CITE_VERDICTS:
                problems.append(f"{key}: no verdict for citation {chunk}")
        problems += [f"{key}: citation {c} is not cited" for c in cites if c not in item["cites"]]
    adjudications = review.get("adjudications") or {}
    keys = {i["key"] for i in items}
    for key, adj in adjudications.items():
        if key not in keys:
            problems.append(f"{key}: adjudication of an item that is not shown")
        elif not isinstance(adj, dict) or not str(adj.get("reason") or "").strip():
            problems.append(f"{key}: adjudication without a reason")
        else:
            if "verdict" in adj and adj["verdict"] not in VERDICTS:
                problems.append(f"{key}: adjudication with an invalid verdict")
            for c, v in (adj.get("citations") or {}).items():
                if v not in CITE_VERDICTS:
                    problems.append(f"{key}: adjudication with an invalid citation verdict for {c}")
    if problems:
        raise ReviewIncomplete(f"{len(problems)} problems: " + "; ".join(problems[:50]))


def resolve(item: dict, review: dict, *, adjudicated: bool) -> dict:
    """The verdicts of one item: the reviewer's own, or with the overseer's adjudication overlaid."""
    section = {"claim": "claims", "gap": "gaps", "calc": "computations"}[item["kind"]]
    entry = dict(review[section][item["key"]])
    cites = dict(entry.get("citations") if item["kind"] == "claim" else entry.get("rate_citations") or {})
    if adjudicated:
        adj = (review.get("adjudications") or {}).get(item["key"])
        if adj:
            for field in ("verdict", "states_law", "figure_correct"):
                if field in adj:
                    entry[field] = adj[field]
            cites |= adj.get("citations") or {}
    return {"verdict": entry.get("verdict"), "states_law": entry.get("states_law"),
            "figure_correct": entry.get("figure_correct"), "cites": cites,
            "support_not_cited": bool(entry.get("support_not_cited"))}


def counts_in_a(item: dict, verdict: dict) -> bool:
    return item["kind"] != "gap" or verdict["states_law"] is True


def supported(item: dict, verdict: dict, label: dict | None) -> bool:
    if item["kind"] == "calc":
        figures = label_figures(label, item["comp"].get("scenario"))
        if figures is not None:
            try:
                figure_ok = Decimal(str(item["comp"]["result"])) in figures
            except (InvalidOperation, TypeError):
                figure_ok = False
        else:
            figure_ok = verdict["figure_correct"] is True
        return checks_passed(item["comp"]) and figure_ok
    return verdict["verdict"] == "supported"


# Scoring one run ---------------------------------------------------------------------------------


def tally(rows: list[dict], items_by_case: dict, review: dict, labels: dict, *, adjudicated: bool,
          intervals: bool = False) -> dict:
    """(a) and (b) over the T and N cases. `intervals` adds the bootstrap over questions."""
    a_s = a_n = b_s = b_n = 0
    per_a, per_b, not_cited, by_subset = [], [], [], {}
    for row in rows:
        if row["subset"] not in IN_SCOPE:
            continue
        sa = na = sb = nb = 0
        for item in items_by_case[row["id"]]:
            v = resolve(item, review, adjudicated=adjudicated)
            if counts_in_a(item, v):
                na += 1
                sa += supported(item, v, labels.get(row["id"]))
            for chunk in item["cites"]:
                nb += 1
                sb += v["cites"][chunk] == "accurate"
            if item["kind"] == "claim" and v["support_not_cited"] and v["verdict"] != "supported":
                not_cited.append(item["key"])
        per_a.append((sa, na))
        per_b.append((sb, nb))
        a_s, a_n, b_s, b_n = a_s + sa, a_n + na, b_s + sb, b_n + nb
        sub = by_subset.setdefault(row["subset"], {"a": [0, 0], "b": [0, 0]})
        sub["a"][0] += sa; sub["a"][1] += na; sub["b"][0] += sb; sub["b"][1] += nb
    out = {}
    for name, s, n, per in (("a", a_s, a_n, per_a), ("b", b_s, b_n, per_b)):
        out[name] = {"supported" if name == "a" else "accurate": s, "total": n,
                     "rate": None if n == 0 else round(s / n, 6), "wilson": wilson(s, n),
                     "threshold": THRESHOLD_PCT[name] / 100, "pass": passes_pct(s, n, THRESHOLD_PCT[name]),
                     "by_subset": {k: {"count": v[name][0], "total": v[name][1],
                                       "rate": None if v[name][1] == 0 else round(v[name][0] / v[name][1], 6),
                                       "wilson": wilson(*v[name])} for k, v in sorted(by_subset.items())}}
        if intervals:
            out[name]["bootstrap"] = bootstrap_interval(per)
    out["a"]["support_not_cited"] = not_cited
    return out


def score_c(rows: list[dict], labels: dict) -> dict:
    rs = [r for r in rows if r["subset"] == "R"]
    failed = [r["id"] for r in rs if r["status"] == "answered" or r["claims"]
              or any(c.get("shown") for c in r["computations"])]
    exact = [r["id"] for r in rs if r["status"] == (labels.get(r["id"]) or {}).get("expected_status")]
    by_category = {}
    for r in rs:
        entry = by_category.setdefault(r["category"], {"cases": 0, "failed": 0})
        entry["cases"] += 1
        entry["failed"] += r["id"] in failed
    return {"cases": len(rs), "failed": len(failed), "failed_ids": failed, "rate": None if not rs else len(failed) / len(rs),
            "wilson": wilson(len(failed), len(rs)), "pass": bool(rs) and not failed,
            "error_cases": [r["id"] for r in rs if r["status"] == "error"],
            "exact_status_match": len(exact), "by_category": dict(sorted(by_category.items())),
            "reason_classes": count_by(r["refusal_reason"] or r["status"] for r in rs)}


def is_false_refusal(row: dict) -> bool:
    return row["status"] in FALSE_REFUSAL_STATUSES or (
        row["status"] == "answered" and not row["claims"] and not any(c.get("shown") for c in row["computations"]))


def score_d(rows: list[dict]) -> dict:
    ins = [r for r in rows if r["subset"] in IN_SCOPE]
    refused = [r for r in ins if is_false_refusal(r)]
    by_subset = {}
    for s in IN_SCOPE:
        sub = [r for r in ins if r["subset"] == s]
        k = sum(is_false_refusal(r) for r in sub)
        by_subset[s] = {"cases": len(sub), "false_refusals": k, "wilson": wilson(k, len(sub))}
    return {"cases": len(ins), "false_refusals": len(refused), "ids": [r["id"] for r in refused],
            "causes": {r["id"]: {"status": r["status"], "refusal_reason": r["refusal_reason"]} for r in refused},
            "rate": None if not ins else round(len(refused) / len(ins), 6), "wilson": wilson(len(refused), len(ins)),
            "threshold": 0.10, "pass": bool(ins) and 10 * len(refused) <= len(ins), "by_subset": by_subset}


def count_by(values) -> dict:
    out: dict = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def reported_metrics(rows: list[dict], labels: dict, review: dict | None) -> dict:
    ins = [r for r in rows if r["subset"] in IN_SCOPE]
    hit = {}
    for s in IN_SCOPE:
        sub = [r for r in ins if r["subset"] == s]
        ranks = [run_e2e.run_eval.first_gold_rank([p["chunk_id"] for p in r["passages"]],
                                                  (labels.get(r["id"]) or {}).get("gold_units") or []) for r in sub]
        hit[s] = {"cases": len(sub), "hit@5": sum(x is not None and x <= 5 for x in ranks),
                  "hit_any_shown": sum(x is not None for x in ranks)}
    answered = [r for r in ins if r["status"] == "answered"]

    def cited(r):
        return {c for cl in r["claims"] for c in cl["chunk_ids"]}

    def expected(r):
        label = labels.get(r["id"]) or {}
        return list(label.get("expected_citations") or []) + list(label.get("acceptable_citations") or [])

    recall = sum(any(run_e2e.run_eval.first_gold_rank([c], expected(r)) is not None for c in cited(r)) for r in answered)
    verif = [r["verification"] or {} for r in answered]
    required = [(set((labels.get(r["id"]) or {}).get("required_notes") or []), set(r["notes"])) for r in answered]
    by_status: dict = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)
    return {
        "hit_at_5": hit,
        "citation_recall": {"answered": len(answered), "citing_expected_or_acceptable": recall},
        "usefulness": {"claims_removed": sum(v.get("claims_removed", 0) for v in verif),
                       "claims_moved": sum(v.get("claims_moved", 0) for v in verif), "answered": len(answered)},
        "notes": {"required": sum(len(a) for a, _ in required), "required_shown": sum(len(a & b) for a, b in required),
                  "shown_not_required": sum(len(b - a) for a, b in required)},
        "disclaimer_at_end": {"answered": len(answered),
                              "present": sum(r["rendered"].endswith(gen.DISCLAIMER) for r in answered)},
        "answer_coverage": (review or {}).get("answers"),  # the reviewer's key-point record, when given
        "latency_s": {"all": spread([r["latency_s"] for r in rows]),
                      "by_status": {k: spread([r["latency_s"] for r in v]) for k, v in sorted(by_status.items())}},
        "cost_usd": {"all": spread([r["cost_usd"] for r in rows]), "total": round(sum(r["cost_usd"] for r in rows), 6),
                     "by_status": {k: spread([r["cost_usd"] for r in v]) for k, v in sorted(by_status.items())},
                     "by_subset": {k: spread([r["cost_usd"] for r in rows if r["subset"] == k]) for k in run_e2e.SUBSETS}},
    }


def check_record(record: dict | None) -> dict:
    """The record is complete: not void, not stopped on cost, and every planned case written once."""
    if record is None or not run_e2e.counts_as_run(record):
        raise run_e2e.GuardError("there is no complete run record to score (void and incomplete runs are never scored)")
    ids = [c["id"] for c in record["cases"]]
    if ids != record["case_ids"]:
        raise run_e2e.GuardError("the record's cases are not the planned cases in order")
    return record


def score_run(record: dict, review: dict, labels: dict, *, resamples: int = BOOTSTRAP_RESAMPLES,
              seed: int = BOOTSTRAP_SEED) -> dict:
    """Scores one complete run record against its review and the labels. Raises ReviewIncomplete when
    the review does not cover every shown item and citation of a T or N answer."""
    check_record(record)
    rows = record["cases"]
    missing = [r["id"] for r in rows if r["id"] not in labels]
    if missing:
        raise run_e2e.GuardError(f"no label row for: {', '.join(missing)}")
    items_by_case = {r["id"]: shown_items(r) for r in rows if r["subset"] in IN_SCOPE}
    all_items = [i for r in rows if r["subset"] in IN_SCOPE for i in items_by_case[r["id"]]]
    validate_review(all_items, review)
    own = tally(rows, items_by_case, review, labels, adjudicated=False)
    final = tally(rows, items_by_case, review, labels, adjudicated=True, intervals=True)
    c, d = score_c(rows, labels), score_d(rows)
    judged = [{"key": i["key"], "kind": i["kind"], "case": i["case"], **{
        k: v for k, v in resolve(i, review, adjudicated=True).items() if k != "support_not_cited"},
        "supported": supported(i, resolve(i, review, adjudicated=True), labels.get(i["case"]))} for i in all_items]
    passed = {"a": final["a"]["pass"], "b": final["b"]["pass"], "c": c["pass"], "d": d["pass"]}
    return {"run": record["run"], "rerun_after_void": record["rerun_after_void"], "reviewer": review.get("reviewer"),
            "thresholds": "D-134 with D-137 (each run on its own) and D-139",
            "a": final["a"], "b": final["b"], "c": c, "d": d,
            "reviewer_own": {"a": own["a"], "b": own["b"]},
            "adjudicated_items": sorted((review.get("adjudications") or {})),
            "pass": passed, "g3_run_pass": all(passed.values()),
            "reported": reported_metrics(rows, labels, review), "items": judged}


# Pooled figures and the G3 verdict ---------------------------------------------------------------


def pooled(scores: list[dict]) -> dict:
    out = {}
    for name, key in (("a", "supported"), ("b", "accurate")):
        s, n = sum(x[name][key] for x in scores), sum(x[name]["total"] for x in scores)
        out[name] = {key: s, "total": n, "rate": None if n == 0 else round(s / n, 6), "wilson": wilson(s, n)}
    out["c"] = {"cases": sum(x["c"]["cases"] for x in scores), "failed": sum(x["c"]["failed"] for x in scores)}
    out["c"]["wilson"] = wilson(out["c"]["failed"], out["c"]["cases"])
    out["d"] = {"cases": sum(x["d"]["cases"] for x in scores), "false_refusals": sum(x["d"]["false_refusals"] for x in scores)}
    out["d"]["wilson"] = wilson(out["d"]["false_refusals"], out["d"]["cases"])
    return out


def g3_result(scores: list[dict]) -> dict:
    """D-137: G3 passes only when each run meets (a) to (d). When either run does not, work stops under
    D-133 and G3 is not recorded as failed until Kene and the overseer decide."""
    if [s["run"] for s in scores] != [1, 2]:
        raise run_e2e.GuardError("the G3 result needs the scores of run 1 and run 2")
    ok = all(s["g3_run_pass"] for s in scores)
    return {"gate": "G3", "rule": "D-137: each run must meet (a) to (d) on its own",
            "runs": {str(s["run"]): {"pass": s["pass"], "g3_run_pass": s["g3_run_pass"]} for s in scores},
            "pooled": pooled(scores),
            "verdict": "pass" if ok else "stop_path_D-133: not recorded as failed until Kene and the overseer decide"}


# Blind calibration (Section 4.1, point 1) --------------------------------------------------------


def calibration_mapping(original_rows: list[dict], seed: int | None = None) -> dict[str, dict]:
    """Blind id to original row, derived from the seed each time and never stored."""
    import review_sheet
    order = review_sheet.blind_order(len(original_rows), review_sheet.BLIND_SEED if seed is None else seed)
    return {review_sheet.blind_id(i): original_rows[j] for i, j in enumerate(order)}


def score_calibration(review: dict, original_rows: list[dict], seed: int | None = None) -> dict:
    """Scores a blind calibration review against the original labels. Targets: at least 58 of 60
    mutations judged not supported with at least 9 of 10 per type; both real unsupported claims judged
    not supported; at most 4 of the 131 human-supported claims judged not supported. Returns counts
    and pass flags (no label by id); `missed` lists blind ids with their class, for the console."""
    mapping = calibration_mapping(original_rows, seed)
    entries = review.get("items") or {}
    bad = [bid for bid in mapping if (entries.get(bid) or {}).get("verdict") not in VERDICTS + ("not_a_claim",)]
    extra = [bid for bid in entries if bid not in mapping]
    if bad or extra:
        raise ReviewIncomplete(f"{len(bad)} items without a valid verdict, {len(extra)} unknown ids: "
                               + ", ".join((bad + extra)[:20]))
    flagged = {bid: entries[bid]["verdict"] != "supported" for bid in mapping}
    mutations = {bid: r for bid, r in mapping.items() if r["kind"] == "mutation"}
    types = sorted({r["mutation_type"] for r in mutations.values()})
    per_type = {t: sum(flagged[b] for b, r in mutations.items() if r["mutation_type"] == t) for t in types}
    type_sizes = {t: sum(r["mutation_type"] == t for r in mutations.values()) for t in types}
    unsupported = [b for b, r in mapping.items() if r["kind"] == "real" and r["human_verdict"] == "unsupported"]
    human = [b for b, r in mapping.items() if r["kind"] == "real" and r["human_verdict"] == "supported"]
    not_claims = [b for b, r in mapping.items() if r["kind"] == "real" and r["human_verdict"] == "not_a_claim"]
    t = CALIBRATION_TARGETS
    result = {
        "mutations_flagged": sum(per_type.values()), "mutations": len(mutations), "per_type": per_type,
        "per_type_sizes": type_sizes,
        "real_unsupported_flagged": sum(flagged[b] for b in unsupported), "real_unsupported": len(unsupported),
        "human_supported_flagged": sum(flagged[b] for b in human), "human_supported": len(human),
        "not_a_claim_items": {"count": len(not_claims), "judged_not_a_claim": sum(
            entries[b]["verdict"] == "not_a_claim" for b in not_claims)}}
    result["targets"] = {
        "mutations_flagged": result["mutations_flagged"] >= t["mutations_flagged_min"],
        "per_type": all(v >= t["per_type_min"] for v in per_type.values()),
        "real_unsupported": result["real_unsupported_flagged"] == t["real_unsupported_flagged"],
        "human_supported": result["human_supported_flagged"] <= t["human_supported_flagged_max"]}
    result["passed"] = all(result["targets"].values())
    result["missed"] = {
        "mutation_not_flagged": sorted(b for b in mutations if not flagged[b]),
        "real_unsupported_not_flagged": sorted(b for b in unsupported if not flagged[b]),
        "human_supported_flagged": sorted(b for b in human if flagged[b])}
    return result


# Command line ------------------------------------------------------------------------------------


def load_labels(ctx: run_e2e.Context | None = None) -> dict:
    """The label rows by id: N and R from e2e_v1.jsonl, T from labels_test_v1.jsonl (sha256-checked)."""
    ctx = ctx or run_e2e.Context()
    rows = run_e2e.load_sealed(ctx.paths["e2e_v1"], ctx.pins.get("e2e_v1"))
    rows += run_e2e.load_sealed(ctx.paths["labels_test_v1"], ctx.pins.get("labels_test_v1"))
    return {r["id"]: r for r in rows}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def score_run_files(run_no: int, ctx: run_e2e.Context | None = None, *, resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    ctx = ctx or run_e2e.Context()
    record = run_e2e.counted_run(run_no, ctx.results_dir)
    review_path = ctx.results_dir / f"e2e_run{run_no}_review.json"
    if not review_path.exists():
        raise ReviewIncomplete(f"{review_path.name} does not exist")
    out = ctx.results_dir / f"e2e_run{run_no}_scores.json"
    if out.exists():
        raise run_e2e.GuardError(f"{out.name} already exists; scores are written once")
    scores = score_run(record, read_json(review_path), load_labels(ctx), resamples=resamples)
    run_e2e.write_record(out, scores)
    return scores


def g3_files(ctx: run_e2e.Context | None = None) -> dict:
    ctx = ctx or run_e2e.Context()
    out = ctx.results_dir / "g3_result.json"
    if out.exists():
        raise run_e2e.GuardError(f"{out.name} already exists; the G3 result is written once")
    paths = [ctx.results_dir / f"e2e_run{n}_scores.json" for n in run_e2e.RUN_NUMBERS]
    if not all(p.exists() for p in paths):
        raise run_e2e.GuardError("the G3 result needs both run score files")
    result = g3_result([read_json(p) for p in paths])
    run_e2e.write_record(out, result)
    return result


def calibration_files(attempt: int, ctx: run_e2e.Context | None = None) -> dict:
    import review_sheet
    ctx = ctx or run_e2e.Context()
    suffix = "" if attempt == 1 else f"_attempt{attempt}"
    review_path = ctx.results_dir / f"blind_calibration_review{suffix}.json"
    out = ctx.results_dir / f"blind_calibration_scores{suffix}.json"
    if not review_path.exists():
        raise ReviewIncomplete(f"{review_path.name} does not exist")
    if out.exists():
        raise run_e2e.GuardError(f"{out.name} already exists; scores are written once")
    original = [json.loads(line) for line in CALIBRATION_SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    blind_path = ctx.paths["blind_calibration_v1"]
    if blind_path.exists() and blind_path.read_bytes() != review_sheet.blind_bytes(original):
        raise run_e2e.GuardError(f"{blind_path.name} is not the file the seed gives for the calibration source")
    result = score_calibration(read_json(review_path), original)
    run_e2e.write_record(out, {k: v for k, v in result.items() if k != "missed"} | {"attempt": attempt})
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", type=int, choices=run_e2e.RUN_NUMBERS, help="score run N (writes once)")
    group.add_argument("--g3", action="store_true", help="the G3 result from both run scores (writes once)")
    group.add_argument("--calibration", action="store_true", help="score the blind calibration review")
    ap.add_argument("--attempt", type=int, choices=(1, 2), default=1, help="with --calibration: the attempt")
    args = ap.parse_args(argv)
    try:
        if args.run:
            s = score_run_files(args.run)
            print(f"run {args.run}: " + ", ".join(f"({k}) {'pass' if s['pass'][k] else 'FAIL'}" for k in "abcd")
                  + f"; a {s['a']['supported']}/{s['a']['total']}, b {s['b']['accurate']}/{s['b']['total']}, "
                  f"c failed {s['c']['failed']}/{s['c']['cases']}, d {s['d']['false_refusals']}/{s['d']['cases']}")
        elif args.g3:
            print("G3: " + g3_files()["verdict"])
        else:
            r = calibration_files(args.attempt)
            print(f"calibration attempt {args.attempt}: {'MET' if r['passed'] else 'NOT MET'}; mutations "
                  f"{r['mutations_flagged']}/{r['mutations']} {r['per_type']}, real unsupported "
                  f"{r['real_unsupported_flagged']}/{r['real_unsupported']}, human-supported flagged "
                  f"{r['human_supported_flagged']}/{r['human_supported']}; targets {r['targets']}")
            for kind, ids in r["missed"].items():  # console only, never written (for the overseer)
                if ids:
                    print(f"  {kind}: {', '.join(ids)}")
    except (run_e2e.GuardError, ReviewIncomplete) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
