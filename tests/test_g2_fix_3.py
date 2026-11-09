"""G2 fix build 3 (G2 fix design 3, D-98 to D-106): the definition index and attachment, the
commencement lookup, the 8-passage prompt, the relevance floor over attachments, the percent rule in
the shared parser, the `norm/6` evidence normaliser, the D-104 tuning record and the adversarial_v4
entry.

Questions written here are the build session's own words, or the commencement probes of the design's
section 3.3 (written with the rule, in-sample); tuning, dev and test questions are named by id only.
eval/guardrails/adversarial_v4.jsonl is never opened (a fixture fails any test that tries). Tests
that read the database fail, not skip, when it is down; query vectors come from the cache only."""
import builtins
import dataclasses
import hashlib
import importlib.util
import json
import pathlib
import sys

import pytest

from generation import generate as gen, guardrails, prompt, verify
from retrieval import references
from retrieval.commencement import load_commencement
from retrieval.definitions import DefinitionIndex, load_scopes, term_of, term_pattern
from retrieval.retrieve import Retrieved, Retriever

ROOT = gen.ROOT
OTHER_ACTS = references.load_other_acts()
V2 = json.loads((ROOT / "eval" / "retrieval" / "config_v2.json").read_text(encoding="utf-8"))
V3_PATH = ROOT / "eval" / "retrieval" / "config_v3.json"
V3 = json.loads(V3_PATH.read_text(encoding="utf-8"))
POLICY_PATH = ROOT / "data" / "curated" / "retrieval_policy.yml"
V4_SHA256 = "83c76466b1b6ad7054f8add0ea0d4e353cdac020ea0e381371cbc103e40e1dcb"  # D-106, the writer's value


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return sys.modules[name]


run_adversarial = load_module("run_adversarial", ROOT / "eval" / "guardrails" / "run_adversarial.py")
run_eval = run_adversarial.run_eval


@pytest.fixture(autouse=True)
def never_open_v4(monkeypatch):
    """D-105, D-106: the build session never opens the v4 questions file. Any open of it fails the test."""
    real_open, real_path_open = builtins.open, pathlib.Path.open

    def guard(path):
        if "adversarial_v4" in str(path):
            raise AssertionError("a test tried to open the adversarial_v4 questions file")

    def checked_open(file, *args, **kwargs):
        guard(file)
        return real_open(file, *args, **kwargs)

    def checked_path_open(self, *args, **kwargs):
        guard(self)
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(pathlib.Path, "open", checked_path_open)


@pytest.fixture(scope="module")
def conn():
    from check_pgvector import connect
    with connect() as c:
        yield c


@pytest.fixture(scope="module")
def index(conn):
    return DefinitionIndex.from_db(conn)


def cached(question):
    """The cached query vector, or a failure: these tests never embed."""
    cache = run_eval.QueryCache(V3["model"])
    from embed.clients import CLIENTS
    digest = cache.key(CLIENTS.get(cache.model), question)
    assert digest in cache.records, "no cached query vector; this test never embeds"
    return cache.records[digest]


# Definition index (design section 2.2, rules 1 to 3) ---------------------------------------------

