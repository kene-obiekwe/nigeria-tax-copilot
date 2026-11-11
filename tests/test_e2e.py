"""The Phase 8 end-to-end harness (D-133 to D-142; evaluation design, Section 7.6). Free: fake clients,
fake sealed files built in a temporary folder, no key and no API call.

The real sealed files (e2e_v1.jsonl, labels_test_v1.jsonl) are never opened here: every test uses
invented rows. The held-out text guard at the end runs only with TAXY_OVERSEER_HOLDOUT_CHECK=1.
Assertion messages name ids only."""
import copy
import functools
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import psycopg
import pytest

from embed.clients import EmbeddingError
from generation import generate as gen, llm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval" / "e2e"))

import review_sheet as rs  # noqa: E402
import run_e2e as e2e  # noqa: E402
import score_e2e as sc  # noqa: E402
import test_tools as tt  # noqa: E402  (the fake clients of tests/test_tools.py)

SECRET = "Overloaded; key sk-ant-api03-SECRETSECRET was sent"
FAKE_QUESTIONS = {
    "T01": "Fake test question one about the rate?", "T02": "Fake test question two about filing?",
    "N01": "Fake new question one about VAT?", "N02": "Fake new question two about CIT?",
    "R01": "Fake refuse question one about stamp duty?", "R02": "Fake refuse question two about France?"}
COUNTS = {"T": 2, "N": 2, "R": 2}


@functools.lru_cache(maxsize=1)
def live():
    return e2e.live_config()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def make_ctx(tmp_path: Path, **over) -> e2e.Context:
    """Fake sealed files, fake pins, a fake clean tree and database; config_p8 not frozen yet."""
    d = tmp_path / "e2e"
    d.mkdir(parents=True, exist_ok=True)
    paths = {name: d / f"{name}.jsonl" for name in e2e.INPUT_PINS} | {"config_p8": d / "config_p8.json"}
    write_rows(paths["e2e_v1"], [
        {"id": "N01", "subset": "N", "category": "vat", "question": FAKE_QUESTIONS["N01"], "expected_status": "answered",
         "gold_units": ["nta2025:s148"], "expected_citations": ["nta2025:s148"], "acceptable_citations": []},
        {"id": "N02", "subset": "N", "category": "cit", "question": FAKE_QUESTIONS["N02"], "expected_status": "answered",
         "gold_units": ["nta2025:s56"], "expected_citations": ["nta2025:s56"], "acceptable_citations": [],
         "expected_calc": {"scenario": "vat_on_supply", "figure": "195000.00", "currency": "NGN", "inputs": {}, "working": []}},
        {"id": "R01", "subset": "R", "category": "R2", "question": FAKE_QUESTIONS["R01"], "expected_status": "refused"},
        {"id": "R02", "subset": "R", "category": "R4", "question": FAKE_QUESTIONS["R02"], "expected_status": "not_covered"}])
    write_rows(paths["labels_test_v1"], [
        {"id": "T01", "subset": "T", "category": "vat", "expected_status": "answered", "gold_units": ["nta2025:s148"],
         "expected_citations": ["nta2025:s148"], "acceptable_citations": ["nta2025:s152"], "required_notes": []},
        {"id": "T02", "subset": "T", "category": "vat", "expected_status": "answered", "gold_units": ["nta2025:s56"]}])
    write_rows(paths["labels_dev_v1"], [{"id": "Q014", "subset": "dev"}])
    paths["blind_calibration_v1"].write_text("{}\n", encoding="utf-8")
    pins = {name: sha(paths[name]) for name in e2e.INPUT_PINS} | {"config_p8": None}
    questions = {k: {"question": FAKE_QUESTIONS[k], "category": "vat"} for k in ("T01", "T02")}
    kw = dict(paths=paths, pins=pins, results_dir=tmp_path / "results", live=live, questions=lambda: questions,
              expected_counts=COUNTS, git=lambda: {"clean": True, "commit": "abc123"}, db=lambda: None, environ={})
    return e2e.Context(**(kw | over))


def frozen_ctx(tmp_path: Path, **over) -> e2e.Context:
    ctx = make_ctx(tmp_path, **over)
    ctx.pins["config_p8"] = e2e.freeze_config(ctx)
    return ctx


# Pins ------------------------------------------------------------------------------------------


def test_pins_block_lists_the_five_files():
    assert set(e2e.PINS) == {"e2e_v1", "labels_test_v1", "labels_dev_v1", "blind_calibration_v1", "config_p8"}
    assert set(e2e.PATHS) == set(e2e.PINS) and e2e.INPUT_PINS == tuple(n for n in e2e.PINS if n != "config_p8")


def test_the_real_runner_refuses_freeze_smoke_and_run_while_a_pin_is_none(capsys):
    if not e2e.missing_pins(e2e.PINS, e2e.PINS):
        pytest.skip("every pin is set")
    before = e2e.PATHS["config_p8"].exists(), e2e.RESULTS_DIR.exists() and sorted(e2e.RESULTS_DIR.glob("*started*"))
    argvs = [["--smoke"], ["--run", "1"], ["--run", "1", "--rerun-after-void"], ["--run", "2"]]
    if e2e.missing_pins(e2e.PINS, e2e.INPUT_PINS):  # with the four input pins set, --freeze is allowed to run
        argvs.insert(0, ["--freeze"])
    for argv in argvs:
        assert e2e.main(argv) == 2, argv
    assert "pins are not set" in capsys.readouterr().err
    after = e2e.PATHS["config_p8"].exists(), e2e.RESULTS_DIR.exists() and sorted(e2e.RESULTS_DIR.glob("*started*"))
    assert after == before  # nothing written: no config, no marker


@pytest.mark.parametrize("none", e2e.PINS)
def test_each_none_pin_blocks_smoke_and_run(tmp_path, none):
    ctx = frozen_ctx(tmp_path)
    ctx.pins[none] = None
    with pytest.raises(e2e.GuardError, match="pins are not set"):
        e2e.check_run_allowed(1, ctx)
    with pytest.raises(e2e.GuardError, match="pins are not set"):
        e2e.check_smoke_allowed(ctx)
    assert not e2e.marker_path(1, ctx.results_dir).exists()


def test_freeze_needs_the_four_input_pins_but_not_its_own(tmp_path):
    ctx = make_ctx(tmp_path)
    assert ctx.pins["config_p8"] is None
    path = ctx.paths["config_p8"]
    for name in e2e.INPUT_PINS:
        broken = make_ctx(tmp_path / name, pins=dict(ctx.pins, **{name: None}))
        with pytest.raises(e2e.GuardError, match=name):
            e2e.freeze_config(broken)
    assert not path.exists()
    digest = e2e.freeze_config(ctx)
    assert digest == sha(path)


def test_freeze_writes_once_and_records_the_base_the_inputs_and_the_order(tmp_path):
    ctx = make_ctx(tmp_path)
    digest = e2e.freeze_config(ctx)
    frozen = json.loads(ctx.paths["config_p8"].read_text(encoding="utf-8"))
    assert digest == sha(ctx.paths["config_p8"])
    assert frozen["base"] == {"file": "eval/tools/config_p7.json", "sha256": e2e.P7_CONFIG_SHA256}
    assert frozen["base"]["sha256"].startswith("e2f61fe6") and frozen["overrides"] == {}
    assert "exit_questions" not in frozen["system"] and frozen["system"] == live()
    assert frozen["inputs"]["e2e_v1"]["sha256"] == ctx.pins["e2e_v1"]
    assert frozen["inputs"]["questions_v1"]["sha256"] == e2e.run_eval.QUESTIONS_SHA256
    assert frozen["case_order"]["seed"] == e2e.CASE_ORDER_SEED and sorted(frozen["case_order"]["ids"]) == sorted(FAKE_QUESTIONS)
    assert frozen["caps"]["run_usd"] == 1.10 and frozen["caps"]["ledger_usd"] == 3.00
    with pytest.raises(e2e.GuardError, match="frozen once"):
        e2e.freeze_config(ctx)


def test_the_build_does_not_freeze_or_run_anything():
    if e2e.PINS["config_p8"] is None:
        assert not e2e.CONFIG.exists() and not (e2e.RESULTS_DIR / "smoke.json").exists()
        assert not list(e2e.RESULTS_DIR.glob("e2e_run*")) if e2e.RESULTS_DIR.exists() else True


# Cases -----------------------------------------------------------------------------------------


def test_cases_load_through_the_pins_only(tmp_path):
    ctx = make_ctx(tmp_path)
    cases = ctx.cases()
    assert sorted(c["id"] for c in cases) == sorted(FAKE_QUESTIONS) and len(cases) == 6
    assert {c["id"]: c["subset"] for c in cases} == {"T01": "T", "T02": "T", "N01": "N", "N02": "N", "R01": "R", "R02": "R"}
    assert [c["id"] for c in cases] == e2e.ordered_ids(sorted(FAKE_QUESTIONS))  # seeded, not sorted
    assert [c["id"] for c in cases] != sorted(FAKE_QUESTIONS)
    assert e2e.ordered_ids(["a", "b", "c"]) == e2e.ordered_ids(["c", "a", "b"])


