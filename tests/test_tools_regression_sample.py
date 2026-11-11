"""The Phase 7 regression sample runner (tools design, Section 8; D-124, D-128). Free: fake clients
only, no model call. The fake clients and helpers are those of the exit-check tests."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

import test_tools as tt
import test_tools_exit_check as tec
from generation import generate as gen

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tools_run_regression_sample", ROOT / "eval" / "tools" / "run_regression_sample.py")
rs = importlib.util.module_from_spec(spec)
sys.modules["tools_run_regression_sample"] = rs
spec.loader.exec_module(rs)

IDS = ["Q003", "Q014", "Q025", "Q034", "Q043", "Q047", "ADV-017", "ADV-019", "ADV-001", "ADV-009", "ADV-025", "ADV-048"]
LIVE = {"fake": "live configuration"}


@pytest.fixture(scope="module")
def cases():
    return rs.load_cases()


@pytest.fixture(scope="module")
def baseline():
    return rs.load_baseline()


def run_fake(tmp_path, cases, baseline, *, llm=None, tagger=None, rerun=False, answer_fn=None):
    clients = {"llm": llm or tec.CountingGen(), "verifier": tt.FakeVerifier(), "tagger": tagger or tt.FakeTagger([])}
    record = rs.run(rerun=rerun, clients=clients, answer_fn=answer_fn or tec.fake_answer, store=tt.STORE,
                    results_dir=tmp_path, baseline=baseline, cases=cases, live_config=LIVE)
    return record, clients


def test_the_twelve_cases_are_the_named_ones_in_order(cases):
    assert [c["id"] for c in cases] == IDS == rs.CASE_IDS
    assert [c["source"] for c in cases] == ["dev"] * 6 + ["adversarial"] * 6
    assert all(c["question"] for c in cases)


def test_every_case_has_a_tuning_run_17_row(baseline):
    assert set(IDS) <= set(baseline)
    assert {baseline[i]["status"] for i in IDS[8:]} == {"refused"} and baseline["ADV-017"]["status"] == "not_covered"


def test_compare_reports_each_difference():
    base = {"status": "answered", "refusal_reason": None, "notes_shown": ["scope:x"], "cost_usd": 0.01,
            "verification": {"summary": {"claims_kept": 4, "claims_removed": 1}}}
    same = {"status": "answered", "refusal_reason": None, "notes_shown": ["scope:x"], "cost_usd": 0.012,
            "claims_kept": 4, "claims_removed": 1, "tool_calls": []}
    assert rs.compare(base, same)["differs"] == []
    assert rs.compare(base, same)["cost_usd"]["delta"] == 0.002
    other = dict(same, status="not_covered", refusal_reason="x", notes_shown=["act:y"], claims_kept=0,
                 claims_removed=0, tool_calls=["lookup_section"])
    got = rs.compare(base, other)
    assert got["differs"] == ["status", "refusal_reason", "claims_kept", "claims_removed", "notes_shown", "tool_called"]
    assert got["notes_shown"]["added"] == ["act:y"] and got["notes_shown"]["dropped"] == ["scope:x"]
    assert got["tool_called"] == {"run17": False, "now": True, "which": ["lookup_section"]}
    refused = {"status": "refused", "refusal_reason": "no_such_section", "notes_shown": [], "cost_usd": 0.0002,
               "verification": None}
    assert rs.compare(refused, dict(refused, claims_kept=0, claims_removed=0, tool_calls=[]))["differs"] == []


def test_a_complete_fake_run_records_the_comparison_config_and_code(tmp_path, cases, baseline):
    record, clients = run_fake(tmp_path, cases, baseline)
    assert record["review"] == "pending" and record["stopped_on_api_error"] is None and record["stopped_on_cost"] is None
    assert [c["id"] for c in record["cases"]] == IDS and record["live_config"] == LIVE
    assert record["code"]["files"].keys() == set(rs.CODE_FILES) and record["check_not_gate"] is True
    assert record["baseline"]["sha256"] == rs.sha256_file(rs.BASELINE) and record["config_p7_frozen"] is rs.rec.CONFIGS[1].exists()
    first = record["cases"][0]
    assert set(first["comparison"]) >= {"status", "refusal_reason", "claims_kept", "claims_removed", "notes_shown",
                                        "tool_called", "cost_usd", "differs"}
    assert first["comparison"]["status"] == {"run17": "answered", "now": "not_covered"}
    assert "status" in record["differences"]["Q003"]
    assert clients["llm"].calls <= 12  # pre-check refusals make no generation call
    assert json.loads((tmp_path / "regression_sample.json").read_text(encoding="utf-8")) == record


def test_the_result_is_written_once_and_never_repeated(tmp_path, cases, baseline):
    run_fake(tmp_path, cases, baseline)
    with pytest.raises(rs.GuardError, match="never repeated"):
        run_fake(tmp_path, cases, baseline)
    with pytest.raises(rs.GuardError, match="not a complete void record"):
        run_fake(tmp_path, cases, baseline, rerun=True)


def test_a_tool_call_is_reported_with_its_name(tmp_path, cases, baseline):
    one = [c for c in cases if c["id"] == "Q003"]
    question = one[0]["question"]
    answer_fn = lambda q, config, clients: gen.generate(  # noqa: E731
        q, retriever=tt.FakeRetriever(), store=tt.STORE, log_dir=None, config=config, llm=tt.ScriptLLM(
            tt.tool_turn(tt.use("t1", tt.fields("vat_on_supply", amount="1000", amount_basis="value_before_vat"))),
            tt.final([("VAT is charged at 7.5 per cent.", ["P1"])], calculations=["C1"])),
        verifier=clients["verifier"], tagger=clients["tagger"])
    record, _ = run_fake(tmp_path, one, baseline, answer_fn=answer_fn)
    row = record["cases"][0]
    assert question and row["tool_calls"] == ["calculate_liability:vat_on_supply"]
    assert row["comparison"]["tool_called"]["which"] == ["calculate_liability:vat_on_supply"]
    assert "tool_called" in row["comparison"]["differs"]


def test_the_cost_stop_stops_before_the_next_case(tmp_path, cases, baseline):
    class Dear:
        def __init__(self, inner):
            self.inner = inner

        def __call__(self, q, config, clients):
            answer = self.inner(q, config, clients)
            answer.cost_usd = 0.16
            return answer

    record, _ = run_fake(tmp_path, cases, baseline, answer_fn=Dear(tec.fake_answer))
    assert len(record["cases"]) == 1 and record["stopped_on_cost"]["before"] == "Q014"
    assert record["cost_usd"] <= rs.COST_CAP_USD


def test_the_cap_is_three_tenths_of_a_dollar():
    assert rs.COST_CAP_USD == 0.30
    assert not rs.projected_over_cap(0.2, [0.02]) and rs.projected_over_cap(0.26, [0.02])


def test_an_api_error_voids_the_run_and_allows_one_re_run(tmp_path, cases, baseline, capsys):
    record, clients = run_fake(tmp_path, cases, baseline, llm=tec.CountingGen(fail_at=2))
    stop = record["stopped_on_api_error"]
    assert (stop["case"], stop["cases_written"], stop["cases_planned"]) == ("Q014", 2, 12)
    assert "SECRETSECRET" not in json.dumps(record) and "[redacted]" in stop["error"]["message"]
    assert clients["llm"].calls == 2 and record["review"] == "void" and "differences" not in record
    assert all("comparison" not in r for r in record["cases"])
    assert rs.main(["--run"], run_fn=lambda rerun: record) == 3
    assert "void" in capsys.readouterr().err
    again, _ = run_fake(tmp_path, cases, baseline, rerun=True)
    assert again["rerun_after_void"] is True and again["review"] == "pending"
    assert (tmp_path / "regression_sample_rerun.json").exists()
    with pytest.raises(rs.GuardError, match="already been made"):
        run_fake(tmp_path, cases, baseline, rerun=True)


def test_a_tagger_api_error_voids_the_run_too(tmp_path, cases, baseline):
    record, _ = run_fake(tmp_path, cases, baseline, tagger=tec.FailingTagger())
    assert record["review"] == "void" and record["stopped_on_api_error"]["case"] == "Q003"


def test_main_exit_codes_and_the_rerun_flag(capsys):
    done = {"stopped_on_api_error": None, "cases": [], "cost_usd": 0.1, "stopped_on_cost": None}
    assert rs.main(["--run"], run_fn=lambda rerun: done) == 0
    with pytest.raises(SystemExit):
        rs.main(["--status", "--rerun-after-void"])
    assert rs.main(["--run"], run_fn=lambda rerun: (_ for _ in ()).throw(rs.GuardError("x"))) == 2


def test_the_real_result_file_is_not_touched_by_the_tests():
    """Free tests write to tmp_path only; the real result exists only after the paid run."""
    assert rs.RESULT.parent.name == "results"