TERMS = {  # every term of the index, by definition section (design section 10: listed by the build)
    "nta2025:s189": (
        "agricultural seeds and seedlings", "baby products", "basic food items", "educational books",
        "educational materials", "equipment", "exported goods",
        "farming machinery and implements and farming transportation equipment", "fertilisers",
        "humanitarian donor funded projects", "locally produced agricultural or veterinary medicines",
        "locally produced animal feeds", "machinery", "medical equipment", "medical products", "medical services",
        "pharmaceutical products", "plant", "purchase", "shared passenger road-transport service",
        "utilisation of gas in downstream petroleum operations", "water"),
    "nta2025:s202": (
        "accelerator", "accounting period", "ad valorem", "agency of Government", "aggregate covered tax paid",
        "agricultural trade or business", "angel investor", "approved Agent", "assessable income",
        "authorised officer", "bank", "banking", "beneficial owner", "borrower", "building", "chargeable gains",
        "commencement date", "commencement of business", "communication infrastructure", "company",
        "compensating payments", "connected persons", "constituent entity", "conveyance on sale", "digital assets",
        "disposal of assets", "economic development incentive certificate", "employment", "entertainment",
        "executor", "exported service", "family income", "finance lease", "financial institutions",
        "financial services", "foreign company", "goods", "government", "gross turnover", "hire purchase", "import",
        "importer", "income from investing activities", "income tax", "incubator", "individual", "instrument",
        "insurance", "investor in gas pipeline", "invoice", "itinerant worker", "labelled startup", "land", "lender",
        "manufacturer", "manufacturing", "minimum effective tax rate", "mining operations", "Minister", "MNE",
        "MNE group", "mortgage", "multinational enterprise", "multinational group", "National Minimum Wage",
        "Nigeria", "Nigerian company", "non-resident", "non-resident individual", "Official Gazette",
        "official market rate", "operating lease", "owner",
        "participation directly or indirectly in the management control or capital of the other person",
        "partnership", "permanent establishment", "permissible by-products", "person", "personal representatives",
        "policy of insurance", "policy of insurance against personal injury", "policy of life insurance",
        "policy of marine insurance", "priority business", "priority company", "priority industry",
        "priority product", "professional services", "public character", "public fund",
        "real estate investment company", "receipt", "regulated securities lending transaction", "related parties",
        "relative", "relevant priority product", "relevant tax authority", "remainder of assessable income",
        "remainder of assessable profit", "rent", "resident", "resident company", "resident individual",
        "royalties", "Service", "services", "small company", "supplies", "supply of goods", "supply of services",
        "tax", "tax ID", "taxable person", "taxable supplies", "trade or business", "transaction at arm s length",
        "vehicle", "venture capitalist", "vessel", "year of assessment"),
    "nta2025:sch1:pt1:para1": (
        "acquisition cost", "basis period", "concession", "lease", "mining operation",
        "qualifying capital expenditure", "trade or business"),
    "ntaa2025:s147": (
        "advance ruling", "agency of government", "agreement", "assessable income", "assessable profit",
        "authorised officer", "board", "book", "business", "calendar year", "commencement of business",
        "Commissioner", "company", "dispute", "distrain", "document", "electronic or digital activities",
        "employee of the tax authority", "employment", "entertainer, sportsperson, or competitor fees", "executor",
        "foreign company", "goods", "Government", "gross income", "High Court", "importer", "individual", "invoice",
        "manufacturer", "manufacturing", "Minister", "nigeria", "Nigerian company", "officer", "person", "President",
        "private dwelling", "registered person", "registration", "relevant tax authority", "Service", "settlement",
        "small business", "Special Purpose Tax Officer", "Stamp Duty", "supply", "tax", "tax agent", "Tax Authority",
        "tax return", "taxable goods and services", "taxable person", "taxpayer information",
        "transaction at arm s length"),
}


def test_index_has_204_terms_over_207_chunks(index):
    assert len(index.definitions) == 204
    assert sum(len(d.chunks) for d in index.definitions.values()) == 207
    by_source = {}
    for (source, _), d in sorted(index.definitions.items()):
        by_source.setdefault(source, []).append(d.term)
    assert {k: tuple(v) for k, v in by_source.items()} == TERMS


def test_multi_chunk_definitions(index):
    assert index.definitions[("nta2025:s189", "basic food items")].chunks == (
        "nta2025:s189:def:basic-food-items(a)-(k)", "nta2025:s189:def:basic-food-items(l)-(p)")
    assert len(index.definitions[("nta2025:sch1:pt1:para1", "qualifying capital expenditure")].chunks) == 3
    assert [len(d.chunks) for d in index.definitions.values()].count(1) == 202


def test_term_extraction():
    assert term_of("nta2025:s202:def:small-company", '"small company" means a company that ...') == "small company"
    assert term_of("ntaa2025:s147:def:service", "“Service” means the Nigeria Revenue Service") == "Service"
    assert term_of("nta2025:s202:def:x", '"royalties" includes any payment') == "royalties"
    assert term_of("nta2025:s202:def:arm-s-length", "no quoted term here") == "arm s length"  # the slug
    assert term_of("nta2025:s189:def:basic-food-items(l)-(p)", "(l) fruit;") == "basic food items"