def test_a_changed_sealed_file_or_a_missing_pin_is_refused_without_printing_content(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.paths["e2e_v1"].write_text(ctx.paths["e2e_v1"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(e2e.GuardError, match="does not match the pinned sha256") as err:
        ctx.cases()
    assert FAKE_QUESTIONS["N01"] not in str(err.value)
    with pytest.raises(e2e.GuardError, match="no sha256 is pinned"):
        e2e.load_cases(ctx.paths, dict(ctx.pins, e2e_v1=None), questions=ctx.questions, expected_counts=COUNTS)


def test_case_counts_duplicates_and_unknown_subsets_are_refused(tmp_path):
    ctx = make_ctx(tmp_path, expected_counts={"T": 30, "N": 20, "R": 40})
    with pytest.raises(e2e.GuardError, match="case counts"):
        ctx.cases()
    ctx = make_ctx(tmp_path / "b")
    rows = [json.loads(x) for x in ctx.paths["labels_test_v1"].read_text(encoding="utf-8").splitlines()]
    write_rows(ctx.paths["labels_test_v1"], rows + [{"id": "T01"}])
    ctx.pins["labels_test_v1"] = sha(ctx.paths["labels_test_v1"])
    with pytest.raises(e2e.GuardError, match="duplicate id"):
        ctx.cases()


def test_smoke_cases_are_tuning_cases_in_the_stated_order():
    cases = e2e.smoke_cases()
    assert [c["id"] for c in cases] == ["Q014", "ADV-017", "ADV-001"] and all(c["question"] for c in cases)


# Run guards ------------------------------------------------------------------------------------


def test_a_frozen_pinned_matching_configuration_is_allowed(tmp_path):
    ctx = frozen_ctx(tmp_path)
    ready = e2e.check_run_allowed(1, ctx)
    assert len(ready["cases"]) == 6 and ready["commit"] == "abc123" and ready["ledger_before_usd"] == 0
    assert e2e.check_smoke_allowed(ctx)["frozen"]["decision"] == e2e.DECISION


def test_the_guards_refuse_before_any_call(tmp_path):
    ctx = frozen_ctx(tmp_path)
    with pytest.raises(e2e.GuardError, match="at most|two runs"):
        e2e.check_run_allowed(3, ctx)
    with pytest.raises(e2e.GuardError, match="TAXY_RUN_API_TESTS"):
        e2e.check_run_allowed(1, e2e.Context(**{**ctx.__dict__, "environ": {"TAXY_RUN_API_TESTS": "1"}}))
    with pytest.raises(e2e.GuardError, match="not clean"):
        e2e.check_run_allowed(1, e2e.Context(**{**ctx.__dict__, "git": lambda: {"clean": False, "commit": "x"}}))

    def down():
        raise e2e.GuardError("the database does not answer")
    with pytest.raises(e2e.GuardError, match="database"):
        e2e.check_run_allowed(1, e2e.Context(**{**ctx.__dict__, "db": down}))


def test_a_changed_live_configuration_or_config_file_is_refused(tmp_path):
    ctx = frozen_ctx(tmp_path)
    changed = copy.deepcopy(live())
    changed["prompts"]["prompt_version"] = "gen-prompt/10"
    with pytest.raises(e2e.GuardError, match="differs from"):
        e2e.check_run_allowed(1, e2e.Context(**{**ctx.__dict__, "live": lambda: changed}))
    other = copy.deepcopy(live())
    other["overrides"] = {"x": 1}  # also no longer equal to config_p7.json
    with pytest.raises(e2e.GuardError, match="config_p7"):
        e2e.check_run_allowed(1, e2e.Context(**{**ctx.__dict__, "live": lambda: other}))
    ctx.paths["config_p8"].write_text(ctx.paths["config_p8"].read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(e2e.GuardError, match="config_p8.json does not match"):
        e2e.check_run_allowed(1, ctx)


def test_a_changed_input_file_is_refused_at_run_time(tmp_path):
    ctx = frozen_ctx(tmp_path)
    ctx.paths["labels_dev_v1"].write_text("{}\n", encoding="utf-8")
    with pytest.raises(e2e.GuardError, match="labels_dev_v1.jsonl does not match"):
        e2e.check_run_allowed(1, ctx)


def test_run_two_needs_a_counted_run_one_and_a_run_is_never_repeated(tmp_path):
    ctx = frozen_ctx(tmp_path)
    with pytest.raises(e2e.GuardError, match="run 2 needs a complete run 1"):
        e2e.check_run_allowed(2, ctx)
    run_fake(ctx)
    with pytest.raises(e2e.GuardError, match="never repeated"):
        e2e.check_run_allowed(1, ctx)
    assert e2e.check_run_allowed(2, ctx)["cases"]


def test_a_marker_without_a_record_blocks_a_second_run(tmp_path):
    ctx = frozen_ctx(tmp_path)
    ctx.results_dir.mkdir()
    e2e.write_record(e2e.marker_path(1, ctx.results_dir), {"started_at": "x"})
    with pytest.raises(e2e.GuardError, match="never repeated"):
        e2e.check_run_allowed(1, ctx)
    with pytest.raises(e2e.GuardError, match="needs a first run"):
        e2e.check_run_allowed(1, ctx, rerun=True)  # no record, so not a void record either


def test_a_second_smoke_run_is_refused(tmp_path):
    ctx = frozen_ctx(tmp_path)
    smoke_fake(ctx)
    with pytest.raises(e2e.GuardError, match="already been made"):
        e2e.check_smoke_allowed(ctx)


def void_record(**extra):
    return {"review": "void", "cases": [], "stopped_on_api_error": {"case": "N01", "error": {"error_type": "X"}}} | extra


def test_re_run_guards(tmp_path):
    ctx = frozen_ctx(tmp_path)
    ctx.results_dir.mkdir()
    first = e2e.result_path(1, ctx.results_dir)
    with pytest.raises(e2e.GuardError, match="needs a first run"):
        e2e.check_run_allowed(1, ctx, rerun=True)
    first.write_text(json.dumps(void_record(stopped_on_api_error={"case": "N01"})), encoding="utf-8")  # no error record
    with pytest.raises(e2e.GuardError, match="not a complete void record"):
        e2e.check_run_allowed(1, ctx, rerun=True)
    first.write_text(json.dumps(void_record(auto_pass=False)), encoding="utf-8")  # a verdict: not void
    with pytest.raises(e2e.GuardError, match="not a complete void record"):
        e2e.check_run_allowed(1, ctx, rerun=True)
    first.write_text("{not json", encoding="utf-8")
    with pytest.raises(e2e.GuardError, match="not a complete void record"):
        e2e.check_run_allowed(1, ctx, rerun=True)
    first.write_text(json.dumps(void_record()), encoding="utf-8")
    assert e2e.check_run_allowed(1, ctx, rerun=True)["cases"]
    with pytest.raises(e2e.GuardError, match="never repeated"):
        e2e.check_run_allowed(1, ctx)
    with pytest.raises(e2e.GuardError, match="a void run does not count"):
        e2e.check_run_allowed(2, ctx)
    e2e.write_record(e2e.marker_path(1, ctx.results_dir, rerun=True), {"started_at": "x"})
    with pytest.raises(e2e.GuardError, match="already been made"):
        e2e.check_run_allowed(1, ctx, rerun=True)


def test_ledger_over_three_dollars_is_refused_and_a_marker_without_a_record_counts_at_its_cap(tmp_path):
    ctx = frozen_ctx(tmp_path)
    d = ctx.results_dir
    d.mkdir()
    e2e.write_record(e2e.smoke_path(d), {"cost_usd": 0.02})
    e2e.write_record(e2e.result_path(1, d), void_record(cost_usd=0.93))
    assert e2e.ledger(d)["spent_usd"] == pytest.approx(0.95)
    e2e.write_record(e2e.marker_path(2, d), {"started_at": "x"})  # crashed: no record, so the cap counts
    assert e2e.ledger(d)["spent_usd"] == pytest.approx(0.95 + 1.10)
    with pytest.raises(e2e.GuardError, match="ledger"):
        e2e.check_ledger(d, e2e.RUN_CAP_USD)  # 2.05 + 1.10 passes 3.00
    assert e2e.check_ledger(d, 0.05) == pytest.approx(2.05)
    ctx2 = frozen_ctx(tmp_path / "x")
    ctx2.results_dir.mkdir()
    e2e.write_record(e2e.result_path(1, ctx2.results_dir), void_record(cost_usd=1.90))
    e2e.write_record(e2e.marker_path(1, ctx2.results_dir, rerun=True), {"started_at": "x"})  # re-run crashed
    with pytest.raises(e2e.GuardError):
        e2e.check_run_allowed(1, ctx2, rerun=True)


def test_a_run_that_cannot_fit_the_ledger_is_refused_before_its_marker(tmp_path):
    ctx = frozen_ctx(tmp_path)
    ctx.results_dir.mkdir()
    e2e.write_record(e2e.smoke_path(ctx.results_dir), {"cost_usd": 2.00})
    calls = []
    with pytest.raises(e2e.GuardError, match="ledger"):
        e2e.run(1, ctx=ctx, clients={}, answer_fn=lambda *a: calls.append(a), resources=fake_resources())
    assert calls == [] and not e2e.marker_path(1, ctx.results_dir).exists()


# Fake runs -------------------------------------------------------------------------------------


def api_error():
    return llm.APIError("InternalServerError", status_code=529, api_error_type="overloaded_error",
                        message=SECRET, request_id="req_x")


class CountingGen:
    """Declines every question; raises an APIError on call `fail_at`; `extra_cost` is added to every answer."""
    model = llm.MODEL

    def __init__(self, fail_at=None):
        self.fail_at, self.calls = fail_at, 0

    def create(self, params):
        self.calls += 1
        if self.calls == self.fail_at:
            raise api_error()
        return tt.ScriptLLM(tt.final([], status="not_covered", gaps=["The passages do not settle it."])).create(params)


class FailingVerifier:
    model = llm.VERIFIER_MODEL

    def __init__(self):
        self.calls = 0

    def create(self, params):
        self.calls += 1
        raise api_error()


class FailingTagger(tt.FakeTagger):
    def __init__(self):
        super().__init__([])
        self.calls = 0

    def create(self, params):
        self.calls += 1
        raise api_error()


class CountingVerifier(tt.FakeVerifier):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def create(self, params):
        self.calls += 1
        return super().create(params)


class FailingEmbedder:
    """Embeds fine until call `fail_at`, then raises the embedding error the real client raises."""

    def __init__(self, fail_at=1):
        self.fail_at, self.calls = fail_at, 0
        self.model, self.dims = "fake/embedder", 3

    def embed_query(self, text):
        self.calls += 1
        if self.calls == self.fail_at:
            raise EmbeddingError("RateLimitError (HTTP 429)")
        return [0.0, 0.0, 1.0]


def fake_answer(question, config, clients, retriever, store):
    return gen.generate(question, retriever=retriever, store=store, log_dir=None, config=config, llm=clients["llm"],
                        verifier=clients["verifier"], tagger=clients["tagger"])


def fake_resources(embedder=None):
    def make(stop):
        retriever = tt.FakeRetriever()
        retriever.client = e2e.StopOnEmbeddingError(embedder or tt.FakeEmbedder(), stop)
        return retriever, tt.STORE, lambda: None
    return make


def fake_clients(*, llm=None, verifier=None, tagger=None):
    return {"llm": llm or CountingGen(), "verifier": verifier or tt.FakeVerifier(), "tagger": tagger or tt.FakeTagger([])}


def run_fake(ctx, *, run_no=1, rerun=False, embedder=None, answer_fn=None, echo=None, **clients):
    c = fake_clients(**clients)
    record = e2e.run(run_no, rerun=rerun, ctx=ctx, clients=c, answer_fn=answer_fn or fake_answer,
                     resources=fake_resources(embedder), echo=echo or (lambda s: None))
    return record, c


def smoke_fake(ctx, *, answer_fn=None, **clients):
    cases = [{"id": f"S{i}", "subset": "smoke", "category": "x", "question": f"Fake smoke question {i}?"} for i in (1, 2, 3)]
    return e2e.smoke(ctx=ctx, cases=cases, clients=fake_clients(**clients), answer_fn=answer_fn or fake_answer,
                     resources=fake_resources(), echo=lambda s: None)


def test_a_complete_fake_run_writes_a_marker_first_then_one_record_with_no_verdict(tmp_path):
    ctx = frozen_ctx(tmp_path)
    seen = []

    def answer(question, config, clients, retriever, store):
        seen.append(e2e.marker_path(1, ctx.results_dir).exists() and not e2e.result_path(1, ctx.results_dir).exists())
        return fake_answer(question, config, clients, retriever, store)

    record, clients = run_fake(ctx, answer_fn=answer)
    assert seen == [True] * 6  # the marker exists before each paid call; the record only afterwards
    assert e2e.read_record(e2e.result_path(1, ctx.results_dir)) == record
    assert record["review"] == "pending" and record["stopped_on_api_error"] is None and record["stopped_on_cost"] is None
    assert [c["id"] for c in record["cases"]] == record["case_ids"] == [c["id"] for c in ctx.cases()]
    assert clients["llm"].calls == 6 and e2e.counted_run(1, ctx.results_dir) == record
    marker = json.loads(e2e.marker_path(1, ctx.results_dir).read_text(encoding="utf-8"))
    assert marker["config_sha256"] == ctx.pins["config_p8"] and marker["cap_usd"] == 1.10
    banned = {"auto_pass", "checks", "verdict", "pass", "g3", "review_verdict"}
    assert not banned & set(record) and all(not banned & set(c) and "review" not in c for c in record["cases"])
    row = record["cases"][0]
    assert {"status", "refusal_reason", "claims", "gaps", "computations", "passages", "notes", "disclosures", "rendered",
            "verification", "tool_uses", "cost_usd", "latency_s", "record_id"} <= set(row)
    assert isinstance(row["latency_s"], float) and row["latency_s"] >= 0


def test_records_are_written_once(tmp_path):
    ctx = frozen_ctx(tmp_path)
    run_fake(ctx)
    with pytest.raises(FileExistsError):
        e2e.write_record(e2e.result_path(1, ctx.results_dir), {})
    with pytest.raises(FileExistsError):
        e2e.write_record(e2e.marker_path(1, ctx.results_dir), {})
    assert e2e.ledger(ctx.results_dir)["entries"].keys() == {"e2e_run1"}


def test_the_runner_prints_ids_statuses_and_costs_only(tmp_path, capsys):
    ctx = frozen_ctx(tmp_path)
    run_fake(ctx, echo=print)
    out = capsys.readouterr()
    assert all(q not in out.out + out.err for q in FAKE_QUESTIONS.values())
    assert re.search(r"^N01: not_covered, \$0\.\d+$", out.out, re.M)
    assert FAKE_QUESTIONS["N01"] not in json.dumps(e2e.status(ctx))
    assert e2e.main(["--status"], ctx=ctx) == 0
    printed = capsys.readouterr().out
    assert all(q not in printed for q in FAKE_QUESTIONS.values()) and json.loads(printed)["runs"]["1"]["counts"] is True


# The API-error stop (D-128): the four clients, the database, an unexpected error -----------------


def assert_void(record, ctx, *, kind):
    stop = record["stopped_on_api_error"]
    assert record["review"] == "void" and stop["kind"] == kind
    assert stop["cases_planned"] == 6 and 1 <= stop["cases_written"] <= 6
    assert "SECRETSECRET" not in json.dumps(record)
    assert e2e.is_void_record(e2e.read_record(e2e.result_path(1, ctx.results_dir)))
    assert e2e.counted_run(1, ctx.results_dir) is None
    assert "auto_pass" not in record and all("checks" not in c and "review" not in c for c in record["cases"])


def test_a_generation_api_error_stops_after_that_case_and_voids_the_run(tmp_path):
    ctx = frozen_ctx(tmp_path)
    record, clients = run_fake(ctx, llm=CountingGen(fail_at=3))
    assert_void(record, ctx, kind="llm")
    stop = record["stopped_on_api_error"]
    assert (stop["cases_written"], stop["case"]) == (3, record["case_ids"][2])
    assert stop["error"]["error_type"] == "InternalServerError" and stop["error"]["status_code"] == 529
    assert "[redacted]" in stop["error"]["message"] and clients["llm"].calls == 3  # no call after the error
    assert len(record["cases"]) == 3


def test_a_verifier_api_error_stops_the_run(tmp_path):
    ctx = frozen_ctx(tmp_path)
    verifier = FailingVerifier()
    record, clients = run_fake(ctx, verifier=verifier)
    assert_void(record, ctx, kind="verifier")
    assert record["stopped_on_api_error"]["cases_written"] == 1 and clients["llm"].calls == 1
    assert verifier.calls >= 1


def test_a_tagger_api_error_stops_the_run(tmp_path):
    ctx = frozen_ctx(tmp_path)
    tagger = FailingTagger()
    record, clients = run_fake(ctx, tagger=tagger)
    assert_void(record, ctx, kind="tagger")
    # the tagger runs alongside the first case, so calls in flight may finish; no call for a later case
    assert record["stopped_on_api_error"]["cases_written"] == 1 and clients["llm"].calls <= 1 and 1 <= tagger.calls <= 3


def test_an_embedding_api_error_stops_the_run_and_no_embedding_call_follows(tmp_path):
    ctx = frozen_ctx(tmp_path)
    embedder = FailingEmbedder(fail_at=2)
    record, clients = run_fake(ctx, embedder=embedder)
    assert_void(record, ctx, kind="embedding")
    stop = record["stopped_on_api_error"]
    assert (stop["cases_written"], stop["case"]) == (1, record["case_ids"][1])  # the 2nd case raised and is not written
    assert stop["error"] == {"error_type": "EmbeddingError", "status_code": None, "api_error_type": None,
                             "message": "RateLimitError (HTTP 429)", "request_id": None}
    assert embedder.calls == 2 and clients["llm"].calls == 1  # no call after the stop


def test_a_database_error_stops_the_run_without_leaking_its_message(tmp_path):
    ctx = frozen_ctx(tmp_path)
    calls = []

    def answer(question, config, clients, retriever, store):
        calls.append(question)
        if len(calls) == 2:
            raise psycopg.OperationalError("connection to server at db.internal.example port 5434 failed")
        return fake_answer(question, config, clients, retriever, store)

    record, clients = run_fake(ctx, answer_fn=answer)
    assert_void(record, ctx, kind="database")
    stop = record["stopped_on_api_error"]
    assert stop["error"]["error_type"] == "OperationalError" and stop["error"]["message"] is None
    assert "db.internal" not in json.dumps(record) and len(calls) == 2 and stop["cases_written"] == 1
    assert clients["llm"].calls == 1


def test_a_database_that_is_down_at_the_first_case_voids_the_run(tmp_path):
    ctx = frozen_ctx(tmp_path)

    def down(stop):
        raise psycopg.OperationalError("refused")

    record = e2e.run(1, ctx=ctx, clients=fake_clients(), answer_fn=fake_answer, resources=down, echo=lambda s: None)
    assert record["review"] == "void" and record["stopped_on_api_error"]["kind"] == "database"
    assert record["stopped_on_api_error"]["cases_written"] == 0 and record["cases"] == []
    assert e2e.is_void_record(record)


def test_an_unexpected_error_leaves_a_void_record_not_a_crash(tmp_path):
    ctx = frozen_ctx(tmp_path)

    def answer(question, config, clients, retriever, store):
        raise KeyError("bug")

    record = e2e.run(1, ctx=ctx, clients=fake_clients(), answer_fn=answer, resources=fake_resources(), echo=lambda s: None)
    assert record["stopped_on_api_error"]["kind"] == "unexpected"
    assert record["stopped_on_api_error"]["error"]["error_type"] == "KeyError" and record["review"] == "void"


def test_an_unexpected_error_void_is_not_re_run_automatically(tmp_path, capsys):
    ctx = frozen_ctx(tmp_path)

    def answer(question, config, clients, retriever, store):
        raise KeyError("bug")

    void = e2e.run(1, ctx=ctx, clients=fake_clients(), answer_fn=answer, resources=fake_resources(), echo=lambda s: None)
    assert e2e.is_void_record(void) and void["stopped_on_api_error"]["kind"] == "unexpected"
    with pytest.raises(e2e.GuardError, match="Kene and the overseer decide"):
        e2e.check_run_allowed(1, ctx, rerun=True)
    calls = []
    with pytest.raises(e2e.GuardError, match="unexpected"):
        e2e.run(1, rerun=True, ctx=ctx, clients=fake_clients(), answer_fn=lambda *a: calls.append(a),
                resources=fake_resources(), echo=lambda s: None)
    assert calls == [] and not e2e.marker_path(1, ctx.results_dir, rerun=True).exists()
    assert e2e.main(["--run", "1", "--rerun-after-void"], ctx=ctx) == 2
    assert "Kene and the overseer decide" in capsys.readouterr().err
    assert e2e.main(["--run", "1"], run_fn=lambda n, rerun: void) == 3
    assert "not re-run automatically" in capsys.readouterr().err
    # an API-error void is still re-runnable once
    ctx2 = frozen_ctx(tmp_path / "api")
    run_fake(ctx2, llm=CountingGen(fail_at=1))
    assert e2e.check_run_allowed(1, ctx2, rerun=True)["cases"]


def test_the_re_run_after_void_writes_its_own_record_and_counts_as_the_run(tmp_path):
    ctx = frozen_ctx(tmp_path)
    run_fake(ctx, llm=CountingGen(fail_at=1))
    record, _ = run_fake(ctx, rerun=True)
    assert record["rerun_after_void"] is True and record["review"] == "pending"
    assert e2e.result_path(1, ctx.results_dir, rerun=True).exists() and e2e.marker_path(1, ctx.results_dir, rerun=True).exists()
    assert e2e.counted_run(1, ctx.results_dir) == record
    assert e2e.check_run_allowed(2, ctx)["cases"]  # the complete re-run counts as run 1
    with pytest.raises(e2e.GuardError, match="already been made"):
        e2e.check_run_allowed(1, ctx, rerun=True)


def test_main_exit_codes(tmp_path, capsys):
    ctx = frozen_ctx(tmp_path)
    void, _ = run_fake(ctx, llm=CountingGen(fail_at=2))
    assert e2e.main(["--run", "1"], run_fn=lambda n, rerun: void) == 3
    err = capsys.readouterr().err
    assert "STOPPED at an error during" in err and "void" in err and "SECRETSECRET" not in err
    done = dict(void, stopped_on_api_error=None, review="pending")
    assert e2e.main(["--run", "1"], run_fn=lambda n, rerun: done) == 0
    stopped = dict(done, stopped_on_cost={"before": "N01", "spent_usd": 1.0, "cap_usd": 1.1}, review="incomplete")
    assert e2e.main(["--run", "1"], run_fn=lambda n, rerun: stopped) == 4
    with pytest.raises(SystemExit):
        e2e.main(["--status", "--rerun-after-void"])


# The cost stops --------------------------------------------------------------------------------


def test_the_per_run_cost_stop():
    assert e2e.projected_over_cap(1.00, [0.03]) is False
    assert e2e.projected_over_cap(1.07, [0.03]) is True      # 1.07 + the 0.036 floor passes $1.10
    assert e2e.projected_over_cap(1.00, [0.03, 0.12]) is True  # the dearest case so far
    assert e2e.projected_over_cap(0.01, [0.0], 0.05, 0.0) is False and e2e.projected_over_cap(0.04, [0.02], 0.05, 0.0) is True


def expensive(cost):
    def answer(question, config, clients, retriever, store):
        a = fake_answer(question, config, clients, retriever, store)
        a.cost_usd = cost
        return a
    return answer


def test_a_run_stopped_on_cost_is_incomplete_has_no_verdict_and_does_not_count(tmp_path):
    ctx = frozen_ctx(tmp_path)
    record, _ = run_fake(ctx, answer_fn=expensive(0.50))
    assert record["review"] == "incomplete" and record["stopped_on_api_error"] is None
    assert record["stopped_on_cost"]["before"] == record["case_ids"][2] and len(record["cases"]) == 2
    assert record["cost_usd"] == pytest.approx(1.0, abs=0.01)
    assert e2e.counted_run(1, ctx.results_dir) is None and not e2e.is_void_record(record)
    with pytest.raises(e2e.GuardError, match="never repeated"):
        e2e.check_run_allowed(1, ctx)
    with pytest.raises(e2e.GuardError, match="needs a first run|not a complete void"):
        e2e.check_run_allowed(1, ctx, rerun=True)
    with pytest.raises(e2e.GuardError, match="complete run 1"):
        e2e.check_run_allowed(2, ctx)
    with pytest.raises(sc.run_e2e.GuardError, match="no complete run record"):
        sc.check_record(record)


def test_the_smoke_run_stops_at_its_own_cap(tmp_path):
    ctx = frozen_ctx(tmp_path)
    ok = smoke_fake(ctx)
    assert ok["smoke"] is True and ok["review"] == "pending" and len(ok["cases"]) == 3 and ok["cap_usd"] == 0.05
    assert e2e.smoke_path(ctx.results_dir).exists() and e2e.smoke_marker_path(ctx.results_dir).exists()
    assert "auto_pass" not in ok
    ctx2 = frozen_ctx(tmp_path / "two")
    stopped = smoke_fake(ctx2, answer_fn=expensive(0.03))
    assert stopped["review"] == "incomplete" and len(stopped["cases"]) == 1 and stopped["stopped_on_cost"]["cap_usd"] == 0.05


def test_a_smoke_api_error_voids_the_smoke_record(tmp_path):
    ctx = frozen_ctx(tmp_path)
    record = smoke_fake(ctx, llm=CountingGen(fail_at=2))
    assert record["review"] == "void" and record["stopped_on_api_error"]["case"] == "S2" and e2e.is_void_record(record)


# The scorer on hand-built records (Section 3.4, D-139) -------------------------------------------

K_OK = {f"K{i}": True for i in range(1, 7)}


def comp(cid="C1", result="195000.00", shown=True, sources=("nta2025:s148",), checks=None, scenario="vat_on_supply"):
    return {"id": cid, "scenario": scenario, "shown": shown, "result": result, "sources": list(sources), "what": "VAT on a supply",
            "result_text": "VAT of 195,000", "working": ["2,600,000 x 7.5% = 195,000"], "checks": checks or dict(K_OK)}


def row(case, subset, status="answered", claims=(), gaps=(), comps=(), passages=("nta2025:s148",), notes=(),
        reason=None, category="vat", latency=1.0, cost=0.01, verification=None):
    return {"id": case, "subset": subset, "category": category, "question": "fake", "status": status,
            "refusal_reason": reason, "flags": [], "notes": list(notes),
            "claims": [{"no": i, "text": t, "chunk_ids": list(c)} for i, (t, c) in enumerate(claims, 1)],
            "gaps": list(gaps), "computations": list(comps),
            "passages": [{"label": f"P{i}", "chunk_id": p, "reasons": []} for i, p in enumerate(passages, 1)],
            "verification": verification, "tool_uses": [], "disclosures": [], "rendered": "x" + gen.DISCLAIMER,
            "cost_usd": cost, "latency_s": latency, "record_id": "r", "error": None}


def record(rows, run=1):
    return {"run": run, "rerun_after_void": False, "review": "pending", "stopped_on_api_error": None,
            "stopped_on_cost": None, "case_ids": [r["id"] for r in rows], "cases": rows}


LABELS = {
    "T1": {"gold_units": ["nta2025:s148"], "expected_citations": ["nta2025:s148"], "acceptable_citations": [], "required_notes": ["scope:x"]},
    "N1": {"gold_units": ["nta2025:s56"], "expected_citations": ["nta2025:s148"], "acceptable_citations": [],
           "expected_calc": {"scenario": "vat_on_supply", "figure": "195000.00", "currency": "NGN", "inputs": {}, "working": []}},
    "N2": {"gold_units": ["nta2025:s999"]}, "R1": {"expected_status": "refused"}, "R2": {"expected_status": "not_covered"}}


def base_rows():
    return [
        row("T1", "T", claims=[("Claim one.", ["nta2025:s148"]), ("Claim two.", ["nta2025:s148", "nta2025:s152"])],
            gaps=["A gap that states law.", "A gap about missing facts."], notes=["scope:x"],
            verification={"claims_removed": 1, "claims_moved": 2}),
        row("N1", "N", claims=[("Claim three.", ["nta2025:s148"])], comps=[comp()]),
        row("N2", "N", status="not_covered", gaps=["Not covered."]),
        row("R1", "R", status="refused", reason="no_such_section"),
        row("R2", "R", status="not_covered")]


def good_rows():
    """base_rows with N2 answered, so that (a) to (d) all pass."""
    rows = base_rows()
    rows[2] = row("N2", "N", claims=[("Claim four.", ["nta2025:s56"])])
    return rows


def ctx_rows():
    """Rows with the ids of the fake sealed files made by make_ctx."""
    return [row("T01", "T", claims=[("Claim one.", ["nta2025:s148"])]), row("T02", "T", claims=[("Claim two.", ["nta2025:s56"])]),
            row("N01", "N", claims=[("Claim three.", ["nta2025:s148"])]),
            row("N02", "N", claims=[("Claim four.", ["nta2025:s56"])], comps=[comp()]),
            row("R01", "R", status="refused", reason="section_outside_v1"), row("R02", "R", status="not_covered")]


def full_review(rows, **over):
    """Every shown item supported and every citation accurate; gaps: the first states law."""
    review = {"run": 1, "reviewer": "r", "claims": {}, "gaps": {}, "computations": {}, "adjudications": {}}
    for r in rows:
        if r["subset"] not in sc.IN_SCOPE:
            continue
        for item in sc.shown_items(r):
            if item["kind"] == "claim":
                review["claims"][item["key"]] = {"verdict": "supported", "reason": "r",
                                                 "citations": {c: "accurate" for c in item["cites"]}}
            elif item["kind"] == "gap":
                law = item["key"].endswith(":gap:1")
                review["gaps"][item["key"]] = {"states_law": law, "verdict": "supported" if law else None, "reason": "r"}
            else:
                review["computations"][item["key"]] = {"figure_correct": True, "reason": "r",
                                                       "rate_citations": {c: "accurate" for c in item["cites"]}}
    return review | over


def score(rows=None, review=None, labels=LABELS):
    rows = rows or base_rows()
    return sc.score_run(record(rows), review or full_review(rows), labels, resamples=200)


def test_unit_counting_for_a_and_b_under_d139():
    s = score()
    # (a): T1 has 2 claims and 1 gap that states law; N1 has 1 claim and 1 shown computed item = 5 items
    assert (s["a"]["supported"], s["a"]["total"]) == (5, 5)
    # (b): T1 claim 1 has 1 citation, claim 2 has 2; N1 claim has 1; its computed item names 1 rate section = 5
    assert (s["b"]["accurate"], s["b"]["total"]) == (5, 5)
    assert s["a"]["by_subset"]["T"]["total"] == 3 and s["a"]["by_subset"]["N"]["total"] == 2
    # (c) passes; (d): N2 is refused, 1 of 3 in-scope cases, over 10%
    assert s["pass"] == {"a": True, "b": True, "c": True, "d": False} and s["g3_run_pass"] is False


def test_a_gap_that_does_not_state_law_is_not_counted_and_one_that_does_is_counted_as_judged():
    rows = base_rows()
    review = full_review(rows)
    review["gaps"]["T1:gap:1"] = {"states_law": True, "verdict": "unsupported", "reason": "no passage says it"}
    s = score(rows, review)
    assert (s["a"]["supported"], s["a"]["total"]) == (4, 5) and s["a"]["pass"] is False
    review["gaps"]["T1:gap:2"] = {"states_law": True, "verdict": "supported", "reason": "r"}
    assert score(rows, review)["a"]["total"] == 6


def test_only_supported_counts_towards_a():
    for verdict in ("partly_supported", "unsupported", "contradicted"):
        rows = base_rows()
        review = full_review(rows)
        review["claims"]["T1:claim:1"]["verdict"] = verdict
        assert score(rows, review)["a"]["supported"] == 4, verdict


def test_citations_are_judged_one_by_one_and_support_not_cited_is_listed():
    rows = base_rows()
    review = full_review(rows)
    review["claims"]["T1:claim:2"]["citations"]["nta2025:s152"] = "inaccurate"
    review["claims"]["T1:claim:2"] |= {"verdict": "unsupported", "support_not_cited": True}
    s = score(rows, review)
    assert (s["b"]["accurate"], s["b"]["total"]) == (4, 5) and s["a"]["support_not_cited"] == ["T1:claim:2"]


def test_a_computed_item_is_supported_only_when_k1_to_k6_passed_and_the_figure_equals_the_label():
    rows = base_rows()
    assert score(rows)["a"]["supported"] == 5
    rows[1]["computations"][0]["result"] = "195001.00"          # not the hand-worked figure of the label
    assert score(rows)["a"]["supported"] == 4
    rows = base_rows()
    rows[1]["computations"][0]["checks"]["K4"] = False         # a failed check
    assert score(rows)["a"]["supported"] == 4
    rows = base_rows()
    s = score(rows, labels=dict(LABELS, N1={"gold_units": []}))  # no figure in the label: the reviewer's own working
    assert s["a"]["supported"] == 5
    review = full_review(rows)
    review["computations"]["N1:calc:C1"]["figure_correct"] = False
    assert score(rows, review, labels=dict(LABELS, N1={"gold_units": []}))["a"]["supported"] == 4


CIT = dict(scenario="company_income_tax", cid="C2", result="10800000.00", sources=("nta2025:s56",))


def two_calc_rows():
    rows = base_rows()
    rows[1]["computations"].append(comp(**CIT))
    return rows


def test_a_computed_item_is_matched_to_the_label_entry_of_its_own_scenario():
    rows = two_calc_rows()
    labels = dict(LABELS, N1={"gold_units": [], "expected_calc": [
        {"scenario": "company_income_tax", "figure": "10,800,000.00"},
        {"scenario": "vat_on_supply", "figure": "195000.00"}]})
    assert score(rows, labels=labels)["a"]["supported"] == 6  # each figure checked against its own entry
    rows[1]["computations"][0]["result"] = "10800000.00"      # the VAT item now carries the CIT figure
    assert score(rows, labels=labels)["a"]["supported"] == 5


def test_a_second_correct_computation_with_no_label_entry_uses_the_reviewers_figure():
    rows = two_calc_rows()  # LABELS["N1"] has an entry for vat_on_supply only
    review = full_review(rows)
    assert score(rows, review)["a"]["supported"] == 6
    review["computations"]["N1:calc:C2"]["figure_correct"] = False
    s = score(rows, review)
    assert s["a"]["supported"] == 5 and {i["key"]: i["supported"] for i in s["items"]}["N1:calc:C2"] is False
    assert sc.label_figures(LABELS["N1"], "company_income_tax") is None
    assert [str(x) for x in sc.label_figures(LABELS["N1"], "vat_on_supply")] == ["195000.00"]


def test_a_wrong_figure_fails_even_when_the_reviewer_marked_it_correct():
    rows = two_calc_rows()
    rows[1]["computations"][0]["result"] = "195000.01"
    review = full_review(rows)  # figure_correct is True for both
    s = score(rows, review)
    assert {i["key"]: i["supported"] for i in s["items"]}["N1:calc:C1"] is False
    assert {i["key"]: i["supported"] for i in s["items"]}["N1:calc:C2"] is True


def test_a_withheld_computed_item_is_not_shown_so_not_counted():
    rows = base_rows()
    rows[1]["computations"][0]["shown"] = False
    s = score(rows)
    assert (s["a"]["total"], s["b"]["total"]) == (4, 4)


def test_the_label_figure_wins_over_the_reviewers_working_and_both_are_reported():
    rows = base_rows()
    rows[1]["computations"][0]["result"] = "195001.00"
    review = full_review(rows)  # the reviewer says the figure is correct, the label disagrees
    assert score(rows, review)["a"]["supported"] == 4


def test_adjudication_overlays_the_reviewer_and_both_figures_are_given():
    rows = base_rows()
    review = full_review(rows)
    review["claims"]["T1:claim:1"]["verdict"] = "unsupported"
    review["claims"]["T1:claim:2"]["citations"]["nta2025:s152"] = "inaccurate"
    review["adjudications"] = {"T1:claim:1": {"verdict": "supported", "reason": "the overseer's reading"},
                               "T1:claim:2": {"citations": {"nta2025:s152": "accurate"}, "reason": "second reading"}}
    s = score(rows, review)
    assert s["a"]["supported"] == 5 and s["reviewer_own"]["a"]["supported"] == 4
    assert s["b"]["accurate"] == 5 and s["reviewer_own"]["b"]["accurate"] == 4
    assert s["adjudicated_items"] == ["T1:claim:1", "T1:claim:2"]


@pytest.mark.parametrize("break_it, message", [
    (lambda r: r["claims"].pop("T1:claim:1"), "T1:claim:1: no review entry"),
    (lambda r: r["claims"]["T1:claim:1"].update(verdict=None), "T1:claim:1: no valid verdict"),
    (lambda r: r["claims"]["T1:claim:2"]["citations"].pop("nta2025:s152"), "no verdict for citation nta2025:s152"),
    (lambda r: r["claims"]["T1:claim:2"]["citations"].update({"nta2025:s152": "maybe"}), "no verdict for citation"),
    (lambda r: r["claims"]["T1:claim:1"]["citations"].update({"nta2025:s56": "accurate"}), "is not cited"),
    (lambda r: r["gaps"]["T1:gap:2"].update(states_law=None), "states_law not recorded"),
    (lambda r: r["gaps"]["T1:gap:1"].update(verdict=None), "no valid verdict for a gap that states law"),
    (lambda r: r["computations"]["N1:calc:C1"].update(figure_correct=None), "figure_correct not recorded"),
    (lambda r: r["computations"]["N1:calc:C1"]["rate_citations"].pop("nta2025:s148"), "no verdict for citation"),
    (lambda r: r["claims"].update({"T1:claim:9": {"verdict": "supported"}}), "T1:claim:9: not a shown claim"),
    (lambda r: r["claims"].update({"R2:claim:1": {"verdict": "supported"}}), "R2:claim:1: not a shown claim"),
    (lambda r: r["adjudications"].update({"T1:claim:1": {"verdict": "supported"}}), "adjudication without a reason"),
    (lambda r: r["adjudications"].update({"T1:claim:7": {"reason": "x"}}), "adjudication of an item that is not shown"),
])
def test_an_incomplete_or_invalid_review_is_refused(break_it, message):
    rows = base_rows()
    review = full_review(rows)
    break_it(review)
    with pytest.raises(sc.ReviewIncomplete, match=re.escape(message)):
        score(rows, review)


def test_the_empty_template_is_refused_by_the_scorer():
    rows = base_rows()
    template = rs.build_template(record(rows))
    assert template["claims"]["T1:claim:2"]["citations"] == {"nta2025:s148": None, "nta2025:s152": None}
    with pytest.raises(sc.ReviewIncomplete):
        score(rows, template)


def test_c_an_r_case_fails_when_answered_or_when_any_item_is_shown():
    assert sc.score_c([row("R1", "R", status="refused"), row("R2", "R", status="not_covered")], LABELS)["pass"] is True
    cases = {"answered": row("R1", "R", claims=[("c", ["x"])]),
             "claim shown in a refusal": row("R1", "R", status="refused", claims=[("c", ["x"])]),
             "computed item shown": row("R1", "R", status="not_covered", comps=[comp()])}
    for name, r in cases.items():
        c = sc.score_c([r, row("R2", "R", status="not_covered")], LABELS)
        assert c["pass"] is False and c["failed_ids"] == ["R1"], name
    ok = sc.score_c([row("R1", "R", status="error"), row("R2", "R", status="not_covered", comps=[comp(shown=False)])], LABELS)
    assert ok["pass"] is True and ok["error_cases"] == ["R1"]  # an error gives no answer; a withheld item is not shown
    assert sc.score_c([row("R1", "R", status="refused", category="R2"), row("R2", "R", status="refused", category="R4")],
                      LABELS)["exact_status_match"] == 1


def test_d_false_refusals_and_the_ten_percent_threshold():
    def rows(k, status="refused"):
        return [row(f"T{i}", "T", status=status if i < k else "answered", claims=[("c", ["x"])]) for i in range(50)]
    assert sc.score_d(rows(5))["pass"] is True and sc.score_d(rows(5))["false_refusals"] == 5
    assert sc.score_d(rows(6))["pass"] is False
    for status in ("refused", "not_covered", "error"):
        assert sc.score_d(rows(1, status))["false_refusals"] == 1, status
    answered_empty = [row("T1", "T", status="answered")]
    assert sc.score_d(answered_empty)["false_refusals"] == 1  # nothing shown: the user got no answer
    assert sc.score_d([row("T1", "T", claims=[("c", ["x"])])])["false_refusals"] == 0
    assert sc.score_d([row("R1", "R", status="refused")])["cases"] == 0  # R cases are not in (d)
    assert sc.score_d(rows(3))["causes"]["T0"] == {"status": "refused", "refusal_reason": None}


def test_the_95_percent_thresholds_are_exact_integer_rules():
    assert sc.passes_pct(19, 20, 95) and not sc.passes_pct(18, 20, 95) and not sc.passes_pct(0, 0, 95)
    assert sc.passes_pct(228, 240, 95) and not sc.passes_pct(227, 240, 95)


def test_wilson_intervals_match_the_design():
    lo, hi = sc.wilson(235, 240)
    assert (round(lo, 3), round(hi, 3)) == (0.952, 0.991)
    lo, hi = sc.wilson(228, 240)
    assert (round(lo, 3), round(hi, 3)) == (0.915, 0.971)
    assert round(sc.wilson(0, 40)[1], 3) == 0.088 and sc.wilson(0, 0) is None


def test_bootstrap_is_seeded_and_brackets_the_rate():
    data = [(s, 5) for s in (5, 5, 5, 4, 5, 3, 5, 5, 4, 5)] * 5
    a = sc.bootstrap_interval(data, resamples=500, seed=7)
    assert a == sc.bootstrap_interval(data, resamples=500, seed=7)
    assert a != sc.bootstrap_interval(data, resamples=500, seed=8)
    rate = sum(s for s, _ in data) / sum(n for _, n in data)
    assert 0 < a[0] <= rate <= a[1] <= 1
    assert sc.bootstrap_interval([(0, 0), (0, 0)], resamples=10) is None
    assert sc.bootstrap_interval([(5, 5)], resamples=10) == [1.0, 1.0]
    assert sc.BOOTSTRAP_RESAMPLES == 10_000


def test_a_scored_run_is_reproducible_with_the_fixed_seed():
    assert score()["a"]["bootstrap"] == score()["a"]["bootstrap"] and score()["a"]["bootstrap"] is not None


def test_reported_metrics():
    rep = score()["reported"]
    assert rep["hit_at_5"]["T"] == {"cases": 1, "hit@5": 1, "hit_any_shown": 1}
    assert rep["hit_at_5"]["N"] == {"cases": 2, "hit@5": 0, "hit_any_shown": 0}  # gold s56 / s999 not among the passages
    assert rep["citation_recall"] == {"answered": 2, "citing_expected_or_acceptable": 2}
    assert rep["usefulness"] == {"claims_removed": 1, "claims_moved": 2, "answered": 2}
    assert rep["notes"] == {"required": 1, "required_shown": 1, "shown_not_required": 0}
    assert rep["disclaimer_at_end"] == {"answered": 2, "present": 2}
    assert rep["latency_s"]["all"]["n"] == 5 and rep["cost_usd"]["total"] == 0.05
    assert rep["latency_s"]["by_status"]["answered"]["n"] == 2


def test_a_void_or_incomplete_or_misordered_record_is_never_scored():
    rows = base_rows()
    review = full_review(rows)
    for bad in (dict(record(rows), review="void", stopped_on_api_error={"case": "T1", "error": {}}),
                dict(record(rows), review="incomplete", stopped_on_cost={"before": "T1"}),
                dict(record(rows), case_ids=["T1", "N1"])):
        with pytest.raises(e2e.GuardError):
            sc.score_run(bad, review, LABELS, resamples=10)
    with pytest.raises(e2e.GuardError, match="no label row"):
        sc.score_run(record(rows), review, {"T1": {}}, resamples=10)


def test_scores_and_the_g3_result_are_written_once(tmp_path):
    ctx = frozen_ctx(tmp_path)
    d = ctx.results_dir
    d.mkdir(parents=True, exist_ok=True)
    rows = ctx_rows()
    for n in (1, 2):
        e2e.write_record(e2e.result_path(n, d), record(rows, run=n) | {"cost_usd": 0.05})
        e2e.write_record(d / f"e2e_run{n}_review.json", full_review(rows))
    scores = [sc.score_run_files(n, ctx, resamples=50) for n in (1, 2)]
    assert (d / "e2e_run1_scores.json").exists()
    with pytest.raises(e2e.GuardError, match="already exists"):
        sc.score_run_files(1, ctx, resamples=50)
    result = sc.g3_files(ctx)
    assert result["verdict"] == "pass" and result["pooled"]["a"]["total"] == 2 * scores[0]["a"]["total"]
    with pytest.raises(e2e.GuardError, match="already exists"):
        sc.g3_files(ctx)


def test_g3_needs_both_runs_to_pass_and_a_failure_is_not_recorded_as_a_failure():
    good = score(good_rows())
    assert good["g3_run_pass"] is True
    rows = good_rows()
    review = full_review(rows)
    review["claims"]["T1:claim:1"]["verdict"] = "unsupported"
    bad = dict(sc.score_run(record(rows, run=2), review, LABELS, resamples=50))
    good1 = dict(good, run=1)
    assert bad["g3_run_pass"] is False and bad["pass"]["a"] is False
    assert sc.g3_result([good1, dict(good, run=2)])["verdict"] == "pass"
    verdict = sc.g3_result([good1, bad])["verdict"]
    assert verdict.startswith("stop_path_D-133") and "not recorded as failed" in verdict
    with pytest.raises(e2e.GuardError):
        sc.g3_result([good1])


# Review sheet ----------------------------------------------------------------------------------


def test_the_run_sheet_shows_claims_with_cited_text_and_flags_but_no_verifier_output():
    rows = [row("T1", "T", claims=[("VAT is charged at the rate of 7.5% under section 148 and 12% under section 999.",
                                    ["nta2025:s148"]), ("Companies pay tax.", ["nta2025:s56"])],
                gaps=["A gap."], verification={"verdicts": {"supported": 2}, "outcome": "passed"}),
            row("N1", "N", claims=[("A claim.", ["nta2025:s148"])], comps=[comp()]),
            row("R1", "R", status="refused", reason="no_such_section")]
    sheet = rs.build_sheet(record(rows), gen.FileCorpusStore())
    assert "### T1:claim:1" in sheet and "### T1:gap:1" in sheet and "### N1:calc:C1" in sheet
    assert "FLAG: figures in no cited chunk" in sheet and "FLAG: section numbers no cited chunk names: 999" in sheet
    assert "R1" not in sheet and "outcome" not in sheet and "verdicts" not in sheet and "passed" not in sheet
    assert "7.5" in sheet  # the cited chunk text is shown


def test_the_run_sheet_is_refused_for_a_void_record(tmp_path):
    ctx = frozen_ctx(tmp_path)
    run_fake(ctx, llm=CountingGen(fail_at=1))
    with pytest.raises(e2e.GuardError, match="void run is never reviewed"):
        rs.write_run_sheet(1, ctx, store=tt.STORE)
    assert not list(ctx.results_dir.glob("*review_sheet*"))


def test_the_run_sheet_and_template_are_written_once_and_the_template_is_not_a_review(tmp_path):
    ctx = frozen_ctx(tmp_path)
    run_fake(ctx)  # every case declines, so nothing is answered: an empty sheet is still written
    sheet, template = rs.write_run_sheet(1, ctx, store=tt.STORE)
    assert sheet.exists() and json.loads(template.read_text(encoding="utf-8"))["claims"] == {}
    with pytest.raises(FileExistsError):
        rs.write_run_sheet(1, ctx, store=tt.STORE)


# Blind calibration (Section 4.1, point 1) --------------------------------------------------------


def source_rows():
    return rs.read_source()


def test_the_blind_file_holds_only_claim_text_and_cited_chunk_ids_in_a_seeded_order(tmp_path):
    source = source_rows()
    path = tmp_path / "blind.jsonl"
    digest = rs.write_calibration(path)
    data = path.read_bytes()
    assert digest == hashlib.sha256(data).hexdigest() and data == rs.blind_bytes(source)
    rows = [json.loads(x) for x in data.decode("utf-8").splitlines()]
    assert len(rows) == 195 and [r["id"] for r in rows] == [f"BC-{i:03d}" for i in range(1, 196)]
    assert all(set(r) == {"id", "claim", "cited_chunk_ids"} and r["cited_chunk_ids"] for r in rows)
    text = data.decode("utf-8")
    for leak in ("CAL-", '"kind"', "expected", "human_verdict", "mutation", "base_id", "base_text", '"change"', "not_supported"):
        assert leak not in text, leak
    assert sorted(r["claim"] for r in rows) == sorted(s["text"] for s in source)  # every claim, once
    assert [r["claim"] for r in rows] != [s["text"] for s in source]               # not in the original order
    assert data == rs.blind_bytes(source)                                         # deterministic from the seed
    assert rs.blind_bytes(source, seed=1) != data
    with pytest.raises(FileExistsError):
        rs.write_calibration(path)


def test_the_real_blind_file_if_built_matches_the_seed():
    path = e2e.PATHS["blind_calibration_v1"]
    if not path.exists():
        pytest.skip("blind_calibration_v1.jsonl is not built yet")
    assert path.read_bytes() == rs.blind_bytes(source_rows())
    if e2e.PINS["blind_calibration_v1"]:
        assert sha(path) == e2e.PINS["blind_calibration_v1"]


def calibration_review(source, *, flag_mutations=60, per_type_miss=None, flag_unsupported=2, flag_human=0):
    """A review built from the true labels (through the same mapping the scorer derives), with chosen misses."""
    mapping = sc.calibration_mapping(source)
    items, missed_types, human_flagged = {}, dict(per_type_miss or {}), 0
    flagged_mut = flagged_un = 0
    for bid, r in mapping.items():
        verdict = "supported"
        if r["kind"] == "mutation":
            t = r["mutation_type"]
            if missed_types.get(t, 0) > 0:
                missed_types[t] -= 1
            elif flagged_mut < flag_mutations:
                verdict, flagged_mut = "unsupported", flagged_mut + 1
        elif r["human_verdict"] == "unsupported":
            if flagged_un < flag_unsupported:
                verdict, flagged_un = "unsupported", flagged_un + 1
        elif r["human_verdict"] == "not_a_claim":
            verdict = "not_a_claim"
        elif human_flagged < flag_human:
            verdict, human_flagged = "partly_supported", human_flagged + 1
        items[bid] = {"verdict": verdict, "reason": "r"}
    return {"reviewer": "r", "attempt": 1, "items": items}


def test_the_scorer_derives_the_blind_mapping_in_code_and_scores_the_targets():
    source = source_rows()
    perfect = sc.score_calibration(calibration_review(source), source)
    assert perfect["passed"] is True and perfect["mutations_flagged"] == 60 and perfect["real_unsupported_flagged"] == 2
    assert perfect["human_supported_flagged"] == 0 and perfect["human_supported"] == 131 and perfect["mutations"] == 60
    assert set(perfect["per_type"]) == {"figure", "hardening", "bridging", "subject_swap", "negation", "citation_move"}
    assert perfect["not_a_claim_items"] == {"count": 2, "judged_not_a_claim": 2}
    edge = sc.score_calibration(calibration_review(source, per_type_miss={"figure": 1}, flag_human=4), source)
    assert edge["passed"] is True and edge["mutations_flagged"] == 59 and edge["human_supported_flagged"] == 4  # on the line


@pytest.mark.parametrize("kwargs, failed", [
    (dict(per_type_miss={"figure": 2}), "per_type"),
    (dict(per_type_miss={"figure": 1, "negation": 2}), "per_type"),
    (dict(flag_unsupported=1), "real_unsupported"),
    (dict(flag_human=5), "human_supported"),
])
def test_each_calibration_target_can_fail(kwargs, failed):
    source = source_rows()
    r = sc.score_calibration(calibration_review(source, **kwargs), source)
    assert r["passed"] is False and r["targets"][failed] is False


def test_three_mutations_missed_in_total_fails_the_58_of_60_target():
    source = source_rows()
    r = sc.score_calibration(calibration_review(source, per_type_miss={"figure": 1, "negation": 1, "bridging": 1}), source)
    assert r["mutations_flagged"] == 57 and r["targets"]["mutations_flagged"] is False and r["passed"] is False


def test_a_calibration_review_must_cover_every_blind_id_with_a_valid_verdict():
    source = source_rows()
    review = calibration_review(source)
    review["items"].pop("BC-001")
    with pytest.raises(sc.ReviewIncomplete, match="BC-001"):
        sc.score_calibration(review, source)
    review = calibration_review(source)
    review["items"]["BC-002"]["verdict"] = "maybe"
    with pytest.raises(sc.ReviewIncomplete, match="BC-002"):
        sc.score_calibration(review, source)
    review = calibration_review(source)
    review["items"]["CAL-R1-Q004-1"] = {"verdict": "supported"}
    with pytest.raises(sc.ReviewIncomplete, match="unknown ids"):
        sc.score_calibration(review, source)


def test_calibration_files_write_counts_only_never_the_mapping_or_a_label(tmp_path):
    source = source_rows()
    ctx = make_ctx(tmp_path)
    ctx.paths["blind_calibration_v1"].write_bytes(rs.blind_bytes(source))
    ctx.results_dir.mkdir()
    e2e.write_record(ctx.results_dir / "blind_calibration_review.json", calibration_review(source, flag_human=1))
    result = sc.calibration_files(1, ctx)
    out = (ctx.results_dir / "blind_calibration_scores.json").read_text(encoding="utf-8")
    saved = json.loads(out)
    assert "missed" not in saved and saved["human_supported_flagged"] == 1 and result["missed"]["human_supported_flagged"]
    assert "CAL-" not in out and "BC-" not in out  # the scores name no item, blind or original
    assert "CAL-" not in ctx.paths["blind_calibration_v1"].read_text(encoding="utf-8")
    assert not any("CAL-" in p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl"))
    with pytest.raises(e2e.GuardError, match="already exists"):
        sc.calibration_files(1, ctx)


def test_a_blind_file_that_does_not_match_the_seed_is_refused(tmp_path):
    ctx = make_ctx(tmp_path)  # the placeholder blind file
    ctx.results_dir.mkdir()
    e2e.write_record(ctx.results_dir / "blind_calibration_review.json", calibration_review(source_rows()))
    with pytest.raises(e2e.GuardError, match="not the file the seed gives"):
        sc.calibration_files(1, ctx)


# Held-out text guard (Section 2.3, point 6) ------------------------------------------------------

TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".json", ".jsonl", ".csv", ".txt", ".toml", ".cfg", ".sql"}
SKIP_PARTS = {"__pycache__", ".venv", ".venv-ocr", ".git", "node_modules"}


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


def scan_for_text(needles: dict[str, str], roots: list[Path], skip_files: set[Path], skip_dirs: list[Path]) -> list[tuple[str, str]]:
    """(id, relative path) of every file under `roots` that holds a needle's text. Needles are
    normalised question texts; only ids and paths are returned."""
    hits = []
    for root in roots:
        for path in root.rglob("*"):
            if (not path.is_file() or path.suffix not in TEXT_SUFFIXES or SKIP_PARTS & set(path.parts)
                    or path.resolve() in skip_files or any(d in path.resolve().parents for d in skip_dirs)):
                continue
            body = normalise(path.read_text(encoding="utf-8", errors="ignore"))
            hits += [(i, path.relative_to(ROOT).as_posix() if ROOT in path.parents else path.name)
                     for i, n in needles.items() if n in body]
    return hits


def test_the_scan_finds_text_and_returns_ids_and_paths_only(tmp_path):
    (tmp_path / "a.py").write_text("x = 'Fake  question\nabout VAT?'\n", encoding="utf-8")
    (tmp_path / "skip.md").write_text("fake question about vat?", encoding="utf-8")
    (tmp_path / "res").mkdir()
    (tmp_path / "res" / "r.json").write_text("fake question about vat?", encoding="utf-8")
    (tmp_path / "b.bin").write_text("fake question about vat?", encoding="utf-8")
    needles = {"N01": normalise("Fake question about VAT?"), "N02": normalise("not anywhere in the files")}
    hits = scan_for_text(needles, [tmp_path], {(tmp_path / "skip.md").resolve()}, [(tmp_path / "res").resolve()])
    assert hits == [("N01", "a.py")]


@pytest.mark.skipif(os.environ.get("TAXY_OVERSEER_HOLDOUT_CHECK") != "1",
                    reason="held-out text guard: set TAXY_OVERSEER_HOLDOUT_CHECK=1 (the overseer's check, D-136)")
def test_no_sealed_question_text_is_in_the_repository():
    """Both sealed files match their pinned sha256 and no sealed question text appears in tests/, python/,
    data/curated/*.yml, eval/ (except the sealed files and eval/e2e/results/) or docs/. Rows are kept in
    memory; only ids and paths are printed."""
    if not (e2e.PINS["e2e_v1"] and e2e.PINS["labels_test_v1"]):
        pytest.fail("the sealed files are not pinned yet, so they cannot be checked")
    rows = e2e.load_sealed(e2e.PATHS["e2e_v1"], e2e.PINS["e2e_v1"])          # raises on a hash mismatch
    e2e.load_sealed(e2e.PATHS["labels_test_v1"], e2e.PINS["labels_test_v1"])  # raises on a hash mismatch
    needles = {r["id"]: normalise(r["question"]) for r in rows if r.get("question")}
    assert len(needles) >= 1
    sealed = {e2e.PATHS["e2e_v1"].resolve(), e2e.PATHS["labels_test_v1"].resolve()}
    roots = [ROOT / "tests", ROOT / "python", ROOT / "eval", ROOT / "docs"] + sorted((ROOT / "data" / "curated").glob("*.yml"))
    files = [r for r in roots if r.is_file()]
    hits = scan_for_text(needles, [r for r in roots if r.is_dir()], sealed, [e2e.RESULTS_DIR.resolve()])
    for f in files:
        body = normalise(f.read_text(encoding="utf-8", errors="ignore"))
        hits += [(i, f.relative_to(ROOT).as_posix()) for i, n in needles.items() if n in body]
    assert not hits, f"sealed question text found: ids {sorted({i for i, _ in hits})} in {sorted({p for _, p in hits})}"


# Scorer replay on stored records (Section 7.6): the dev cases of the Phase 7 regression sample ---------

REGRESSION_SAMPLE = ROOT / "eval" / "tools" / "results" / "regression_sample.json"
DEV_LABELS = ROOT / "eval" / "e2e" / "labels_dev_v1.jsonl"  # open tuning data; deliberately not pinned here
LABEL_FIELDS = {"id", "subset", "category", "expected_status", "expected_reason", "expected_answer", "expected_citations",
                "acceptable_citations", "gold_units", "expected_calc", "required_notes", "must_not", "rationale"}


def dev_labels():
    return {r["id"]: r for r in (json.loads(x) for x in DEV_LABELS.read_text(encoding="utf-8").splitlines() if x.strip())}


def stored_dev_record():
    """The six dev cases of the stored regression sample, as a run record (dev counts as in-scope, subset N)."""
    sample = json.loads(REGRESSION_SAMPLE.read_text(encoding="utf-8"))
    labels = dev_labels()
    rows = []
    for c in sample["cases"]:
        if c["source"] != "dev":
            continue
        rows.append({"id": c["id"], "subset": "N", "category": labels[c["id"]]["category"], "question": "", "status": c["status"],
                     "refusal_reason": c["refusal_reason"], "flags": c["flags"], "notes": c["notes_shown"],
                     "claims": c["claims"], "gaps": c["gaps"], "computations": c["computations"],
                     "passages": [{"label": p["label"], "chunk_id": p["chunk_id"], "reasons": []} for p in c["passages"]],
                     "verification": c["verification"]["summary"], "tool_uses": c["tool_uses"], "disclosures": [],
                     "rendered": c["rendered"], "cost_usd": c["cost_usd"], "latency_s": 0.0, "record_id": c["record_id"],
                     "error": c["error"]})
    return record(rows)


def replay_review(rec):
    """A fixture review of the stored dev answers: everything supported and accurate, except three
    deliberate verdicts that the counts below account for."""
    review = full_review(rec["cases"])
    review["claims"]["Q014:claim:1"] |= {"verdict": "unsupported", "support_not_cited": True}
    for entry in review["gaps"].values():  # full_review lets every first gap state law; here only one does
        entry |= {"states_law": False, "verdict": None}
    first_gap = next(k for k in review["gaps"] if k.startswith("Q025:"))
    review["gaps"][first_gap] |= {"states_law": True, "verdict": "supported"}
    key = "Q043:claim:2"
    chunk = next(iter(review["claims"][key]["citations"]))
    review["claims"][key]["citations"][chunk] = "inaccurate"
    return review, first_gap, key


def test_the_dev_labels_have_the_fields_the_scorer_reads():
    labels = dev_labels()
    assert len(labels) == 20 and all(LABEL_FIELDS <= set(r) for r in labels.values())
    assert all(r["subset"] == "dev" and r["expected_status"] == "answered" and r["gold_units"] for r in labels.values())
    for r in labels.values():
        assert isinstance(r["expected_citations"], list) and isinstance(r["acceptable_citations"], list)
        assert isinstance(r["required_notes"], list) and isinstance(r["expected_answer"], list)
        for entry in sc.label_calcs(r):  # none of the dev rows is a calculation question
            assert "scenario" in entry and sc.label_figures(r, entry["scenario"]) is not None


def test_scorer_replay_on_the_stored_dev_records_with_a_fixture_review():
    rec = stored_dev_record()
    labels = dev_labels()
    assert [r["id"] for r in rec["cases"]] == ["Q003", "Q014", "Q025", "Q034", "Q043", "Q047"]
    review, gap_key, claim_key = replay_review(rec)
    s = sc.score_run(rec, review, labels, resamples=200)
    claims = sum(len(r["claims"]) for r in rec["cases"])
    cites = sum(len(set(c["chunk_ids"])) for r in rec["cases"] for c in r["claims"])
    assert claims == 31 and s["a"]["total"] == claims + 1            # every claim plus the one gap that states law
    assert s["a"]["supported"] == claims                               # Q014 claim 1 fails; the gap counts and is supported
    assert s["b"]["total"] == cites and s["b"]["accurate"] == cites - 1
    assert s["a"]["support_not_cited"] == ["Q014:claim:1"] and s["reviewer_own"]["a"]["supported"] == s["a"]["supported"]
    assert s["d"]["false_refusals"] == 0 and s["d"]["cases"] == 6 and s["c"]["cases"] == 0 and s["c"]["pass"] is False
    assert s["pass"]["a"] is True and s["pass"]["b"] is True         # 31 of 32 is 96.9%; one inaccurate citation is under 5%
    assert s["a"]["rate"] == pytest.approx(31 / 32) and s["b"]["accurate"] == s["b"]["total"] - 1
    assert s["a"]["wilson"][0] < s["a"]["rate"] < s["a"]["wilson"][1] and s["a"]["bootstrap"] is not None
    rep = s["reported"]
    assert rep["hit_at_5"]["N"]["cases"] == 6 and rep["hit_at_5"]["N"]["hit@5"] == 6  # the gold unit is in the top 5 of every dev case
    assert rep["citation_recall"]["answered"] == 6 and rep["disclaimer_at_end"] == {"answered": 6, "present": 6}
    assert rep["cost_usd"]["total"] == pytest.approx(sum(r["cost_usd"] for r in rec["cases"]))
    assert rep["usefulness"]["answered"] == 6
    assert score_replay_again(rec, labels, review) == s["a"]["bootstrap"]  # seeded: the same interval on a second scoring


def score_replay_again(rec, labels, review):
    return sc.score_run(rec, review, labels, resamples=200)["a"]["bootstrap"]


def test_an_unfilled_review_of_the_stored_dev_records_is_refused():
    rec = stored_dev_record()
    with pytest.raises(sc.ReviewIncomplete):
        sc.score_run(rec, rs.build_template(rec), dev_labels(), resamples=10)
