"""Grounded generation (Phase 5): prompt, request shape, parser checks, rendering, logging and the
model client. A fake model client, a fake retriever and the committed chunk files stand in for the
API and the database, so no key and no database are needed. The client tests use a mock HTTP
transport. Assertion messages name ids only, never question text."""
import hashlib
import importlib.util
import json
import re
import sys

import httpx2
import pytest

from embed import clients
from generation import audit_log, generate as gen, llm, prompt
from retrieval.retrieve import Retrieved

ROOT = clients.ROOT
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "retrieval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
sys.modules.setdefault("run_eval", run_eval)
spec.loader.exec_module(run_eval)

STORE = gen.FileCorpusStore()
SYSTEM_PROMPT_SHA256 = "210b16b9b0b2be7491aa5f5789211367c33cf63497d0ee985b3aa6727eb4caf0"  # gen-prompt/2
RETRIEVAL_CONFIG_SHA256 = "6ce743b317a58260f7eb397c7a6e9a7e14afab86cbde2dbc296273131c9e3684"  # D-21
QUESTION = "Does the development levy apply to a small company?"
CHUNK_IDS = ["nta2025:s59", "nta2025:s202:def:small-company", "nta2025:s56"]


class FakeRetriever:
    def __init__(self, chunk_ids=CHUNK_IDS):
        self.chunk_ids = chunk_ids

    def retrieve(self, question, k=5, filters=None, query_vector=None):
        return [Retrieved(c, i, 0.5 - i / 100, 0.5 + i / 100, ["fake"]) for i, c in enumerate(self.chunk_ids[:k], 1)]


class FakeLLM:
    model = llm.MODEL

    def __init__(self, output=None, *, stop_reason="end_turn", raises=None, text=None):
        self.output, self.stop_reason, self.raises, self.text = output, stop_reason, raises, text
        self.requests = []

    def create(self, params):
        self.requests.append(params)
        if self.raises:
            raise self.raises
        text = self.text if self.text is not None else json.dumps(self.output)
        usage = {"input_tokens": 1500, "output_tokens": 400, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 700}
        return llm.LLMResponse([{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
                               self.stop_reason, None, usage, "req_fake", self.model, 12)


ANSWER = {"status": "answered",
          "claims": [{"text": "Small companies are not charged the development levy.", "passages": ["P1"]},
                     {"text": "A small company is defined by turnover.", "passages": ["P2", "P1", "P2"]}],
          "gaps": []}


def run(output=ANSWER, tmp_path=None, **kw):
    fake = kw.pop("fake", None) or FakeLLM(output)
    answer = gen.generate(QUESTION, retriever=kw.pop("retriever", FakeRetriever()), llm=fake, store=STORE,
                          log_dir=tmp_path, **kw)
    return answer, fake


# Prompt and request ----------------------------------------------------------------------------


def test_system_prompt_is_pinned():
    assert hashlib.sha256(prompt.SYSTEM_PROMPT.encode("utf-8")).hexdigest() == SYSTEM_PROMPT_SHA256
    assert prompt.PROMPT_VERSION == "gen-prompt/2"
    assert not re.search(r"\d{4}-\d{2}-\d{2}T|\{|\}", prompt.SYSTEM_PROMPT)  # no per-call content


def test_user_message_labels_passages_in_rank_order_with_exact_text():
    chunks = [STORE.chunks([c])[c] for c in CHUNK_IDS]
    message = prompt.build_user_message(chunks, QUESTION)
    assert [m for m in re.findall(r'label="(P\d)"', message)] == ["P1", "P2", "P3"]
    for chunk in chunks:
        assert f'chunk_id="{chunk["chunk_id"]}"' in message
        assert "\n" + chunk["text"] + "\n</passage>" in message
    assert 'definition of &quot;small company&quot;' in message  # attribute quotes escaped
    assert message.endswith("<question>\n" + QUESTION + "\n</question>")
    assert 'disputed="yes"' in message
    with pytest.raises(ValueError):
        prompt.build_user_message(chunks * 2, QUESTION)


def test_request_shape():
    params = llm.request_params("system", "user", prompt.OUTPUT_SCHEMA)
    assert params["model"] == "claude-sonnet-5-5" and params["max_tokens"] == 16_000
    assert params["system"] == [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}]
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "medium",
                                       "format": {"type": "json_schema", "schema": prompt.OUTPUT_SCHEMA}}
    assert not {"temperature", "top_p", "top_k"} & set(params)