def test_term_pattern_case_plurals_and_whole_words():
    small = term_pattern("small company")
    assert small.search("A Small Company is charged") and small.search("small companies are charged")
    assert not small.search("a smallcompany") and not small.search("non-small company-wide")
    service = term_pattern("Service")  # printed with a capital: case kept
    assert service.search("the Service may") and not service.search("a service supplied")
    assert term_pattern("royalty").search("royalties paid")
    assert term_pattern("tax").search("taxes") and not term_pattern("tax").search("taxable")


def _row(chunk_id, text, act="nta2025", chapter=None, part=None, schedule=None, schedule_part=None):
    return {"chunk_id": chunk_id, "act_id": act, "chapter_no": chapter, "part_no": part, "schedule_no": schedule,
            "schedule_part": schedule_part, "text": text}


SYNTHETIC = [
    _row("nta2025:s202:def:company", '"company" means a body corporate', chapter=9),
    _row("nta2025:s202:def:small-company", '"small company" means a company with low turnover', chapter=9),
    _row("nta2025:s202:def:service", '"Service" means the Revenue Service', chapter=9),
    _row("ntaa2025:s147:def:small-business", '"small business" means a business', act="ntaa2025"),
    _row("nta2025:s189:def:water", '"water" means natural water', chapter=8, part=4),
    _row("nta2025:sch1:pt1:para1:def:lease", '"lease" includes a sub-lease', schedule=1, schedule_part="I"),
    _row("nta2025:s56", "a small company, at 0%; water; a lease; a service; the Service; a small business",
         chapter=2, part=1),
    _row("nta2025:s146", "water supplied by a small company", chapter=6, part=2),
    _row("nta2025:sch1:pt1:para2-3", "a lease of plant by a company", schedule=1, schedule_part="I"),
    _row("ntaa2025:s2", "every small company and small business", act="ntaa2025"),
]


@pytest.fixture
def synthetic():
    return DefinitionIndex(SYNTHETIC, load_scopes())


def test_scopes_follow_the_statutes_words(synthetic):
    used = {cid: {term for _, term in synthetic.terms_used(cid)} for cid in
            ("nta2025:s56", "nta2025:s146", "nta2025:sch1:pt1:para2-3", "ntaa2025:s2")}
    # s56 (Chapter 2): section 202 terms only; not water (section 189: Chapter 6, Part IV of Chapter 8),
    # not lease (First Schedule Part I only), not small business (the Administration Act's)
    assert used["nta2025:s56"] == {"small company", "service"}
    assert used["nta2025:s146"] == {"water", "small company"}  # Chapter 6: section 189 applies
    assert used["nta2025:sch1:pt1:para2-3"] == {"lease", "company"}
    assert used["ntaa2025:s2"] == {"small business"}  # section 202 terms do not apply to the Administration Act


def test_longest_match_wins_and_order_of_first_use(synthetic):
    assert synthetic.terms_used("nta2025:s146") == [("nta2025:s189", "water"), ("nta2025:s202", "small company")]
    assert ("nta2025:s202", "company") not in synthetic.terms_used("nta2025:s56")  # inside "small company"
    assert synthetic.terms_used("nta2025:s202:def:company") == []  # a definition does not use its own term


def test_scope_policy_is_complete(index):
    scopes = load_scopes()
    assert [s.definitions for s in scopes] == ["nta2025:s202:def:", "ntaa2025:s147:def:", "nta2025:s189:def:",
                                               "nta2025:sch1:pt1:para1:def:"]
    with pytest.raises(ValueError, match="no scope"):
        DefinitionIndex([_row("nta2025:s99:def:x", '"x" means y')], scopes)


# Definition attachment rules (design section 2.2, rules 4 to 6) ------------------------------------


def _item(chunk_id, rank, reason="vector rank 1 (cosine distance 0.3000)", score=0.7):
    return Retrieved(chunk_id, rank, score, 1 - score, [reason])


