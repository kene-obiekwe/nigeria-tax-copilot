"""Phase 7 exit check runner (D-116, D-123; tools design, sections 7.1 and 9).

    .venv\\Scripts\\python eval\\tools\\run_exit_check.py --status       # free: what is frozen, what may run
    .venv\\Scripts\\python eval\\tools\\run_exit_check.py --freeze       # free: writes config_p7.json once
    .venv\\Scripts\\python eval\\tools\\run_exit_check.py --run 1        # PAID: the 14 exit questions, tools on

The 14 questions are eval/tools/exit_questions_v1.jsonl, written from design Section 9.1 and pinned by
sha256 below. They are new questions: none comes from the retrieval test split (D-29) or the
adversarial sets.

**Configuration (Section 7.1).** `config_p7.json` records its base, `config_g2_v4.json`, by sha256;
the one named override (`heading_corrections`, D-126); the tools block; the prompt labels; and the
exit questions. It is frozen once, before run 1, and never edited. A fix between runs 1 and 2 makes
`config_p7_r2.json`. A run checks that the live configuration equals its frozen file.

**Runs (D-116).** At most 2, each once. Run 1 uses config_p7.json and run 2 config_p7_r2.json. The
runner stops before a question when the run's spend plus the dearest question so far (at least
$0.045) would pass the $0.60 cap.

**API errors (D-128, as Phase 6 D-69 and D-70).** The generation, verifier and tagger clients share
one stop: the first API error stops the run after that question, and no later call reaches the API.
The record is then void: `stopped_on_api_error` (the question, and the error's type and redacted
message from `llm.APIError`), review "void", and no pass or fail verdict. The runner exits with
code 3. A void run does not count towards the two runs; it may be re-run once,
`--run N --rerun-after-void`, which writes `exit_run<N>_rerun.json`, only when the first record is a
complete void record and no re-run record exists.

**Checks (Section 9.2).** The runner does the automatic part: status, the computed items and their
figures, that every number came from code, the runner figure check, the E11 lookup, the notes and
the disclaimer. Check 3 (every kept claim supported) is the overseer's and Kene's review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "eval" / "guardrails"))
sys.path.insert(0, str(ROOT / "eval" / "retrieval"))

import run_adversarial  # noqa: E402
from embed.load import sha256_file  # noqa: E402
from generation import generate as gen  # noqa: E402
from generation.guardrails import HEADING_CORRECTIONS  # noqa: E402
from generation.prompt import (MAX_TOOL_PASSAGES, OUTPUT_SCHEMA_TOOLS, SYSTEM_PROMPT_TOOLS, TOOL_DEFS,  # noqa: E402
                               TOOL_DEFS_VERSION, TOOLS_PROMPT_VERSION, TOOLS_SCHEMA_VERSION)
from generation.verify import extract_tokens, source_tokens  # noqa: E402
from tools import taxcalc  # noqa: E402

QUESTIONS = HERE / "exit_questions_v1.jsonl"
QUESTIONS_SHA256 = "ba25dfd29abfd6b039676436aeb32007a2e078897f71767ad6b9f7dbad63b01a"
BASE_CONFIG = ROOT / "eval" / "guardrails" / "config_g2_v4.json"
BASE_CONFIG_SHA256 = "d088e6cdde077f7d2b9cb85c2c380790148a998eeb50f02196461de12270a3b5"  # D-114, never edited
CONFIGS = {1: HERE / "config_p7.json", 2: HERE / "config_p7_r2.json"}
CONFIG_SHA256: dict[int, str | None] = {1: "e2f61fe680759c0778719c01dfc30d2a463d6f484f54e8f7d7989e5a02ceec25", 2: None}  # recorded here when each is frozen (run 1: D-131)
RESULTS_DIR = HERE / "results"
DECISION = "D-123"
COST_CAP_USD = 0.60
MIN_QUESTION_ESTIMATE_USD = 0.045  # the dearest expected question (design Section 12)
FIGURE_KINDS = ("amt", "pct")
FREEZE_FIELDS = ("frozen_at", "decision")  # recorded at the freeze, not compared with the live configuration


class GuardError(RuntimeError):
    """A run or freeze that the rules forbid. Raised before any model call."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False))