def test_retrieval_config_is_the_frozen_one():
    assert hashlib.sha256(gen.RETRIEVAL_CONFIG.read_bytes()).hexdigest() == RETRIEVAL_CONFIG_SHA256


# Parser ----------------------------------------------------------------------------------------


LABELS = {"P1": "nta2025:s59", "P2": "nta2025:s56"}


def parse(output=None, text=None, stop="end_turn"):
    response = FakeLLM(output, stop_reason=stop, text=text).create({})
    return gen.parse_output(response, LABELS)


def test_parser_accepts_a_valid_answer_and_dedupes_labels():
    result = parse(ANSWER)
    assert result.status == "answered"
    assert [c.chunk_ids for c in result.claims] == [["nta2025:s59"], ["nta2025:s56", "nta2025:s59"]]
    assert result.rejected == [] and result.error is None


def test_parser_rejects_labels_not_shown_and_drops_uncited_claims():
    out = {"status": "answered", "gaps": [],
           "claims": [{"text": "One.", "passages": ["P1", "P5"]}, {"text": "Two.", "passages": ["P4"]}]}
    result = parse(out)
    assert result.status == "answered" and [c.text for c in result.claims] == ["One."]
    assert [(r["claim"], r["label"]) for r in result.rejected] == [(0, "P5"), (1, "P4"), (1, None)]


def test_parser_errors_when_no_claim_keeps_a_citation():
    out = {"status": "answered", "gaps": [], "claims": [{"text": "One.", "passages": []}]}
    assert parse(out).status == "error"


def test_parser_not_covered_drops_claims_and_answered_without_claims_declines():
    out = {"status": "not_covered", "gaps": ["the rate"], "claims": [{"text": "x", "passages": ["P1"]}]}
    result = parse(out)
    assert (result.status, result.claims, result.gaps) == ("not_covered", [], ["the rate"])
    assert parse({"status": "answered", "claims": [], "gaps": []}).status == "not_covered"


@pytest.mark.parametrize("text,stop", [("not json", "end_turn"), ('{"status": "answered"}', "end_turn"),
                                       (json.dumps(ANSWER), "max_tokens"), (json.dumps(ANSWER), "refusal")])
def test_parser_errors(text, stop):
    result = parse(text=text, stop=stop)
    assert result.status == "error" and result.error


def test_parser_flags_a_label_inside_claim_text():
    out = {"status": "answered", "gaps": [], "claims": [{"text": "The rate is 4% [P1].", "passages": ["P1"]}]}
    assert parse(out).flags == [{"claim": 0, "flag": "label in claim text"}]


# Generation, rendering and logging --------------------------------------------------------------


def test_generate_answered_renders_citations_sources_and_disclaimer(tmp_path):
    answer, fake = run(tmp_path=tmp_path)
    assert answer.status == "answered" and len(fake.requests) == 1
    assert answer.cited_chunk_ids == ["nta2025:s59", "nta2025:s202:def:small-company"]
    assert [p.label for p in answer.passages] == ["P1", "P2", "P3"]
    sources = answer.rendered.split("Sources\n")[1].split("\n\n")[0]
    assert "Nigeria Tax Act 2025, section 59" in sources and "section 56" not in sources
    assert "[Nigeria Tax Act 2025, section 59]" in answer.rendered
    assert answer.rendered.endswith(gen.DISCLAIMER)
    assert answer.cost_usd == pytest.approx((1500 * 2 + 700 * 0.10 + 400 * 10) / 1e6)