def _offline_retriever(synthetic, distances, config=V3):
    r = Retriever(None, config)
    r._definition_index = synthetic
    r.distances = lambda vec, ids: {c: distances[c] for c in ids if c in distances}
    return r


DIST = {"nta2025:s202:def:small-company": 0.45, "nta2025:s202:def:service": 0.30, "nta2025:s189:def:water": 0.20,
        "nta2025:s202:def:company": 0.10}


def test_attachment_is_p1_first_then_nearest(synthetic):
    r = _offline_retriever(synthetic, DIST)
    sources = [_item("nta2025:s56", 1), _item("nta2025:s146", 2)]
    out = r.definition_attachment(sources, [0.0], {"nta2025:s56", "nta2025:s146"})
    # P1 uses small company (0.55) and Service (0.70): the nearest of P1's terms wins, though water
    # (P2) is nearer still
    assert [x.chunk_id for x in out] == ["nta2025:s202:def:service"]
    assert out[0].reasons[0].startswith("definition attachment: Service (similarity 0.700")


def test_attachment_floor_and_at_most_one(synthetic):
    far = dict(DIST, **{"nta2025:s202:def:service": 0.65, "nta2025:s202:def:small-company": 0.61})
    r = _offline_retriever(synthetic, far)
    out = r.definition_attachment([_item("nta2025:s56", 1), _item("nta2025:s146", 2)], [0.0], set())
    assert [x.chunk_id for x in out] == ["nta2025:s189:def:water"]  # P1's terms are below 0.40
    none = _offline_retriever(synthetic, {c: 0.61 for c in DIST})
    assert none.definition_attachment([_item("nta2025:s56", 1), _item("nta2025:s146", 2)], [0.0], set()) == []
    assert V3["definition_attachment"] == {"max": 1, "min_similarity": 0.40, "sources": 5}


def test_attachment_skipped_when_part_of_the_definition_is_shown(synthetic):
    r = _offline_retriever(synthetic, DIST)
    out = r.definition_attachment([_item("nta2025:s56", 1)], [0.0], {"nta2025:s56", "nta2025:s202:def:service"})
    assert [x.chunk_id for x in out] == ["nta2025:s202:def:small-company"]


def test_multi_chunk_definition_is_skipped_when_any_chunk_is_shown():
    rows = [dict(zip(("chunk_id", "act_id", "chapter_no", "part_no", "schedule_no", "schedule_part", "text"), v))
            for v in (("nta2025:s189:def:basic-food-items(a)-(k)", "nta2025", 8, 4, None, None,
                       '"basic food items" means (a) bread'),
                      ("nta2025:s189:def:basic-food-items(l)-(p)", "nta2025", 8, 4, None, None, "(l) fruit;"),
                      ("nta2025:s186", "nta2025", 8, 4, None, None, "basic food items are exempt"))]
    small = DefinitionIndex(rows, load_scopes())
    r = _offline_retriever(small, {"nta2025:s189:def:basic-food-items(a)-(k)": 0.3,
                                   "nta2025:s189:def:basic-food-items(l)-(p)": 0.2})
    assert [x.chunk_id for x in r.definition_attachment([_item("nta2025:s186", 1)], [0.0], set())] == [
        "nta2025:s189:def:basic-food-items(l)-(p)"]
    assert r.definition_attachment([_item("nta2025:s186", 1)], [0.0],
                                   {"nta2025:s189:def:basic-food-items(a)-(k)"}) == []


def test_options_off_rank_as_before():
    r = Retriever(None, V2)
    assert r.commencement is None and r.definition_max == 0 and r.policy_path is None


def test_policy_hash_is_pinned_in_config_v3(tmp_path):
    assert V3["retrieval_policy"] == {"path": "data/curated/retrieval_policy.yml",
                                      "sha256": hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()}
    bad = dict(V3, retrieval_policy={"path": "data/curated/retrieval_policy.yml", "sha256": "0" * 64})
    with pytest.raises(ValueError, match="does not match the sha256"):
        Retriever(None, bad)


# The real retrieval on tuning cases, by id (cached vectors; design sections 2.3, 3.3, 5) ----------


