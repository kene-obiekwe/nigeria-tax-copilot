"""The Phase 7 tool loop in generation (D-117 to D-123; tools design, sections 2, 5, 6, 7, 8 and 11).

Fake clients only: a scripted generation client, a fake verifier, a fake retriever with a fake
section lookup, and the committed chunk files. No key, no database and no API call. Assertion
messages name ids only."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from embed.load import CHUNKS, read_chunks
from generation import audit_log, generate as gen, llm, note_tagger, prompt, verify
from retrieval.retrieve import Retrieved
from tools import taxcalc

STORE = gen.FileCorpusStore()
RATES = taxcalc.load_rates()
CHUNK_IDS = ["nta2025:s59", "nta2025:s202:def:small-company", "nta2025:s56"]
BY_SECTION: dict[tuple[str, str], list[str]] = {}
for _c in read_chunks(CHUNKS):
    if _c["section"]:
        BY_SECTION.setdefault((_c["act_id"], str(_c["section"])), []).append(_c["chunk_id"])

# Section 8, item 1: the tools-off request, byte for byte. Equal to the request that the base commit's
# code (cd0b9da, before Phase 7) builds for the same question and fake clients (checked when pinned).
TOOLS_OFF_REQUEST_SHA256 = "204b474a42711c633753b97594f0b3cbd463ff72e42588fe1b7a85e942aa26df"
QUESTION = "Does the development levy apply to a small company?"
E01 = ("We are invoicing a customer ₦2,600,000 for printing services, before VAT. How much VAT should we add to "
       "the invoice?")
E03 = ("Our company's gross turnover for the year was ₦180 million and its total profits were ₦36 million. "
       "Work out its company income tax.")
THINKING = {"type": "thinking", "thinking": "Which tool fits?", "signature": "c2lnbmF0dXJlLWJ5dGVz=="}
USAGE = {"input_tokens": 1500, "output_tokens": 400, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 700}


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        return [0.0, 0.0, 1.0]


class FakeRetriever:
    """Retrieval returns `chunk_ids`; the section lookup returns a section's chunks in file order."""

    def __init__(self, chunk_ids=CHUNK_IDS, score=0.5):
        self.chunk_ids, self.score = chunk_ids, score
        self.client, self.retrieve_calls, self.lookups = FakeEmbedder(), 0, []

    def retrieve(self, question, k=5, filters=None, query_vector=None):
        self.retrieve_calls += 1
        self.query_vector = query_vector
        return [Retrieved(c, i, self.score - i / 100, 1 - self.score, ["fake"])
                for i, c in enumerate(self.chunk_ids[:k], 1)]

    def lookup(self, refs, query_vector, allowed=None):
        self.lookups.append((refs, query_vector))
        return [(cid, i / 10, ref, ref.act_id) for ref in refs
                for i, cid in enumerate(BY_SECTION.get((ref.act_id, ref.section), []))]


class ScriptLLM:
    """Answers each call with the next scripted step: (content blocks, stop_reason) or an exception.
    Keeps a deep copy of every request."""
    model = llm.MODEL

    def __init__(self, *steps):
        self.steps, self.requests = list(steps), []

    def create(self, params):
        self.requests.append(copy.deepcopy(params))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        content, stop = step
        return llm.LLMResponse(copy.deepcopy(content), stop, None, dict(USAGE), f"req_{len(self.requests)}",
                               self.model, 10)


def fields(scenario, **values):
    return {k: None for k in taxcalc.INPUT_FIELDS} | {"scenario": scenario} | values


VAT = fields("vat_on_supply", amount="2600000", amount_basis="value_before_vat")
CIT = fields("company_income_tax", amount="36000000", gross_turnover="180000000")


def use(tool_id, inputs, name="calculate_liability"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inputs}


def tool_turn(*blocks):
    return [THINKING, *blocks], "tool_use"