def test_hidden_disclosures_are_recorded_not_rendered(tmp_path):
    answer, _ = run(tmp_path=tmp_path)
    hidden = [d for d in answer.disclosures if d.kind == "hidden"]
    shown = [d for d in answer.disclosures if d.kind != "hidden"]
    assert hidden and shown
    for d in hidden:
        assert f"({d.ref})" not in answer.rendered
    for d in shown:
        assert d.ref in answer.rendered
    record = audit_log.read_records(next(tmp_path.glob("*.jsonl")))[0]
    assert {d["ref"] for d in record["disclosures"] if d["kind"] == "hidden"} == {d.ref for d in hidden}


def test_generate_not_covered_and_error_paths(tmp_path):
    gaps = ["Rates for other countries.", "The rate for the current year."]
    declined, _ = run({"status": "not_covered", "claims": [], "gaps": gaps}, tmp_path)
    assert declined.status == "not_covered" and declined.rendered.startswith(gen.DECLINE_TEXT)
    assert ("Not covered by the retrieved provisions:\n- Rates for other countries.\n"
            "- The rate for the current year.") in declined.rendered
    assert ".;" not in declined.rendered and declined.rendered.endswith(gen.DISCLAIMER)
    failed, _ = run(fake=FakeLLM(raises=llm.GenerationError("RateLimitError (HTTP 429)")), tmp_path=tmp_path)
    assert failed.status == "error" and failed.rendered == gen.ERROR_TEXT + "\n\n" + gen.DISCLAIMER
    assert failed.disclosures == []
    records = audit_log.read_records(next(tmp_path.glob("*.jsonl")))
    assert [r["parsed"]["status"] for r in records] == ["not_covered", "error"]
    assert records[0]["refusal_flag"] is True and records[1]["error"] == "RateLimitError (HTTP 429)"


def test_no_passages_declines_without_a_model_call(tmp_path):
    answer, fake = run(retriever=FakeRetriever([]), tmp_path=tmp_path)
    assert answer.status == "not_covered" and fake.requests == []
    assert gen.NO_PASSAGES_GAP in answer.rendered


def test_dq12_note_reaches_a_decline():
    fake = FakeLLM({"status": "not_covered", "claims": [], "gaps": ["the commencement provision"]})
    answer = gen.generate("When did the Nigeria Tax Administration Act 2025 come into force?",
                          retriever=FakeRetriever(), llm=fake, store=STORE, log_dir=None)
    assert any(d.ref == "DQ-12" for d in answer.disclosures) and "1st day of January, 2025" in answer.rendered


def test_log_record_has_every_field_and_no_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-NEVER-LOGGED-123")
    answer, _ = run(tmp_path=tmp_path)
    path = next(tmp_path.glob("*.jsonl"))
    text = path.read_text(encoding="utf-8")
    assert "NEVER-LOGGED" not in text
    record = audit_log.read_records(path)[0]
    assert list(record) == list(audit_log.FIELDS) and record["schema"] == "gen-log/1"
    assert record["record_id"] == answer.record_id and record["verification"] is None
    assert record["request"]["system"][0]["text"] == prompt.SYSTEM_PROMPT
    assert record["request"]["prompt_version"] == "gen-prompt/2"
    assert [c["chunk_id"] for c in record["retrieval"]["chunks"]] == CHUNK_IDS
    assert record["retrieval"]["config_sha256"] == RETRIEVAL_CONFIG_SHA256
    assert record["response"]["usage"]["cache_read_input_tokens"] == 700
    assert record["corpus"]["corpus_snapshot"] == "2026-10-18"
    with pytest.raises(ValueError):
        audit_log.write_record({"schema": "x"}, gen.datetime.now(gen.timezone.utc), tmp_path)


def test_no_log_written_when_log_dir_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "LOG_DIR", tmp_path)
    run(tmp_path=None)
    assert not list(tmp_path.iterdir())