def _tuning_question(case_id):
    for name, part in (("v3", "holdout"), ("v2", "holdout")):
        for row in run_adversarial.load_adversarial(name, part):
            if row["id"] == case_id:
                return row["question"]
    raise KeyError(case_id)


@pytest.mark.parametrize("case_id, attached", [
    ("ADV3-043", [("nta2025:s203", "commencement lookup"),
                  ("nta2025:s202:def:year-of-assessment", "definition attachment: year of assessment")]),
    ("ADV3-044", [("nta2025:s202:def:small-company", "definition attachment: small company")]),
    ("ADV2-048", [("ntaa2025:s147:def:small-business", "definition attachment: small business")]),
])
def test_failed_controls_get_their_provisions(conn, case_id, attached):
    question = _tuning_question(case_id)
    vector = cached(question)
    before = [x.chunk_id for x in Retriever(conn, V2).retrieve(question, 5, query_vector=vector)]
    after = Retriever(conn, V3).retrieve(question, 5, query_vector=vector)
    assert [x.chunk_id for x in after[:6]] == before  # P1 to P6 never moved or replaced
    assert [(x.chunk_id, x.rank) for x in after[6:]] == [(c, 7 + i) for i, (c, _) in enumerate(attached)]
    assert all(x.reasons[0].startswith(reason) for x, (_, reason) in zip(after[6:], attached))
    assert len(after) <= 8


# Commencement lookup (design section 3.3) --------------------------------------------------------

COMMENCEMENT_PROBES = [  # (question, fires), the design's 28 in-sample probes
    ("When did the NTA kick in?", True),
    ("When does the Nigeria Tax Act come into force?", True),
    ("Is the NTA already in force?", True),
    ("From what date does the Nigeria Tax Act 2025 apply?", True),
    ("When did the new Nigeria Tax Act start?", True),
    ("What is the commencement date of the NTA?", True),
    ("Has the Nigeria Tax Act taken effect yet?", True),
    ("Since when has the NTA been operative?", True),
    ("When did the new tax act become law?", True),
    ("When does the Nigerian Tax Act begin to apply to companies?", True),
    ("Which year does the NTA first apply to?", True),
    ("When did the 2025 tax law go live?", True),
    ("The NTA started when exactly? I need the date for our board paper.", True),
    ("Did the NTA come into effect in 2025 or 2026?", True),
    ("When does a company's basis period start under the NTA?", False),
    ("What counts as the commencement date of a new business under the Nigeria Tax Act?", False),
    ("When must a small company file its return under the NTA?", False),
    ("From what date is VAT charged on a new supply?", False),
    ("When does the NTAA come into force?", False),
    ("When did the Tax Administration Act start?", False),
    ("When did CITA come into force?", False),
    ("When does the administration law start applying?", False),
    ("Does the NTA apply to a non-resident company?", False),
    ("When does the 0% rate start to apply to my company?", False),
    ("When does a VAT registration take effect?", False),
    ("When did the Finance Act 2023 come into force?", False),
    ("Under the NTA, when does an objection take effect?", True),  # the known false-positive class (kept)
    ("Which law applies to my company's 2025 profits, CITA or the NTA?", False),
]
RULE = load_commencement(OTHER_ACTS)


@pytest.mark.parametrize("question, fires", COMMENCEMENT_PROBES)
def test_commencement_probes(question, fires):
    assert (RULE.target(question) == "nta2025") is fires
    assert RULE.target(question) in (None, "nta2025")


def test_commencement_never_for_the_administration_act_or_other_acts():
    for q in ("When did the Nigeria Tax Administration Act come into force?", "When did the NTAA take effect?",
              "When did the administration Act come into force?", "When did PITA come into force?",
              "When did the Value Added Tax Act take effect?"):
        assert RULE.target(q) is None, q
    assert RULE.chunks == {"nta2025": "nta2025:s203"} and RULE.chunk_for("ntaa2025") is None