def load_questions(path: Path = QUESTIONS, pinned: str | None = QUESTIONS_SHA256) -> list[dict]:
    if pinned is not None and sha256_file(path) != pinned:
        raise GuardError(f"{path.name} does not match the pinned sha256 {pinned}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({r["id"] for r in rows}) != len(rows):
        raise GuardError(f"{path.name} has duplicate ids")
    return rows


# Configuration (Section 7.1) -------------------------------------------------------------------


def base_overrides(live: dict, base: dict) -> dict:
    """The named overrides of the base: only `heading_corrections` may differ (D-126). Any other
    difference raises, so it can never enter config_p7.json unnamed."""
    allowed = {"heading_corrections_sha256": "heading_corrections"}
    keys = set(live) | set(base)
    differ = sorted(k for k in keys if live.get(k) != base.get(k))
    unnamed = [k for k in differ if k not in allowed]
    if unnamed:
        raise GuardError(f"the live guardrails configuration differs from {BASE_CONFIG.name} in {unnamed}, "
                         "which is not a named override")
    out = {}
    if "heading_corrections_sha256" in differ:
        out["heading_corrections"] = {"path": HEADING_CORRECTIONS.relative_to(ROOT).as_posix(),
                                      "sha256": live["heading_corrections_sha256"],
                                      "base_sha256": base["heading_corrections_sha256"], "decision": "D-126",
                                      "dq": "DQ-19"}
    return out


def current_config(rates: taxcalc.RateTable | None = None) -> dict:
    """The live Phase 7 configuration, as config_p7.json records it (without the freeze fields)."""
    if sha256_file(BASE_CONFIG) != BASE_CONFIG_SHA256:
        raise GuardError(f"{BASE_CONFIG.name} does not match its frozen sha256 {BASE_CONFIG_SHA256}")
    base = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    live = run_adversarial.current_config("v4")
    rates = rates or taxcalc.load_rates()
    tools = gen.ToolsConfig(enabled=True)
    return {
        "base": {"file": BASE_CONFIG.relative_to(ROOT).as_posix(), "sha256": BASE_CONFIG_SHA256},
        "overrides": base_overrides(live, base),
        "tools": {
            "enabled": True,
            "tool_defs_version": TOOL_DEFS_VERSION, "tool_defs_sha256": sha256_json(TOOL_DEFS),
            "tool_choice": ["auto", "auto", "none"],
            "max_model_calls": tools.max_model_calls, "max_lookup_calls": tools.max_lookup_calls,
            "max_lookup_chunks": tools.max_lookup_chunks, "max_calc_calls": tools.max_calc_calls,
            "max_calc_sources": tools.max_calc_sources, "max_passages": MAX_TOOL_PASSAGES,
            "calc_version": taxcalc.CALC_VERSION,
            "calc_rates": {"path": rates.path.relative_to(ROOT).as_posix(), "sha256": rates.sha256},
            "rules": {r.id: {"chunk_id": r.chunk_id, "section": r.section, "chunk_text_sha256": r.chunk_text_sha256}
                      for r in taxcalc.RULES.values()},  # D-129: text-only relies_on items
            "scenarios": list(taxcalc.SCENARIOS),
            "default_assumptions": list(taxcalc.DEFAULT_ASSUMPTIONS),
            "vetoes": {ref: list(scenarios) for ref, scenarios in gen.SCOPE_VETOES.items()},
            "currencies": {"symbols": list(rates.currency_symbols), "words": list(rates.currency_words)},
            "gap_figure_filter": tools.gap_figure_filter},
        "prompts": {"prompt_version": TOOLS_PROMPT_VERSION, "system_prompt_sha256": sha256_text(SYSTEM_PROMPT_TOOLS),
                    "schema_version": TOOLS_SCHEMA_VERSION, "schema_sha256": sha256_json(OUTPUT_SCHEMA_TOOLS),
                    "inherited": {"verifier": base["verifier"]["prompt_version"],
                                  "gap": base["verifier"]["gap_prompt_version"],
                                  "tagger": base["tagger"]["prompt_version"], "normaliser": base["normaliser"]}},
        "exit_questions": {"file": QUESTIONS.relative_to(ROOT).as_posix(), "sha256": QUESTIONS_SHA256},
    }


def freeze_config(run_no: int = 1, *, path: Path | None = None, now: datetime | None = None) -> str:
    """Writes the run's configuration file once and returns its sha256. Makes no model call. The
    sha256 must then be recorded in CONFIG_SHA256 (and the decision log) before the run."""
    path = path or CONFIGS[run_no]
    if path.exists():
        raise GuardError(f"{path.name} already exists; it is frozen once and never edited")
    load_questions()
    config = current_config() | {"frozen_at": (now or datetime.now(timezone.utc)).isoformat(), "decision": DECISION}
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(config, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return sha256_file(path)


def read_record(path: Path) -> dict | None:
    """A run record, or None when it is missing or not a complete JSON record."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def is_void_record(record: dict | None) -> bool:
    """A complete void record (D-128): marked void, with the API error's stop record, and no verdict."""
    stop = (record or {}).get("stopped_on_api_error")
    return (record is not None and record.get("review") == "void" and isinstance(stop, dict)
            and isinstance(stop.get("error"), dict) and stop.get("case") is not None
            and "auto_pass" not in record and isinstance(record.get("cases"), list))


def counted_run(run_no: int, results_dir: Path) -> dict | None:
    """The record of run `run_no` that counts towards the two D-116 runs: the first record unless
    it is void, else the re-run's record unless that is void too. None when no run counts."""
    for path in (result_path(run_no, results_dir), result_path(run_no, results_dir, rerun=True)):
        record = read_record(path)
        if record is not None and not is_void_record(record):
            return record
    return None


def check_run_allowed(run_no: int, *, configs: dict | None = None, pins: dict | None = None,
                      results_dir: Path | None = None, live: dict | None = None, rerun: bool = False) -> dict:
    """The frozen configuration for `run_no`, or GuardError before any model call: at most 2 runs,
    each once; run 2 only after a run 1 that counts; the file frozen, pinned and equal to the live
    configuration. With `rerun`, the one re-run after a void run (D-128, as Phase 6 D-70): allowed
    only when the first record is a complete void record and no re-run record exists. A void run
    does not count towards the two runs."""
    configs, pins, results_dir = configs or CONFIGS, pins or CONFIG_SHA256, results_dir or RESULTS_DIR
    if run_no not in configs:
        raise GuardError("the exit check runs at most twice (D-116): run 1 or run 2")
    first = result_path(run_no, results_dir)
    if rerun:
        if result_path(run_no, results_dir, rerun=True).exists():
            raise GuardError(f"the one re-run allowed after a void run {run_no} has already been made")
        if not first.exists():
            raise GuardError(f"a re-run of run {run_no} needs a first run that stopped on an API error; there is none")
        if not is_void_record(read_record(first)):
            raise GuardError(f"run {run_no}'s record is not a complete void record (stopped_on_api_error), "
                             "so it cannot be re-run (D-128)")
    elif first.exists():
        raise GuardError(f"exit-check run {run_no} has already been made; a run is never repeated "
                         "(after a void run, use --rerun-after-void once)")
    if run_no == 2 and counted_run(1, results_dir) is None:
        raise GuardError("run 2 needs a complete run 1 (a void run does not count)")
    path, pinned = configs[run_no], pins.get(run_no)
    if not pinned:
        raise GuardError(f"no frozen configuration sha256 is recorded for run {run_no} yet ({path.name})")
    if not path.exists() or sha256_file(path) != pinned:
        raise GuardError(f"{path.name} does not match the recorded sha256 {pinned}")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    comparable = {k: v for k, v in frozen.items() if k not in FREEZE_FIELDS}
    if comparable != (live if live is not None else current_config()):
        raise GuardError(f"the live configuration differs from {path.name}")
    return frozen


def result_path(run_no: int, results_dir: Path | None = None, *, rerun: bool = False) -> Path:
    return (results_dir or RESULTS_DIR) / f"exit_run{run_no}{'_rerun' if rerun else ''}.json"


# Automatic checks (Section 9.2) ----------------------------------------------------------------


def figures(text: str) -> set[str]:
    return {t for t in extract_tokens(text) if t.split(":", 1)[0] in FIGURE_KINDS}


def figure_check(answer, question: str, chunks: dict[str, dict]) -> dict:
    """Section 5.2 runner check: every naira amount and percentage in the rendered answer is in the
    question, a shown passage, a note or disclosure text, or a shown computed item; and no claim or
    gap states a computed amount."""
    shown = [c for c in answer.computations if c["shown"]]
    allowed = figures(question) | extract_tokens(question, source=True)
    for chunk in chunks.values():
        allowed |= source_tokens(chunk)
    for d in list(answer.notes) + list(answer.disclosures):
        allowed |= figures(d.text)
    computed_text = set()
    for c in shown:
        computed_text |= figures(" ".join([c["what"], *c["from_question"], *c["assumptions"], *c["working"],
                                           c["result_text"], *c["not_computed"]]))
    unexplained = sorted(figures(answer.rendered) - allowed - computed_text)
    results = {f"amt:{Decimal(c['result']).normalize():f}" for c in shown if c["result"] is not None}
    stated = sorted(t for t in results if any(t in figures(x) for x in [c.text for c in answer.claims] + answer.gaps))
    return {"unexplained": unexplained, "computed_amount_in_claim_or_gap": stated,
            "passed": not unexplained and not stated}


def code_check(record: dict, rates: taxcalc.RateTable) -> list[str]:
    """Section 9.2, check 2, for one shown computation: problems, or an empty list."""
    problems = []
    if not record["steps"] or not all(s["by"].startswith("taxcalc.") for s in record["steps"]):
        problems.append("a step not done by taxcalc")
    for c in record["constants"]:
        row = rates.constants.get(c["id"])
        if row is None or (row.value, row.chunk_id) != (c["value"], c["chunk_id"]):
            problems.append(f"constant {c['id']} not as in the pinned table")
    for key in taxcalc.FIGURE_FIELDS:
        if record["inputs"].get(key) is not None and key not in record["input_matches"]:
            problems.append(f"input {key} not matched to the question")
    if record["code"] != {"calc_version": taxcalc.CALC_VERSION, "rates_sha256": rates.sha256}:
        problems.append("calc version or rate table not the pinned ones")
    return problems


def check_case(row: dict, answer, chunks: dict[str, dict], rates: taxcalc.RateTable) -> dict:
    shown = [c for c in answer.computations if c["shown"]]
    uses = (answer.tools or {}).get("tool_uses", [])
    calc_uses = [u for u in uses if u["name"] == "calculate_liability"]
    out: dict = {}
    if row["kind"] == "compute":
        got = sorted(c["result"] for c in shown)
        rendered_ok = all("- Result: " in answer.rendered and taxcalc.naira(Decimal(r)) in answer.rendered
                          for r in row["expected_results"])
        out["correct"] = (answer.status == "answered" and got == sorted(row["expected_results"]) and rendered_ok)
    else:
        declined = [c for c in answer.computations if c["status"] == "declined"]
        reasons_logged = all(c["declined_reason"] for c in declined)
        out["correct"] = not shown and (not calc_uses or (bool(declined) and reasons_logged))
    out["code"] = {c["id"]: code_check(c, rates) for c in shown}
    out["code_ok"] = all(not p for p in out["code"].values())
    out["figures"] = figure_check(answer, row["question"], chunks)
    notes = {n.ref for n in answer.notes}
    out["notes_ok"] = all(n in notes for n in row["required_notes"])
    if row["expected_lookup"]:
        want = row["expected_lookup"]
        out["lookup_ok"] = any(u["name"] == "lookup_section" and u["is_error"] and u["lookup"]
                               and u["lookup"]["verdict"] == want["verdict"]
                               and (u["input"] or {}).get("section_number", "").strip() == want["section_number"]
                               and (u["input"] or {}).get("act") == want["act"] for u in uses)
    else:
        out["lookup_ok"] = True
    expected_scenarios = sorted(c["scenario"] for c in row["expected_calls"])
    out["extra_calls"] = [u["name"] + (f" {(u['input'] or {}).get('scenario')}" if u["name"] == "calculate_liability"
                                       else "") for u in uses
                          if not (u["name"] == "calculate_liability" and (u["input"] or {}).get("scenario")
                                  in expected_scenarios)
                          and not (u["name"] == "lookup_section" and row["expected_lookup"])]
    out["cites_expected"] = sorted(set(row["expected_citations"]) - set(answer.cited_chunk_ids))  # reported only
    out["disclaimer"] = answer.rendered.endswith(gen.DISCLAIMER)
    out["auto_pass"] = all(out[k] for k in ("correct", "code_ok", "notes_ok", "lookup_ok", "disclaimer")) \
        and out["figures"]["passed"]
    return out


def answer_row(row: dict, answer, checks: dict) -> dict:
    v = answer.verification
    return {"id": row["id"], "question": row["question"], "kind": row["kind"], "status": answer.status,
            "refusal_reason": answer.refusal_reason, "flags": answer.flags,
            "notes": [n.ref for n in answer.notes],
            "claims": [{"no": i, "text": c.text, "chunk_ids": c.chunk_ids} for i, c in enumerate(answer.claims, 1)],
            "gaps": answer.gaps,
            "passages": [{"label": p.label, "chunk_id": p.chunk_id, "reasons": p.reasons} for p in answer.passages],
            "verification": None if v is None else v.summary,
            "computations": answer.computations,
            "tool_uses": [{k: u[k] for k in ("turn", "name", "input", "is_error", "limit_hit", "calculation_id")}
                          for u in (answer.tools or {}).get("tool_uses", [])],
            "disclosures": [d.ref for d in answer.disclosures],
            "rendered": answer.rendered, "cost_usd": round(answer.total_cost_usd, 6),
            "record_id": answer.record_id, "error": answer.error, "checks": checks,
            "review": {"claims_supported": None}}  # Section 9.2, check 3: the overseer and Kene


def projected_over_cap(spent: float, costs: list[float], cap: float = COST_CAP_USD) -> bool:
    return spent + max([MIN_QUESTION_ESTIMATE_USD, *costs]) > cap


def default_clients() -> dict:
    """The live clients (paid). Imported here so that tests, which pass fakes, never build them."""
    from generation.llm import MODEL, VERIFIER_MODEL, AnthropicLLMClient
    from generation.note_tagger import TAGGER_MODEL
    return {"llm": AnthropicLLMClient(model=MODEL), "verifier": AnthropicLLMClient(model=VERIFIER_MODEL),
            "tagger": AnthropicLLMClient(model=TAGGER_MODEL)}


def default_answer(question: str, config, clients: dict):
    return gen.generate(question, config=config, llm=clients["llm"], verifier=clients["verifier"],
                        tagger=clients["tagger"])


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def run(run_no: int, *, rerun: bool = False, clients: dict | None = None, answer_fn=None, store=None,
        results_dir: Path | None = None, guard=None) -> dict:
    """The paid run, or with `rerun` the one re-run after a void run (D-128). The generation,
    verifier and tagger clients share one stop (Phase 6 D-69): the first API error stops the run after
    that question, and every later call fails without reaching the API. Such a run is void: its
    record has `stopped_on_api_error` and review "void", and no pass or fail verdict, not even per
    question. `answer_fn(question, config, clients)` is `generate`; tests pass a fake with fake
    clients, a results folder and `guard`, which replaces the run guard."""
    results_dir = results_dir or RESULTS_DIR
    frozen = guard() if guard is not None else check_run_allowed(run_no, rerun=rerun)
    rows = load_questions()
    rates = taxcalc.load_rates()
    config = gen.GenerationConfig(tools=gen.ToolsConfig(
        enabled=True, rates=rates, config_file=CONFIGS[run_no].relative_to(ROOT).as_posix(),
        config_sha256=CONFIG_SHA256[run_no]))
    stop = run_adversarial.APIStop()
    raw = clients if clients is not None else default_clients()
    wrapped = {name: run_adversarial.StopOnAPIError(client, stop) for name, client in raw.items()}
    answer_fn = answer_fn or default_answer
    store = store or gen.FileCorpusStore()
    started = datetime.now(timezone.utc)
    out_rows, costs, spent, cost_stop, api_stop = [], [], 0.0, None, None
    for row in rows:
        if projected_over_cap(spent, costs):
            cost_stop = {"before": row["id"], "spent_usd": round(spent, 6), "cap_usd": COST_CAP_USD}
            break
        answer = answer_fn(row["question"], config, wrapped)
        chunks = store.chunks([p.chunk_id for p in answer.passages])
        out_rows.append(answer_row(row, answer, check_case(row, answer, chunks, rates)))
        costs.append(answer.total_cost_usd)
        spent += answer.total_cost_usd
        if stop.stopped:
            api_stop = {"case": row["id"], "cases_written": len(out_rows), "cases_planned": len(rows),
                        "error": stop.error.as_log()}
            break
    result = {"run": run_no, "rerun_after_void": rerun,
              "config": {"file": CONFIGS[run_no].relative_to(ROOT).as_posix(), "sha256": CONFIG_SHA256[run_no],
                         "frozen_at": frozen["frozen_at"]},
              "questions": {"file": QUESTIONS.relative_to(ROOT).as_posix(), "sha256": QUESTIONS_SHA256},
              "started": started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(),
              "cost_usd": round(spent, 6), "cap_usd": COST_CAP_USD}
    if api_stop is not None:  # void (D-128): no verdict anywhere in the record; it is never reviewed
        for r in out_rows:
            r.pop("checks")
            r.pop("review")
        result |= {"review": "void", "stopped_on_api_error": api_stop, "stopped_on_cost": cost_stop,
                   "cases": out_rows}
    else:
        result |= {"review": "pending", "stopped_on_api_error": None, "stopped_on_cost": cost_stop,
                   "auto_pass": cost_stop is None and all(r["checks"]["auto_pass"] for r in out_rows),
                   "cases": out_rows}
    write_record(result_path(run_no, results_dir, rerun=rerun), result)
    return result


def status() -> dict:
    out = {"questions_sha256_ok": sha256_file(QUESTIONS) == QUESTIONS_SHA256, "runs": {}}
    for n, path in CONFIGS.items():
        out["runs"][n] = {"config": path.name, "frozen": path.exists(), "pinned": CONFIG_SHA256[n],
                          "result": result_path(n).exists(), "void": is_void_record(read_record(result_path(n))),
                          "rerun_result": result_path(n, rerun=True).exists(),
                          "counts": counted_run(n, RESULTS_DIR) is not None}
        for key, rerun in (("allowed", False), ("rerun_allowed", True)):
            try:
                check_run_allowed(n, rerun=rerun)
                out["runs"][n][key] = True
            except GuardError as exc:
                out["runs"][n][key] = str(exc)
    return out


def main(argv=None, *, run_fn=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="free: report what is frozen and what may run")
    group.add_argument("--freeze", type=int, choices=sorted(CONFIGS), help="free: freeze the configuration of run N")
    group.add_argument("--run", type=int, choices=sorted(CONFIGS), help="PAID: run the exit check")
    ap.add_argument("--rerun-after-void", action="store_true",
                    help="PAID: with --run N, the one re-run after a void run N (D-128)")
    args = ap.parse_args(argv)
    if args.rerun_after_void and not args.run:
        ap.error("--rerun-after-void goes with --run only")
    try:
        if args.status:
            print(json.dumps(status(), indent=1))
            return 0
        if args.freeze:
            print(f"{CONFIGS[args.freeze].name} sha256 {freeze_config(args.freeze)}")
            return 0
        result = (run_fn or run)(args.run, rerun=args.rerun_after_void)
    except GuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if stop := result["stopped_on_api_error"]:
        error = stop["error"]
        print(f"STOPPED at an API error during {stop['case']} ({stop['cases_written']} of {stop['cases_planned']} "
              f"questions written): {error['error_type']} {error['status_code']} {error['api_error_type']}: "
              f"{error['message']}. The record is void (D-128): it does not count towards the two runs and may "
              f"be re-run once with --run {args.run} --rerun-after-void.", file=sys.stderr)
        return 3
    print(f"run {args.run}{' (re-run after void)' if args.rerun_after_void else ''}: auto_pass "
          f"{result['auto_pass']}, cost ${result['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