def test_disclaimer_matches_readme_and_snapshot():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").replace("**", "").replace("> ", "").split())
    first_three = ". ".join(gen.DISCLAIMER.split(". ")[:3]) + "."
    assert first_three in readme
    manifest = json.loads((ROOT / "data" / "derived" / "chunks" / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["corpus_snapshot"] == "2026-10-18" and "18 October 2026" in gen.DISCLAIMER


# Model client ----------------------------------------------------------------------------------


def mock_client(handler, monkeypatch, api_key="sk-test-fake-SECRET-abc"):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # must be ignored
    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return llm.AnthropicLLMClient(api_key, max_retries=0, http_client=http)


def message_json(text):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 3,
                      "cache_read_input_tokens": 0}}


def test_client_sends_to_the_api_host_and_parses_the_response(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx2.Response(200, json=message_json(json.dumps(ANSWER)), headers={"request-id": "req_1"})

    client = mock_client(handler, monkeypatch)
    response = client.create(llm.request_params("s", "u", prompt.OUTPUT_SCHEMA))
    assert seen["url"].startswith("https://api.anthropic.com/")
    assert not {"temperature", "top_p", "top_k"} & set(seen["body"])
    assert response.stop_reason == "end_turn" and json.loads(response.text) == ANSWER
    assert response.usage == {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 3,
                              "cache_read_input_tokens": 0}


def test_client_errors_name_type_and_status_only(monkeypatch):
    def handler(request):
        return httpx2.Response(401, json={"type": "error", "error": {
            "type": "authentication_error", "message": "invalid x-api-key sk-test-fake-SECRET-abc"}})

    client = mock_client(handler, monkeypatch)
    with pytest.raises(llm.GenerationError) as info:
        client.create(llm.request_params("s", "u", prompt.OUTPUT_SCHEMA))
    assert str(info.value) == "AuthenticationError (HTTP 401)"
    assert "SECRET" not in repr(info.value) and info.value.__cause__ is None


def test_missing_key_never_falls_back_to_ambient_credentials(monkeypatch):
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient-should-not-be-used")
    with pytest.raises(llm.GenerationError, match="LLM_API_KEY is not set"):
        llm.AnthropicLLMClient()
    with pytest.raises(llm.GenerationError):
        gen.generate(QUESTION, retriever=FakeRetriever(), store=STORE, log_dir=None)


# No test-split question in a Phase 5 artefact (D-29) --------------------------------------------


def _artefacts():
    paths = [ROOT / "docs" / "phase05_generation" / "generation_design.md",
             ROOT / "data" / "curated" / "disclosure_policy.yml"]
    paths += sorted((ROOT / "python" / "generation").glob("*.py"))
    paths += sorted((ROOT / "eval" / "generation").rglob("*.*"))
    paths += sorted((ROOT / "tests").glob("test_*gen*.py")) + [ROOT / "tests" / "test_disclosure.py"]
    paths += sorted((ROOT / "docs" / "phase05_generation").glob("exit_check*"))
    return [p for p in paths if p.is_file() and p.suffix in {".py", ".md", ".yml", ".json", ".jsonl", ".csv"}]


def test_no_test_question_text_in_phase5_artefacts():
    questions = run_eval.load_questions("test")
    assert len(questions) == 30
    leaks = []
    for path in _artefacts():
        text = path.read_text(encoding="utf-8").lower()
        leaks += [(path.name, q["id"]) for q in questions if q["question"].lower() in text]
    assert not leaks, leaks


def test_exit_questions_are_dev_or_new():
    rows = [json.loads(line) for line in (ROOT / "eval" / "generation" / "exit_questions.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()]
    dev = {q["id"] for q in run_eval.load_questions("dev")}
    assert len(rows) == 15
    assert {r["id"] for r in rows if r["source"] == "questions_v1"} <= dev
    assert sum(r["source"] == "questions_v1" for r in rows) == 10
    assert {r["id"] for r in rows if r["source"] == "new"} == {f"G0{i}" for i in range(1, 6)}
    assert all("question" not in r for r in rows if r["source"] == "questions_v1")