def test_commencement_attaches_only_when_not_shown():
    r = Retriever(None, V3)
    r.distances = lambda vec, ids: {c: 0.5 for c in ids}
    out = r.commencement_attachment("When did the NTA kick in?", [0.0], set())
    assert [x.chunk_id for x in out] == ["nta2025:s203"] and out[0].reasons[0].startswith(
        "commencement lookup: the question asks when the Nigeria Tax Act 2025 starts")
    assert r.commencement_attachment("When did the NTA kick in?", [0.0], {"nta2025:s203"}) == []
    assert r.commencement_attachment("What is the VAT rate?", [0.0], set()) == []


def test_commencement_fires_on_no_dev_question():
    dev = run_eval.load_questions("dev")
    assert len(dev) == 20 and not [q["id"] for q in dev if RULE.target(q["question"])]


# Passages, prompt and relevance floor (design section 4) ----------------------------------------


def test_prompt_has_eight_labels_and_gen_prompt_8():
    assert prompt.PROMPT_VERSION == "gen-prompt/8" and prompt.MAX_PASSAGES == 8
    assert prompt.LABELS == tuple(f"P{i}" for i in range(1, 9))
    assert prompt.SYSTEM_PROMPT.count("(P1 to P8)") == 1 and "(P1 to P6)" not in prompt.SYSTEM_PROMPT
    assert V3["passages"] == 8 and gen.RETRIEVAL_CONFIG == V3_PATH


def test_floor_never_reads_an_attachment():
    items = [_item("a", 1, "section lookup: Section 56, Nigeria Tax Act 2025 (cosine distance 0.5)", 0.5),
             _item("b", 2, "vector rank 1 (cosine distance 0.7000)", 0.30),
             _item("c", 3, "keyword slot: BM25 rank 1 (BM25 9.000, cosine distance 0.05)", 0.95),
             _item("d", 4, "commencement lookup: the question asks when the Nigeria Tax Act 2025 starts "
                           "(cosine distance 0.0500)", 0.95),
             _item("e", 5, "definition attachment: tax (similarity 0.990, cosine distance 0.0100; term used by "
                           "result 1)", 0.99)]
    assert [guardrails._is_vector_result(i) for i in items] == [False, True, False, False, False]
    floor = guardrails.relevance_floor(items[1:], 0.4)
    assert (floor.status, floor.top1) == ("below", 0.30)  # attachments with high scores do not lift it


def test_passages_carry_the_attachment_reason():
    p = gen.Passage("P7", "nta2025:s203", "c", "h", 7, 0.4, False, ["commencement lookup: x"])
    assert p.attachment == "commencement lookup: x"
    assert gen.Passage("P1", "x", "c", "h", 1, 0.4, False, ["vector rank 1"]).attachment is None


# Percent after a section number (D-101; design section 7(e)) --------------------------------------


@pytest.mark.parametrize("question, numbers", [
    ("Is the penalty under section 65, 10% of the unpaid tax, added to interest?", [65]),
    ("Under section 148, 7.5% is charged on supplies; does it apply to bread?", [148]),
    ("Does section 56, 30 per cent for other companies, apply to us?", [56]),
    ("Does section 56, 30 percent apply?", [56]),
    ("subject to section 67, 10% of the cost", [67]),
    ("Read sections 20 and 21 together.", [20, 21]),
    ("Read sections 20, 21 and 25.", [20, 21, 25]),
    ("Do sections 22 to 25 apply?", [22, 23, 24, 25]),
    ("Do sections 30 and 31% apply?", [30]),
])
def test_percent_is_not_a_section(question, numbers):
    assert [r.number for r in references.parse_references(question, OTHER_ACTS)] == numbers


def test_percent_rule_leaves_the_d21_reading_unchanged():
    assert [r.number for r in references.parse_section_refs_d21("section 56, 30% applies")] == [56, 30]
    assert [r.number for r in references.parse_section_refs_d21("sections 20 and 21")] == [20, 21]


def test_percent_rule_stops_the_wrong_lookup():
    from retrieval.retrieve import parse_section_refs
    assert [r.section for r in parse_section_refs("Under section 148, 7.5% applies", OTHER_ACTS)] == ["148"]


# norm/6 (D-102; design section 7(b)) ------------------------------------------------------------

TEXT = "(1) Tax is charged at the standard rate.\n(2) The supplier shall issue an invoice."