def final(claims, gaps=(), calculations=(), status="answered"):
    data = {"status": status, "claims": [{"text": t, "passages": p} for t, p in claims], "gaps": list(gaps),
            "calculations": list(calculations)}
    return [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": json.dumps(data)}], \
        "end_turn"


class FakeVerifier:
    """Supports every statement by quoting the first words of its first passage, except statements
    starting with "Unsupported"; every gap is missing information."""
    model = llm.VERIFIER_MODEL

    def __init__(self):
        self.requests = []

    def create(self, params):
        self.requests.append(params)
        message = params["messages"][0]["content"]
        if params["output_config"]["format"]["schema"] == prompt.GAP_SCHEMA:
            out = {"gaps": [{"index": i, "class": "missing_information"} for i in range(message.count("<gap index="))]}
        else:
            label = message.split('label="')[1].split('"')[0]
            first = " ".join(message.split("</passage>")[0].split("\n", 2)[2].split()[:6])
            said = message.split("<statement>\n")[1].split("\n</statement>")[0]
            if said.startswith("Unsupported"):
                out = {"reasoning": "no", "verdict": "unsupported", "reason": "not_in_passages",
                       "unsupported_part": "all", "evidence": [], "citations": [{"label": label, "supports": False}]}
            else:
                out = {"reasoning": "yes", "verdict": "supported", "reason": "none", "unsupported_part": "",
                       "evidence": [{"label": label, "quote": first}], "citations": [{"label": label, "supports": True}]}
        return llm.LLMResponse([{"type": "text", "text": json.dumps(out)}], "end_turn", None,
                               {"input_tokens": 400, "output_tokens": 150, "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0}, "req_v", self.model, 9)


TOOLS = gen.GenerationConfig(tools=gen.ToolsConfig(enabled=True))
TOOLS_NO_VERIFY = gen.GenerationConfig(verify=False, tools=gen.ToolsConfig(enabled=True))
VAT_CLAIM = ("VAT is charged on the value of all taxable supplies at the rate of 7.5%.", ["P9"])


def run(question, *steps, config=TOOLS, tmp_path=None, retriever=None, verifier=None, tagger=None):
    fake = ScriptLLM(*steps)
    retriever = retriever or FakeRetriever()
    answer = gen.generate(question, retriever=retriever, llm=fake, store=STORE, log_dir=tmp_path, config=config,
                          verifier=verifier or FakeVerifier(), tagger=tagger)
    return answer, fake, retriever


def records(tmp_path):
    return [r for p in sorted(tmp_path.glob("*.jsonl")) for r in audit_log.read_records(p)]


# Section 8, item 1: tools off ------------------------------------------------------------------


def test_tools_off_request_is_the_frozen_request_byte_for_byte(tmp_path):
    output = {"status": "answered", "claims": [{"text": "Small companies are not charged the development levy.",
                                                "passages": ["P1"]}], "gaps": []}
    answer, fake, retriever = run(QUESTION, ([{"type": "text", "text": json.dumps(output)}], "end_turn"),
                                  config=gen.GenerationConfig(verify=False), tmp_path=tmp_path)
    assert answer.status == "answered" and len(fake.requests) == 1
    request = fake.requests[0]
    assert set(request) == {"model", "max_tokens", "system", "messages", "thinking", "output_config"}
    assert request["system"][0]["text"] == prompt.SYSTEM_PROMPT
    assert request["output_config"]["format"]["schema"] == prompt.OUTPUT_SCHEMA
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert digest == TOOLS_OFF_REQUEST_SHA256
    assert retriever.client.calls == 0 and retriever.query_vector is None  # no extra embedding with tools off
    (record,) = records(tmp_path)
    assert record["schema"] == "gen-log/2" and "tools" not in record
    assert record["request"]["prompt_version"] == "gen-prompt/8" and answer.tools is None and answer.computations == []


def test_tools_on_prompt_is_gen_prompt_8_with_exact_replacements():
    assert prompt.TOOLS_PROMPT_VERSION == "gen-prompt/9" and prompt.TOOLS_SCHEMA_VERSION == "gen-schema/2"
    assert prompt.SYSTEM_PROMPT_TOOLS != prompt.SYSTEM_PROMPT and "(P1 to P14)" in prompt.SYSTEM_PROMPT_TOOLS
    assert "calculate_liability" in prompt.SYSTEM_PROMPT_TOOLS and "Do no arithmetic." in prompt.SYSTEM_PROMPT_TOOLS
    schema = prompt.OUTPUT_SCHEMA_TOOLS
    assert schema["required"] == ["status", "claims", "gaps", "calculations"]
    assert [t["name"] for t in prompt.TOOL_DEFS] == ["lookup_section", "calculate_liability"]
    calc = prompt.TOOL_DEFS[1]["input_schema"]
    assert set(calc["required"]) == set(taxcalc.INPUT_FIELDS) == set(calc["properties"])
    assert calc["properties"]["scenario"]["enum"] == list(taxcalc.SCENARIOS)


def schema_enum_problems(node, path="input_schema") -> list[str]:
    """Walks a tool schema: every enum value must match the type declared beside it (the API's tool-schema
    validator refuses an enum value that its sibling `type` does not allow, for example None beside
    ["string", "null"]). Returns the paths and values that do not."""
    kinds = {"string": str, "boolean": bool, "integer": int, "null": type(None), "number": (int, float)}
    problems = []
    if isinstance(node, dict):
        if "enum" in node and "type" in node:
            types = node["type"] if isinstance(node["type"], list) else [node["type"]]
            for value in node["enum"]:
                # a list type with a null member is where the API refused the enum (D-130): never allowed
                if len(types) > 1 or not isinstance(value, kinds[types[0]]) or (isinstance(value, bool) and types[0] != "boolean"):
                    problems.append(f"{path}: enum value {value!r} beside type {node['type']!r}")
        for key, child in node.items():
            problems += schema_enum_problems(child, f"{path}.{key}")
    elif isinstance(node, list):
        for i, child in enumerate(node):
            problems += schema_enum_problems(child, f"{path}[{i}]")
    return problems


def test_every_tool_schema_enum_value_matches_its_declared_type():
    """D-130: step A's HTTP 400 came from a nullable enum. Walks both tool schemas."""
    assert prompt.TOOL_DEFS_VERSION == "tool-defs/2"
    for tool in prompt.TOOL_DEFS:
        assert schema_enum_problems(tool["input_schema"], tool["name"]) == [], tool["name"]
    props = prompt.TOOL_DEFS[1]["input_schema"]["properties"]
    for field in ("amount_basis", "provision"):
        assert {"type": "null"} in props[field]["anyOf"] and "enum" not in props[field]
        assert None not in props[field]["anyOf"][0]["enum"]


def test_the_schema_walk_catches_the_old_nullable_enum_form():
    old = {"type": "object", "properties": {
        "amount_basis": {"type": ["string", "null"], "enum": ["value_before_vat", "consideration_including_vat", None]},
        "provision": {"type": ["string", "null"], "enum": ["ntaa2025:s100", None]},
        "scenario": {"type": "string", "enum": ["a", 1]}}}
    problems = schema_enum_problems(old)
    assert len(problems) == 6  # 3 + 2 values beside a list type, and 1 beside "string"
    assert any("amount_basis" in p and "None" in p for p in problems) and any("scenario" in p and "1" in p for p in problems)


# The loop ----------------------------------------------------------------------------------------


def test_calculation_then_answer_shows_the_computed_item(tmp_path):
    answer, fake, retriever = run(E01, tool_turn(use("toolu_1", VAT)), final([VAT_CLAIM], calculations=["C1"]),
                                  tmp_path=tmp_path)
    assert answer.status == "answered", (answer.status, answer.error)
    assert [r["tool_choice"] for r in fake.requests] == [{"type": "auto"}, {"type": "auto"}]
    assert all(r["tools"] == prompt.TOOL_DEFS and r["system"][0]["text"] == prompt.SYSTEM_PROMPT_TOOLS
               for r in fake.requests)
    assert retriever.client.calls == 1 and retriever.query_vector == [0.0, 0.0, 1.0]
    assert [p.label for p in answer.passages][-1] == "P9" and answer.passages[-1].chunk_id == "nta2025:s148"
    (comp,) = answer.computations
    assert (comp["id"], comp["status"], comp["result"], comp["shown"]) == ("C1", "computed", "195000.00", True)
    assert comp["checks"]["K4"] == {"vat_rate": [1]}
    assert gen.CALCULATION_HEADING in answer.rendered
    assert "- Working: ₦2,600,000 x 7.5% = ₦195,000" in answer.rendered
    assert "- Result: VAT of ₦195,000" in answer.rendered
    assert taxcalc.ROUNDED_LINE in answer.rendered and taxcalc.NOT_ASSESSMENT_LINE in answer.rendered
    assert answer.rendered.index(gen.CALCULATION_HEADING) < answer.rendered.index("Sources")
    assert "195,000" not in " ".join(c.text for c in answer.claims)
    # The tool result is data the model reads: the computed result and the new passage block.
    result = fake.requests[1]["messages"][2]["content"][0]
    assert result["tool_use_id"] == "toolu_1" and result["is_error"] is False
    assert '"result": "₦195,000"' in result["content"] and '<passage label="P9"' in result["content"]


def test_log_is_gen_log_3_and_round_trips(tmp_path):
    answer, fake, _ = run(E01, tool_turn(use("toolu_1", VAT)), final([VAT_CLAIM], calculations=["C1"]),
                          tmp_path=tmp_path)
    (record,) = records(tmp_path)
    assert record["schema"] == "gen-log/3" and tuple(record) == audit_log.FIELDS_V3
    assert set(audit_log.FIELDS) <= set(record)  # gen-log/2 readers find every field they read
    assert record["request"]["prompt_version"] == "gen-prompt/9" and record["request"]["tool_choice"] == {"type": "auto"}
    assert record["response"]["request_id"] == "req_2" and record["parsed"]["calculations"] == ["C1"]
    tools = record["tools"]
    assert [c["turn"] for c in tools["calls"]] == [1, 2] and tools["settings"]["enabled"] is True
    assert tools["tool_uses"][0]["name"] == "calculate_liability" and tools["tool_uses"][0]["is_error"] is False
    assert tools["passages_added"] == [{"label": "P9", "chunk_id": "nta2025:s148",
                                        "reason": "calculation source: C1 vat_on_supply"}]
    (comp,) = tools["computations"]
    assert all(s["by"].startswith("taxcalc.") for s in comp["steps"])
    assert comp["code"] == {"calc_version": "calc/1", "rates_sha256": taxcalc.RATES_SHA256}
    assert comp["input_matches"]["amount"] == "amt:2600000"
    one = llm.cost_usd(USAGE, llm.MODEL)
    assert answer.cost_usd == pytest.approx(2 * one) and tools["cost_usd"] == pytest.approx(2 * one, abs=1e-6)
    assert answer.usage["input_tokens"] == 3000 and tools["latency_ms"] == 20
    assert json.loads(json.dumps(record)) == record


def test_thinking_blocks_go_back_unchanged():
    first = tool_turn(use("toolu_1", VAT))
    _, fake, _ = run(E01, first, final([VAT_CLAIM], calculations=["C1"]), config=TOOLS_NO_VERIFY)
    sent = fake.requests[1]["messages"]
    assert sent[0] == {"role": "user", "content": fake.requests[0]["messages"][0]["content"]}
    assert sent[1]["role"] == "assistant"
    assert json.dumps(sent[1]["content"], sort_keys=True) == json.dumps(first[0], sort_keys=True)


def test_parallel_calls_are_answered_in_one_message_in_call_order():
    question = E03
    levy = fields("development_levy", amount="36000000", gross_turnover="180000000", non_resident=False)
    _, fake, _ = run(question, tool_turn(use("toolu_a", CIT), use("toolu_b", levy)), final([], status="not_covered",
                     gaps=["The question does not say."]), config=TOOLS_NO_VERIFY)
    results = fake.requests[1]["messages"][2]
    assert results["role"] == "user" and [r["tool_use_id"] for r in results["content"]] == ["toolu_a", "toolu_b"]
    assert all(r["type"] == "tool_result" for r in results["content"])


def test_the_last_call_has_tool_choice_none_and_must_end_the_turn():
    answer, fake, _ = run(E01, tool_turn(use("t1", VAT)), tool_turn(use("t2", VAT)), tool_turn(use("t3", VAT)),
                          config=TOOLS_NO_VERIFY)
    assert [r["tool_choice"]["type"] for r in fake.requests] == ["auto", "auto", "none"]
    assert answer.status == "error" and answer.error == "stop_reason tool_use"
    assert gen.CALCULATION_HEADING not in answer.rendered and not answer.claims


def test_an_api_error_mid_loop_is_an_error(tmp_path):
    answer, fake, _ = run(E01, tool_turn(use("t1", VAT)), llm.GenerationError("overloaded"), tmp_path=tmp_path)
    assert (answer.status, answer.error) == ("error", "overloaded") and len(fake.requests) == 2
    assert gen.ERROR_TEXT in answer.rendered and gen.CALCULATION_HEADING not in answer.rendered
    (record,) = records(tmp_path)
    assert record["tools"]["calls"][1]["error"] == "overloaded" and record["tools"]["calls"][1]["response"] is None


def test_a_tool_that_raises_gives_an_error_result_and_nothing_shown(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("bad table row")
    monkeypatch.setattr(gen, "calculate", boom)
    answer, fake, _ = run(E01, tool_turn(use("t1", VAT)), final([VAT_CLAIM], calculations=["C1"]))
    result = fake.requests[1]["messages"][2]["content"][0]
    assert (result["is_error"], result["content"]) == (True, gen.TOOL_FAILED_TEXT)
    assert answer.tools["tool_uses"][0]["exception"] == "ValueError: bad table row"
    assert [(c["id"], c["status"], c["shown"]) for c in answer.computations] == [("C1", "error", False)]
    assert answer.computations[0]["withheld_reason"] == "error"
    assert gen.CALCULATION_HEADING not in answer.rendered


def test_an_unknown_tool_gets_an_error_result():
    _, fake, _ = run(E01, tool_turn(use("t1", {"x": 1}, name="web_search")), final([VAT_CLAIM]),
                     config=TOOLS_NO_VERIFY)
    result = fake.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] is True and result["content"].startswith("Unknown tool 'web_search'")


def test_calculation_limit():
    _, fake, _ = run(E01, tool_turn(use("t1", VAT), use("t2", VAT), use("t3", VAT)), final([VAT_CLAIM]),
                     config=TOOLS_NO_VERIFY)
    content = fake.requests[1]["messages"][2]["content"]
    assert [r["is_error"] for r in content] == [False, False, True] and content[2]["content"] == gen.CALC_LIMIT_TEXT


def test_lookup_adds_passages_and_has_a_limit():
    def look(tool_id, number):
        return use(tool_id, {"act": "nta2025", "section_number": number, "subsection": None}, name="lookup_section")
    answer, fake, retriever = run(QUESTION, tool_turn(look("l1", "148"), look("l2", "149"), look("l3", "57")),
                                  final([VAT_CLAIM]), config=TOOLS_NO_VERIFY)
    content = fake.requests[1]["messages"][2]["content"]
    assert [r["is_error"] for r in content] == [False, False, True] and content[2]["content"] == gen.LOOKUP_LIMIT_TEXT
    assert content[0]["content"].startswith("Section 148 of the Nigeria Tax Act 2025: 1 chunk")
    added = [(p.label, p.chunk_id) for p in answer.passages if p.label in prompt.TOOL_PASSAGE_LABELS]
    assert added[0] == ("P9", "nta2025:s148") and len(retriever.lookups) == 2
    assert retriever.lookups[0][1] == [0.0, 0.0, 1.0]  # the question's vector, embedded once
    assert answer.passages[-1].reasons[0].startswith("lookup_section: Section 149, Nigeria Tax Act 2025")


def test_a_lookup_outside_the_corpus_is_an_error_result_not_an_answer():
    out = use("l1", {"act": "ntaa2025", "section_number": "67", "subsection": None}, name="lookup_section")
    answer, fake, _ = run(E01, tool_turn(out), final([], status="not_covered", gaps=["Section 67 is not shown."]),
                          config=TOOLS_NO_VERIFY)
    result = fake.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] is True and '("Recovery of tax") is outside the corpus' in result["content"]
    assert answer.status == "not_covered" and [p.label for p in answer.passages] == ["P1", "P2", "P3"]


# Guardrails around the tools (design section 6) ------------------------------------------------


@pytest.mark.parametrize("question,reason", [
    ("", "invalid_question"),
    ("What does section 231 of the Nigeria Tax Act 2025 say?", "no_such_section"),
    ("What does section 58 of the Nigeria Tax Act 2025 say?", "section_outside_v1"),
    ("How much corporate income tax does a company in Ghana pay under Ghanaian law?", "foreign_law")])
def test_pre_check_refusals_make_no_model_call(question, reason):
    answer, fake, retriever = run(question)
    assert (answer.status, answer.refusal_reason) == ("refused", reason)
    assert fake.requests == [] and retriever.client.calls == 0 and retriever.retrieve_calls == 0


def test_low_relevance_makes_no_model_call():
    answer, fake, _ = run(E01, retriever=FakeRetriever(score=0.1))
    assert (answer.status, answer.refusal_reason, fake.requests) == ("refused", "low_relevance", [])


def test_a_model_decline_shows_no_computed_item():
    answer, _, _ = run(E01, tool_turn(use("t1", VAT)),
                       final([], status="not_covered", gaps=["The passages do not settle it."], calculations=["C1"]))
    assert answer.status == "not_covered" and gen.CALCULATION_HEADING not in answer.rendered
    (comp,) = answer.computations
    assert comp["checks"]["K2"] is False and comp["withheld_reason"] == "status not_covered"


def test_a_verifier_refusal_shows_no_computed_item():
    answer, _, _ = run(E01, tool_turn(use("t1", VAT)),
                       final([("Unsupported: VAT is charged at the rate of 7.5%.", ["P9"])], calculations=["C1"]))
    assert answer.status == "refused" and answer.refusal_reason == "unsupported_answer"
    assert gen.CALCULATION_HEADING not in answer.rendered and answer.computations[0]["shown"] is False


def test_a_computed_figure_in_a_claim_is_removed_by_the_rule_check():
    answer, _, _ = run(E01, tool_turn(use("t1", VAT)),
                       final([VAT_CLAIM, ("The VAT to add is ₦195,000 at 7.5%.", ["P9"])], calculations=["C1"]))
    assert answer.status == "answered" and [c.text for c in answer.claims] == [VAT_CLAIM[0]]
    rule = answer.verification.checks[1].rule
    assert rule.passed is False and "amt:195000" in rule.missing
    assert gen.CALCULATION_HEADING in answer.rendered  # the rate claim still confirms the item (K4)


def test_the_gap_figure_filter_removes_a_figure_from_nowhere():
    gaps = ["The VAT of ₦195,000 cannot be confirmed from the passages.",
            "Whether the ₦2,600,000 supply is exempt is not settled by the passages."]
    answer, _, _ = run(E01, tool_turn(use("t1", VAT)), final([VAT_CLAIM], gaps=gaps, calculations=["C1"]))
    log = {g["text"]: g for g in answer.verification.as_log()["gaps"]["gaps"]}
    assert log[gaps[0]]["kept"] is False and log[gaps[0]]["reason"] == "figure_not_in_source"
    assert log[gaps[1]]["kept"] is True
    assert answer.gaps == [gaps[1]]


def test_the_gap_figure_filter_is_off_with_tools_off():
    assert verify.gap_figures_not_in_source("₦195,000 is due.", {"amt:2600000"}) == ["amt:195000"]
    gap = "The VAT of ₦195,000 cannot be confirmed from the passages."
    fake = ScriptLLM(([{"type": "text", "text": json.dumps({"status": "answered", "claims": [
        {"text": VAT_CLAIM[0], "passages": ["P1"]}], "gaps": [gap]})}], "end_turn"))
    answer = gen.generate(E01, retriever=FakeRetriever(["nta2025:s148"]), llm=fake, store=STORE, log_dir=None,
                          config=gen.GenerationConfig(), verifier=FakeVerifier())
    assert answer.gaps == [gap]


def test_k3_an_unlisted_calculation_is_not_shown():
    answer, _, _ = run(E01, tool_turn(use("t1", VAT)), final([VAT_CLAIM], calculations=[]))
    assert answer.status == "answered" and answer.computations[0]["withheld_reason"] == "not_listed"
    assert gen.CALCULATION_HEADING not in answer.rendered and "calculation was not shown" not in answer.rendered


def test_a_listed_id_that_was_not_computed_is_rejected(tmp_path):
    claim = (VAT_CLAIM[0], ["P1"])
    answer, _, _ = run(E01, final([claim], calculations=["C2"]), retriever=FakeRetriever(["nta2025:s148"]),
                       tmp_path=tmp_path)
    assert answer.status == "answered" and answer.computations == []
    (record,) = records(tmp_path)
    assert {"claim": None, "flag": "calculation_id_rejected", "id": "C2"} in record["parsed"]["flags"]
    assert record["parsed"]["calculations"] == [] and gen.CALCULATION_HEADING not in answer.rendered


def test_k4_withholds_when_no_kept_claim_confirms_the_rate():
    answer, _, _ = run(E01, tool_turn(use("t1", VAT)),
                       final([("VAT is charged on the value of all taxable supplies.", ["P9"])], calculations=["C1"]))
    assert answer.status == "answered" and answer.computations[0]["withheld_reason"] == "rate_not_confirmed"
    assert gen.CALCULATION_HEADING not in answer.rendered and gen.RATE_NOT_CONFIRMED_LINE[0] in answer.rendered


def test_k4_an_8_percent_claim_cannot_stand_in_for_the_4_percent_levy():
    question = ("Our company is resident in Nigeria, its gross turnover is ₦900 million and its assessable "
                "profits are ₦250 million. How much development levy is due?")
    comp = taxcalc.calculate("C1", "development_levy", fields("development_levy", amount="250000000",
                             gross_turnover="900000000", non_resident=False), question, RATES)
    assert comp.computed
    session = SimpleNamespace(computations=[comp], rates=RATES)

    def kept(*claims):
        return SimpleNamespace(kept=[SimpleNamespace(no=i, text=t, kept_chunk_ids=ids)
                                     for i, (t, ids) in enumerate(claims, 1)])
    small = ("A small company has gross turnover of ₦50,000,000 or less.", ["nta2025:s202:def:small-company"])
    wrong = kept(("The development levy is charged at 8% of assessable profits.", ["nta2025:s59"]), small)
    records, shown = gen.computed_checks(session, "answered", ["C1"], wrong, set())
    assert shown == [] and records[0]["withheld_reason"] == "rate_not_confirmed"
    assert records[0]["checks"]["K4"] == {"dev_levy_rate": [], "small_turnover_max": [2]}
    right = kept(("The development levy is charged at 4% of assessable profits.", ["nta2025:s59"]), small)
    records, shown = gen.computed_checks(session, "answered", ["C1"], right, set())
    assert shown == [comp] and records[0]["checks"]["K4"] == {"dev_levy_rate": [1], "small_turnover_max": [2]}
    other_chunk = kept(("The development levy is charged at 4% of assessable profits.", ["nta2025:s56"]), small)
    assert gen.computed_checks(session, "answered", ["C1"], other_chunk, set())[1] == []


def test_k5_a_pattern_note_vetoes_the_calculation():
    question = ("In 2024 we invoiced a customer ₦2,600,000 for printing services, before VAT. How much VAT should "
                "we add to the invoice?")
    answer, _, _ = run(question, tool_turn(use("t1", VAT)), final([VAT_CLAIM], calculations=["C1"]))
    assert "scope:pre_2026_periods" in {n.ref for n in answer.notes}
    assert answer.computations[0]["withheld_reason"] == "veto scope:pre_2026_periods"
    assert gen.CALCULATION_HEADING not in answer.rendered
    assert ("1 calculation was not shown because the note on tax periods before 2026 applies to this question."
            in answer.rendered)


class FakeTagger:
    model = note_tagger.TAGGER_MODEL

    def __init__(self, labels):
        self.labels = labels

    def create(self, params):
        return llm.LLMResponse([{"type": "text", "text": json.dumps({"reasoning": "fake", "labels": self.labels})}],
                               "end_turn", None, {"input_tokens": 900, "output_tokens": 60,
                                                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                               "req_tag", self.model, 7)


def test_k5_a_tagger_note_is_joined_before_the_veto():
    claims = [("A small company has gross turnover of ₦50,000,000 or less.", ["P2"]),
              ("Companies other than small companies pay tax at 30 per cent.", ["P3"])]
    plain, _, _ = run(E03, tool_turn(use("t1", CIT)), final(claims, calculations=["C1"]))
    assert plain.status == "answered", (plain.status, plain.error)
    assert plain.computations[0]["shown"] is True and "- Result: company income tax of ₦10,800,000" in plain.rendered
    vetoed, _, _ = run(E03, tool_turn(use("t1", CIT)), final(claims, calculations=["C1"]),
                       tagger=FakeTagger(["personal_income_tax"]))
    assert "scope:personal_income_tax" in {n.ref for n in vetoed.notes}
    assert vetoed.computations[0]["withheld_reason"] == "veto scope:personal_income_tax"
    assert ("1 calculation was not shown because the note on personal income tax applies to this question."
            in vetoed.rendered) and gen.CALCULATION_HEADING not in vetoed.rendered


def test_a_declined_calculation_renders_nothing():
    x2 = "Our company made total profits of ₦25 million this year. How much company income tax does it pay?"
    answer, fake, _ = run(x2, tool_turn(use("t1", fields("company_income_tax", amount="25000000"))),
                          final([("Companies other than small companies pay tax at 30 per cent.", ["P3"])],
                                gaps=["The gross turnover is not given."], calculations=["C1"]))
    result = json.loads(fake.requests[1]["messages"][2]["content"][0]["content"])
    assert result["status"] == "declined"
    assert answer.computations[0]["status"] == "declined" and answer.computations[0]["withheld_reason"] == "declined"
    assert gen.CALCULATION_HEADING not in answer.rendered and "calculation was not shown" not in answer.rendered


# D-129: a VAT-inclusive amount also rests on the Section 149(1)(a) value rule ---------------------

E02 = ("A customer paid us ₦500,000 for taxable goods, and that price already includes VAT. How much of the "
       "₦500,000 is VAT?")
VAT_INCL = fields("vat_on_supply", amount="500000", amount_basis="consideration_including_vat")
VALUE_RULE_CLAIM = ("For a money consideration, the value of a taxable supply is the amount which with the addition "
                    "of the VAT chargeable is equal to the consideration.", ["P10"])


def test_e02_is_shown_when_a_section_149_claim_is_kept():
    answer, fake, _ = run(E02, tool_turn(use("t1", VAT_INCL)), final([VAT_CLAIM, VALUE_RULE_CLAIM], calculations=["C1"]))
    assert answer.status == "answered", (answer.status, answer.error)
    assert [(p.label, p.chunk_id) for p in answer.passages[-2:]] == [("P9", "nta2025:s148"), ("P10", "nta2025:s149")]
    (comp,) = answer.computations
    assert comp["relies_on"] == ["vat_rate", "vat_inclusive_value"]
    assert comp["checks"]["K4"] == {"vat_rate": [1], "vat_inclusive_value": [2]} and comp["shown"] is True
    assert "- Result: VAT of ₦34,883.72" in answer.rendered
    result = json.loads(fake.requests[1]["messages"][2]["content"][0]["content"].split("\n")[0])
    assert result["relies_on"] == [
        {"constant": "vat_rate", "states": RATES["vat_rate"].states, "passage": "P9"},
        {"rule": "vat_inclusive_value", "states": taxcalc.RULES["vat_inclusive_value"].states, "passage": "P10"}]


def test_e02_is_withheld_when_no_kept_claim_cites_section_149():
    answer, _, _ = run(E02, tool_turn(use("t1", VAT_INCL)), final([VAT_CLAIM], calculations=["C1"]))
    assert answer.status == "answered"
    (comp,) = answer.computations
    assert comp["checks"]["K4"] == {"vat_rate": [1], "vat_inclusive_value": []}
    assert comp["withheld_reason"] == "rate_not_confirmed" and gen.CALCULATION_HEADING not in answer.rendered
    removed, _, _ = run(E02, tool_turn(use("t1", VAT_INCL)),
                        final([VAT_CLAIM, ("Unsupported: " + VALUE_RULE_CLAIM[0], ["P10"])], calculations=["C1"]))
    assert removed.computations[0]["checks"]["K4"]["vat_inclusive_value"] == []  # a removed claim does not count
    assert removed.computations[0]["shown"] is False


def test_the_value_rule_needs_no_token():
    claim = SimpleNamespace(no=1, text="Section 149 sets out how the value is found.", kept_chunk_ids=["nta2025:s149"])
    rate = SimpleNamespace(no=2, text="VAT is charged at the rate of 7.5%.", kept_chunk_ids=["nta2025:s148"])
    comp = taxcalc.calculate("C1", "vat_on_supply", VAT_INCL, E02, RATES)
    session = SimpleNamespace(computations=[comp], rates=RATES)
    records, shown = gen.computed_checks(session, "answered", ["C1"], SimpleNamespace(kept=[claim, rate]), set())
    assert records[0]["checks"]["K4"] == {"vat_rate": [2], "vat_inclusive_value": [1]} and shown == [comp]
