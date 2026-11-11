"""The Phase 7 exit-check runner and its questions (tools design, sections 7.1, 9.1 and 9.2). Free:
no model call. The runner is loaded under its own module name, because the Phase 5 runner is
`run_exit_check` in sys.modules."""
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from generation import generate as gen
from tools import taxcalc

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tools_run_exit_check", ROOT / "eval" / "tools" / "run_exit_check.py")
rec = importlib.util.module_from_spec(spec)
sys.modules["tools_run_exit_check"] = rec
spec.loader.exec_module(rec)

import test_tools as tt  # noqa: E402  (the fake clients of tests/test_tools.py)

RATES = taxcalc.load_rates()
IDS = [f"E{i:02d}" for i in range(1, 12)] + ["X1", "X2", "X3"]


def design_questions() -> dict[str, str]:
    doc = (ROOT / "docs" / "phase07_tools" / "tools_design.md").read_text(encoding="utf-8")
    table = doc.split("### 9.1 Questions")[1].split("### 9.2")[0]
    return {m.group(1): m.group(2) for m in (re.match(r"\| (E\d\d|X\d) \| (.+?) \| ", line)
                                             for line in table.splitlines()) if m}


def test_questions_are_pinned_and_copied_from_the_design():
    rows = rec.load_questions()
    assert [r["id"] for r in rows] == IDS
    assert {r["id"]: r["question"] for r in rows} == design_questions()
    assert all(r["kind"] == ("compute" if r["id"].startswith("E") else "decline") for r in rows)


def test_questions_are_not_from_the_adversarial_sets_or_the_dev_split():
    import run_adversarial
    import run_eval
    texts = {r["question"] for r in rec.load_questions()}
    seen = {q["question"] for q in run_eval.load_questions("dev")}  # the test split is never loaded (D-29)
    for name in ("v1", "v2", "v3", "v4"):
        seen |= {q["question"] for q in run_adversarial.load_adversarial(name, "holdout")}
        if run_adversarial.get_set(name).split is not None:
            seen |= {q["question"] for q in run_adversarial.load_adversarial(name, "tune")}
    assert len(seen) > 200 and not texts & seen