def test_norm6_version():
    assert verify.NORMALISER_VERSION == "norm/7"  # norm/7 (D-111) keeps norm/6's evidence rule


def test_norm6_recovers_trailing_punctuation():
    assert verify.evidence_found("charged at the standard rate;", TEXT)
    assert verify.evidence_found("; the supplier shall issue an invoice,", TEXT)
    assert verify.evidence_found("charged at the standard rate:", TEXT)


@pytest.mark.parametrize("quote", ["Tax is charged at the standard rate.", "the supplier shall issue an invoice.",
                                   "(2) The supplier shall", "Tax is charged", "standard rate.\n(2) The"])
def test_norm6_loses_none_of_a_fixed_set(quote):
    assert verify.evidence_found(quote, TEXT)


def test_norm6_still_needs_the_words():
    assert not verify.evidence_found("charged at a standard rate;", TEXT)  # a changed word stays a limitation
    assert not verify.evidence_found("rate;", TEXT)  # under 12 characters after trimming
    assert not verify.evidence_found(";;;;;;;;;;;;;;;;", TEXT)


# D-104 tuning record and the v4 entry (D-105, D-106) ---------------------------------------------


def test_d104_tuning_cases_are_217_with_54_in_scope():
    cases = run_adversarial.tune_cases("v1", ["tune", "holdout"], [("v2", ["holdout"]), ("v3", ["holdout"])])
    assert len(cases) == 217 and len({c["id"] for c in cases}) == 217
    assert sum(run_adversarial.is_in_scope(c) for c in cases) == 54
    assert {c.get("set") for c in cases if c["source"] == "adversarial"} == {"v1", "v2", "v3"}
    assert run_adversarial.parse_add(["v2:holdout", "v3:holdout"]) == [("v2", ["holdout"]), ("v3", ["holdout"])]


def test_d104_report_names_the_v3_controls_and_attachments():
    def row(id_, status, set_, shown=(), claims=None, passages=()):
        return {"id": id_, "source": "adversarial", "status": status, "expected": "answer_supported",
                "category": "C", "set": set_, "split": "holdout", "required_notes": [], "notes_shown": list(shown),
                "note_tagger": {"status": "ok", "labels": [], "reason": None},
                "claims": [{"no": i, "text": "x", "chunk_ids": ids} for i, ids in enumerate(claims or [], 1)],
                "verification": None if claims is None else {"claims": [{"verdict": "supported"} for _ in claims],
                                                             "gaps": []},
                "passages": list(passages)}
    p7 = {"label": "P7", "rank": 7, "chunk_id": "nta2025:s203", "score": 0.4, "attachment": "commencement lookup: x"}
    rows = [row("ADV3-043", "answered", "v3", claims=[["nta2025:s203"], ["nta2025:s22"]],
                passages=[{"label": "P1", "rank": 1, "chunk_id": "nta2025:s22", "score": 0.5}, p7]),
            row("ADV3-044", "not_covered", "v3"),
            row("ADV3-045", "answered", "v3", shown=["DQ-12"], claims=[["nta2025:s192"]]),
            row("ADV3-046", "answered", "v3", shown=["scope:surcharge"], claims=[["ntaa2025:s147"]])]
    report = run_adversarial.d84_report(rows)
    assert report["answered_every_claim_supported"] == {"ADV-081": None, "ADV-086": None, "ADV3-043": True,
                                                        "ADV3-044": False}
    v3 = report["d57"]["v3 controls"]
    assert (v3["questions"], v3["limit"], v3["within_limit"]) == (4, 1, False)
    assert report["reported"]["attachments"] == {"ADV3-043": {
        "passages": [{"label": "P7", "chunk_id": "nta2025:s203", "reason": "commencement lookup: x"}],
        "claims_citing": [1]}}
    text = run_adversarial.d84_markdown(report)
    assert "ADV3-044 NO" in text and "1 cases with an attachment, 1 passages; claims citing one: ADV3-043 claim 1" in text


V4_CONFIG_SHA256 = "d088e6cdde077f7d2b9cb85c2c380790148a998eeb50f02196461de12270a3b5"  # D-114, 2026-11-09
TUNE_RUNS_16_17 = [ROOT / "eval" / "guardrails" / "results" / f"tune_run{n}.json" for n in (16, 17)]


def test_v4_entry_is_pinned_and_frozen(tmp_path):
    """D-105, D-106, D-114: the v4 entry has the writer's sha256 and its frozen configuration sha256
    pinned, and no split file. The configuration was frozen once after tuning runs 16 and 17 met D-84 as
    extended by D-104 (D-109); it equals both runs' recorded configuration plus the set's version and
    sha256, and matches the live one. The held-out part stays unloadable until the decision run writes
    its marker. Only guards and the configuration file are read; the v4 questions file is never opened."""
    v4 = run_adversarial.SETS["v4"]
    assert (v4.questions.name, v4.questions_sha256, v4.split, v4.split_sha256) == \
        ("adversarial_v4.jsonl", V4_SHA256, None, None)
    assert v4.config == run_adversarial.HERE / "config_g2_v4.json" and v4.config_sha256 == V4_CONFIG_SHA256
    assert hashlib.sha256(v4.config.read_bytes()).hexdigest() == V4_CONFIG_SHA256
    assert v4.marker == run_adversarial.RESULTS_DIR / "v4" / "holdout_started.json" and not v4.marker.exists()
    assert v4.result == run_adversarial.RESULTS_DIR / "v4" / "holdout_final.json" and not v4.result.exists()
    assert v4.pack_prefix == "v4_holdout_repeat" and v4.config_records_set and v4.d57_control_limit == 1
    for call, message in ((lambda: run_adversarial.load_adversarial("v4", "holdout"), "has not been run"),
                          (lambda: run_adversarial.load_adversarial("v4", "tune"), "no split file"),
                          (lambda: run_adversarial.freeze_config("v4"), "frozen once")):
        with pytest.raises(run_adversarial.GuardError, match=message):
            call()
    frozen = json.loads(v4.config.read_text(encoding="utf-8"))
    assert frozen["adversarial_set"] == {"version": "v4", "sha256": V4_SHA256}
    for path in TUNE_RUNS_16_17:  # D-84: the passing pair's recorded configuration, field by field
        assert {k: v for k, v in frozen.items() if k != "adversarial_set"} == \
            json.loads(path.read_text(encoding="utf-8"))["config"]
    assert (frozen["generation"]["prompt_version"], frozen["normaliser"]) == ("gen-prompt/8", "norm/7")
    assert run_adversarial.check_holdout_guard("v4") == frozen  # live configuration matches the freeze
    assert run_adversarial.main(["--freeze-config", "--set", "v4"]) == 2
    copy = dataclasses.replace(v4, config=tmp_path / "config_g2_v4.json")  # a fresh freeze gives the same file
    assert run_adversarial.freeze_config(copy) == V4_CONFIG_SHA256


def test_live_configuration_records_the_retrieval_policy(monkeypatch, tmp_path):
    live = run_adversarial.current_config("v4")
    assert live["retrieval_policy_sha256"] == hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    assert live["retrieval_config_sha256"] == hashlib.sha256(V3_PATH.read_bytes()).hexdigest()
    assert (live["generation"]["prompt_version"], live["normaliser"]) == ("gen-prompt/8", "norm/7")
    other = tmp_path / "policy.yml"
    other.write_bytes(POLICY_PATH.read_bytes() + b"\n# changed\n")
    monkeypatch.setattr(run_adversarial, "RETRIEVAL_POLICY", other)
    assert run_adversarial.current_config("v4") != live  # a policy change is caught by a freeze


def test_v4_freeze_writes_once(tmp_path):
    copy = dataclasses.replace(run_adversarial.SETS["v4"], config=tmp_path / "config_g2_v4.json")
    sha = run_adversarial.freeze_config(copy)
    assert json.loads(copy.config.read_text(encoding="utf-8")) == run_adversarial.current_config("v4")
    assert hashlib.sha256(copy.config.read_bytes()).hexdigest() == sha
    with pytest.raises(run_adversarial.GuardError, match="frozen once"):
        run_adversarial.freeze_config(copy)