def test_a_changed_question_file_is_refused(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_bytes(rec.QUESTIONS.read_bytes().replace(b"2,600,000", b"2,700,000"))
    with pytest.raises(rec.GuardError, match="pinned sha256"):
        rec.load_questions(path)


@pytest.mark.parametrize("row", rec.load_questions(), ids=lambda r: r["id"])
def test_expected_calls_give_the_hand_worked_figures(row):
    """The calculator gives each hand-worked figure of Section 9.1 from the expected inputs."""
    comps = [taxcalc.calculate(f"C{i}", c["scenario"], c, row["question"], RATES)
             for i, c in enumerate(row["expected_calls"], 1)]
    if row["kind"] == "compute":
        assert [c.status for c in comps] == ["computed"] * len(comps)
        assert [c.result for c in comps] == row["expected_results"]
        for c in comps:
            for key in c.relies_on:
                assert taxcalc.relied_item(key, RATES).chunk_id in row["expected_citations"]
    else:
        assert all(not c.computed for c in comps) and row["expected_results"] == []
    if row["id"] == "X2":
        assert comps[0].declined_code == "missing_input"


# Configuration ---------------------------------------------------------------------------------


def test_live_configuration_has_only_the_named_override():
    config = rec.current_config()
    assert config["base"] == {"file": "eval/guardrails/config_g2_v4.json", "sha256": rec.BASE_CONFIG_SHA256}
    assert rec.BASE_CONFIG_SHA256.startswith("d088e6cd") and rec.BASE_CONFIG_SHA256.endswith("a3b5")
    assert list(config["overrides"]) == ["heading_corrections"]
    over = config["overrides"]["heading_corrections"]
    assert over["base_sha256"].startswith("8ba9de04") and over["sha256"] == rec.sha256_file(rec.HEADING_CORRECTIONS)
    tools = config["tools"]
    assert (tools["max_model_calls"], tools["max_lookup_calls"], tools["max_lookup_chunks"], tools["max_calc_calls"],
            tools["max_passages"]) == (3, 2, 3, 2, 14)
    assert tools["calc_rates"]["sha256"] == taxcalc.RATES_SHA256 and tools["gap_figure_filter"] is True
    assert tools["tool_choice"] == ["auto", "auto", "none"] and tools["calc_version"] == "calc/1"
    assert config["prompts"]["prompt_version"] == "gen-prompt/9"
    assert config["prompts"]["inherited"] == {"verifier": "ver-prompt/4", "gap": "gap-prompt/3",
                                              "tagger": "tag-prompt/3", "normaliser": "norm/7"}
    assert config["exit_questions"]["sha256"] == rec.QUESTIONS_SHA256


def test_an_unnamed_difference_from_the_base_is_refused():
    base = json.loads(rec.BASE_CONFIG.read_text(encoding="utf-8"))
    with pytest.raises(rec.GuardError, match=r"\['tau'\]"):
        rec.base_overrides(dict(base, tau=0.5), base)
    assert rec.base_overrides(dict(base), base) == {}


def test_freeze_writes_once_and_records_the_live_configuration(tmp_path):
    path = tmp_path / "config_p7.json"
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    digest = rec.freeze_config(path=path, now=now)
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert digest == rec.sha256_file(path) and frozen["frozen_at"] == now.isoformat() and frozen["decision"] == "D-123"
    assert {k: v for k, v in frozen.items() if k not in rec.FREEZE_FIELDS} == rec.current_config()
    with pytest.raises(rec.GuardError, match="frozen once"):
        rec.freeze_config(path=path)


def test_config_p7_is_not_frozen_by_the_build():
    """The build writes the freeze code only; config_p7.json is frozen before run 1 (Section 7.1)."""
    if rec.CONFIG_SHA256[1] is None:
        assert not rec.CONFIGS[1].exists() and not rec.result_path(1).exists()


def test_run_guards(tmp_path):
    live = rec.current_config()
    path = tmp_path / "config_p7.json"
    pins = {1: None, 2: None}
    with pytest.raises(rec.GuardError, match="no frozen configuration"):
        rec.check_run_allowed(1, configs={1: path, 2: tmp_path / "r2.json"}, pins=pins, results_dir=tmp_path, live=live)
    pins[1] = rec.freeze_config(path=path)
    configs = {1: path, 2: tmp_path / "r2.json"}
    assert rec.check_run_allowed(1, configs=configs, pins=pins, results_dir=tmp_path, live=live)["decision"] == "D-123"
    with pytest.raises(rec.GuardError, match="differs"):
        rec.check_run_allowed(1, configs=configs, pins=pins, results_dir=tmp_path, live=dict(live, overrides={}))
    with pytest.raises(rec.GuardError, match="does not match"):
        rec.check_run_allowed(1, configs=configs, pins={1: "0" * 64}, results_dir=tmp_path, live=live)
    with pytest.raises(rec.GuardError, match="run 2 needs a complete run 1"):
        rec.check_run_allowed(2, configs=configs, pins=pins, results_dir=tmp_path, live=live)
    with pytest.raises(rec.GuardError, match="at most twice"):
        rec.check_run_allowed(3, configs=configs, pins=pins, results_dir=tmp_path, live=live)
    rec.result_path(1, tmp_path).write_text("{}", encoding="utf-8")
    with pytest.raises(rec.GuardError, match="never repeated"):
        rec.check_run_allowed(1, configs=configs, pins=pins, results_dir=tmp_path, live=live)


def test_a_run_is_refused_before_any_model_call_while_not_frozen():
    if rec.CONFIG_SHA256[1] is not None:
        pytest.skip("config_p7.json is frozen")
    calls = []
    with pytest.raises(rec.GuardError):
        rec.run(1, clients={}, answer_fn=lambda q, c, clients: calls.append(q))
    assert calls == []


def test_the_cost_stop():
    assert rec.projected_over_cap(0.50, [0.03]) is False
    assert rec.projected_over_cap(0.56, [0.03]) is True     # 0.56 + the 0.045 floor passes $0.60
    assert rec.projected_over_cap(0.50, [0.03, 0.12]) is True  # the dearest question so far


# Automatic checks on fake-client answers --------------------------------------------------------


def e01(*final_args, **final_kw):
    answer, _, _ = tt.run(tt.E01, tt.tool_turn(tt.use("t1", tt.VAT)), tt.final(*final_args, **final_kw))
    return answer


def chunks_of(answer):
    return tt.STORE.chunks([p.chunk_id for p in answer.passages])


def test_checks_pass_on_a_correct_computed_answer():
    row = rec.load_questions()[0]
    answer = e01([tt.VAT_CLAIM], calculations=["C1"])
    checks = rec.check_case(row, answer, chunks_of(answer), RATES)
    assert checks["auto_pass"] is True, checks
    assert checks["code"] == {"C1": []} and checks["figures"]["unexplained"] == []
    assert checks["cites_expected"] == []


def test_the_figure_check_finds_a_figure_from_nowhere_and_a_computed_amount_in_a_gap():
    row = rec.load_questions()[0]
    answer = e01([tt.VAT_CLAIM], calculations=["C1"])
    answer.rendered = answer.rendered.replace("Sources", "Also ₦12,345 is due.\n\nSources", 1)
    checks = rec.check_case(row, answer, chunks_of(answer), RATES)
    assert checks["figures"]["unexplained"] == ["amt:12345"] and checks["auto_pass"] is False
    answer = e01([tt.VAT_CLAIM], calculations=["C1"])
    answer.gaps = ["The VAT of ₦195,000 is not confirmed."]
    assert rec.figure_check(answer, row["question"], chunks_of(answer))["computed_amount_in_claim_or_gap"] == \
        ["amt:195000"]


def test_a_withheld_item_fails_the_compute_check():
    row = rec.load_questions()[0]
    answer = e01([("VAT is charged on the value of all taxable supplies.", ["P9"])], calculations=["C1"])
    checks = rec.check_case(row, answer, chunks_of(answer), RATES)
    assert checks["correct"] is False and checks["auto_pass"] is False


def test_the_code_check_catches_a_constant_not_in_the_table():
    answer = e01([tt.VAT_CLAIM], calculations=["C1"])
    record = json.loads(json.dumps(answer.computations[0]))
    assert rec.code_check(record, RATES) == []
    record["constants"][0]["value"] = "8"
    record["steps"][0]["by"] = "model"
    assert rec.code_check(record, RATES) == ["a step not done by taxcalc", "constant vat_rate not as in the pinned table"]


def test_a_decline_question_passes_with_a_logged_decline():
    x2 = rec.load_questions()[12]
    assert x2["id"] == "X2"
    answer, _, _ = tt.run(x2["question"], tt.tool_turn(tt.use("t1", tt.fields("company_income_tax", amount="25000000"))),
                          tt.final([("Companies other than small companies pay tax at 30 per cent.", ["P3"])],
                                   gaps=["The gross turnover is not given."], calculations=["C1"]))
    checks = rec.check_case(x2, answer, chunks_of(answer), RATES)
    assert checks["correct"] is True and checks["extra_calls"] == [] and checks["auto_pass"] is True, checks


# D-128: an API error stops the run and voids it (as Phase 6 D-69 and D-70) -----------------------

SECRET_MESSAGE = "Overloaded; key sk-ant-api03-SECRETSECRET was sent"


def api_error():
    from generation import llm
    return llm.APIError("InternalServerError", status_code=529, api_error_type="overloaded_error",
                        message=SECRET_MESSAGE, request_id="req_x")


class CountingGen:
    """A tools-on generation client that declines every question, raising an APIError on call `fail_at`."""
    from generation import llm as _llm
    model = _llm.MODEL

    def __init__(self, fail_at=None):
        self.fail_at, self.calls = fail_at, 0

    def create(self, params):
        self.calls += 1
        if self.calls == self.fail_at:
            raise api_error()
        return tt.ScriptLLM(tt.final([], status="not_covered", gaps=["The passages do not settle it."])).create(params)


class FailingTagger(tt.FakeTagger):
    def __init__(self):
        super().__init__([])
        self.calls = 0

    def create(self, params):
        self.calls += 1
        raise api_error()


def fake_answer(question, config, clients):
    return gen.generate(question, retriever=tt.FakeRetriever(), store=tt.STORE, log_dir=None, config=config,
                        llm=clients["llm"], verifier=clients["verifier"], tagger=clients["tagger"])


def run_fake(tmp_path, *, llm=None, tagger=None, rerun=False):
    clients = {"llm": llm or CountingGen(), "verifier": tt.FakeVerifier(), "tagger": tagger or tt.FakeTagger([])}
    record = rec.run(1, rerun=rerun, clients=clients, answer_fn=fake_answer, store=tt.STORE, results_dir=tmp_path,
                     guard=lambda: {"frozen_at": "2026-11-11T00:00:00+00:00"})
    return record, clients


def test_a_complete_fake_run_is_pending_review_with_a_verdict(tmp_path):
    record, clients = run_fake(tmp_path)
    assert record["review"] == "pending" and record["stopped_on_api_error"] is None and "auto_pass" in record
    assert len(record["cases"]) == 14 and clients["llm"].calls == 14
    assert rec.read_record(rec.result_path(1, tmp_path)) == record and not rec.is_void_record(record)


def test_a_generation_api_error_stops_the_run_after_that_question_and_voids_it(tmp_path):
    record, clients = run_fake(tmp_path, llm=CountingGen(fail_at=3))
    stop = record["stopped_on_api_error"]
    assert (stop["case"], stop["cases_written"], stop["cases_planned"]) == ("E03", 3, 14)
    assert stop["error"]["error_type"] == "InternalServerError" and stop["error"]["status_code"] == 529
    assert "SECRETSECRET" not in json.dumps(record) and "[redacted]" in stop["error"]["message"]
    assert clients["llm"].calls == 3  # no call after the error
    assert record["review"] == "void" and "auto_pass" not in record
    assert all("checks" not in r and "review" not in r for r in record["cases"])
    assert rec.is_void_record(rec.read_record(rec.result_path(1, tmp_path)))


def test_a_tagger_api_error_stops_the_run_too(tmp_path):
    tagger = FailingTagger()
    record, clients = run_fake(tmp_path, tagger=tagger)
    assert record["stopped_on_api_error"]["case"] == "E01" and record["review"] == "void"
    # The tagger runs alongside E01's generation (D-77), so calls already in flight may finish; the stop
    # is shared, so no call is made for any later question.
    assert 1 <= tagger.calls <= 3 and clients["llm"].calls <= 1 and len(record["cases"]) == 1


def test_the_re_run_after_void_writes_its_own_record(tmp_path):
    run_fake(tmp_path, llm=CountingGen(fail_at=1))
    record, _ = run_fake(tmp_path, rerun=True)
    assert record["rerun_after_void"] is True and record["review"] == "pending"
    assert rec.result_path(1, tmp_path, rerun=True).exists()
    assert rec.counted_run(1, tmp_path) == record


def test_main_exits_3_on_a_void_run_and_0_on_a_complete_one(tmp_path, capsys):
    void, _ = run_fake(tmp_path, llm=CountingGen(fail_at=2))
    assert rec.main(["--run", "1"], run_fn=lambda n, rerun: void) == 3
    err = capsys.readouterr().err
    assert "STOPPED at an API error during E02" in err and "void" in err and "SECRETSECRET" not in err
    done = dict(void, stopped_on_api_error=None, auto_pass=False)
    assert rec.main(["--run", "1"], run_fn=lambda n, rerun: done) == 0
    with pytest.raises(SystemExit):
        rec.main(["--status", "--rerun-after-void"])


def write(path, record):
    path.write_text(json.dumps(record), encoding="utf-8")


def test_re_run_guards(tmp_path):
    live = rec.current_config()
    configs = {1: tmp_path / "config_p7.json", 2: tmp_path / "config_p7_r2.json"}
    pins = {1: rec.freeze_config(path=configs[1]), 2: rec.freeze_config(path=configs[2])}

    def allowed(n, rerun=False):
        return rec.check_run_allowed(n, configs=configs, pins=pins, results_dir=tmp_path, live=live, rerun=rerun)

    with pytest.raises(rec.GuardError, match="needs a first run"):
        allowed(1, rerun=True)
    void = {"review": "void", "cases": [], "stopped_on_api_error": {"case": "E01", "error": {"error_type": "X"}}}
    first = rec.result_path(1, tmp_path)
    write(first, dict(void, stopped_on_api_error={"case": "E01"}))  # no error record: not complete
    with pytest.raises(rec.GuardError, match="not a complete void record"):
        allowed(1, rerun=True)
    write(first, dict(void, auto_pass=False))  # a verdict: not a void record
    with pytest.raises(rec.GuardError, match="not a complete void record"):
        allowed(1, rerun=True)
    first.write_text("{not json", encoding="utf-8")
    with pytest.raises(rec.GuardError, match="not a complete void record"):
        allowed(1, rerun=True)
    write(first, void)
    assert allowed(1, rerun=True)["decision"] == "D-123"
    with pytest.raises(rec.GuardError, match="never repeated"):
        allowed(1)
    with pytest.raises(rec.GuardError, match="a void run does not count"):
        allowed(2)
    write(rec.result_path(1, tmp_path, rerun=True), dict(void))  # the re-run was void too
    with pytest.raises(rec.GuardError, match="already been made"):
        allowed(1, rerun=True)
    with pytest.raises(rec.GuardError, match="a void run does not count"):
        allowed(2)
    rec.result_path(1, tmp_path, rerun=True).unlink()
    write(rec.result_path(1, tmp_path, rerun=True), {"review": "pending", "stopped_on_api_error": None,
                                                     "auto_pass": False, "cases": []})
    assert allowed(2)["decision"] == "D-123"  # the complete re-run counts as run 1
