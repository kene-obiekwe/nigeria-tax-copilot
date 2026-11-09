"""Retrieval layer and evaluation guard (Phase 4b): section-reference parsing, fusion order,
filters, the components against a throwaway load, the test-split guard, and that no test-split
question has reached a Phase 4b artefact. Database tests use deterministic fake vectors, so no
API key is needed. Assertion messages name question ids only, never question text."""
import hashlib
import importlib.util
import json
import sys

import pytest

from embed import clients, load
from retrieval import references, retrieve as rt
from retrieval.smoke_query import matches

ROOT = clients.ROOT
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "retrieval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
sys.modules["run_eval"] = run_eval
spec.loader.exec_module(run_eval)

CHUNKS = {c["chunk_id"]: c for c in load.read_chunks()}


class FakeClient:
    model = "test/fake-8"
    dims = 8
    usd_per_million_tokens = 0.0

    def __init__(self):
        self.tokens_used = 0
        self.requests = 0

    def _vector(self, text):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255 + 0.01 for b in digest[: self.dims]]

    def embed_documents(self, texts):
        self.requests += 1
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


@pytest.fixture
def loaded(db_schema, tmp_path):
    load.load(db_schema, FakeClient(), cache_dir=tmp_path / "cache", log=lambda *_: None)
    return db_schema


def retriever(conn, **config):
    return rt.Retriever(conn, {"model": FakeClient.model, **config}, FakeClient())


QUERY = FakeClient()._vector("query")


# Section reference parsing -------------------------------------------------------------------


@pytest.mark.parametrize("question, expected", [
    ("What is section 192 of the Nigeria Tax Act about?", [("192", "nta2025")]),
    ("Explain Section 66 of the Nigeria Tax Administration Act.", [("66", "ntaa2025")]),
    ("Compare section 20 of the NTA with s. 41(5) of the NTAA", [("20", "nta2025"), ("41", "ntaa2025")]),
    ("Under the Nigeria Tax Act, what do sections 20 and 21 say?", [("20", "nta2025"), ("21", "nta2025")]),
    ("sections 4, 5 or 9 of NTA", [("4", "nta2025"), ("5", "nta2025"), ("9", "nta2025")]),
    ("What does s148 say?", [("148", None)]),
    ("What does sec. 9 cover?", [("9", None)]),
    ("Is this section of the law relevant?", []),
    ("Revenue of 5 million in U.S. 5 dollars", []),
    ("What is the VAT rate?", []),
])
def test_parse_section_refs(question, expected):
    assert [(r.section, r.act_id) for r in rt.parse_section_refs(question)] == expected


def test_lowercase_nta_is_not_an_act_name():
    assert rt.parse_section_refs("section 9 of the nta") == [rt.SectionRef("9", None)]


# Fusion and filters (no database) --------------------------------------------------------------


def test_rrf_fuse_order_and_ties():
    fused = rt.rrf_fuse([["a", "b", "c"], ["c", "d"]], rrf_k=60)
    assert [chunk_id for chunk_id, _ in fused] == ["c", "a", "b", "d"]  # b and d tie: b is in list 1
    assert fused[0][1] == pytest.approx(1 / 63 + 1 / 61)


def test_filter_clause_rejects_unknown_keys_and_values():
    with pytest.raises(ValueError, match="unknown filter keys"):
        rt.filter_clause({"category": "vat"})
    with pytest.raises(ValueError, match="unknown act"):
        rt.filter_clause({"act": "cita"})
    with pytest.raises(ValueError, match="unknown tax_area"):
        rt.filter_clause({"tax_area": "pit"})
    assert rt.filter_clause(None) == ("TRUE", [])


# Components against a throwaway load --------------------------------------------------------


def test_plain_vector_search_matches_phase3_order(loaded):
    from retrieval.smoke_query import top_k

    got = retriever(loaded, section_lookup=False).retrieve("anything", k=10, query_vector=QUERY)
    assert [(r.chunk_id, round(r.distance, 6)) for r in got] == \
        [(c, round(d, 6)) for c, d in top_k(loaded, QUERY, FakeClient.model, 10)]
    assert [r.rank for r in got] == list(range(1, 11))
    assert all(r.reasons[0].startswith("vector rank") for r in got)


def test_named_section_comes_first_in_its_act(loaded):
    got = retriever(loaded).retrieve("What does section 17 of the Nigeria Tax Act say?", k=8,
                                     query_vector=QUERY)
    section17 = sorted(c for c in CHUNKS if c.startswith("nta2025:s17("))
    assert len(section17) == 5
    assert sorted(r.chunk_id for r in got[:5]) == section17
    assert all("section lookup: Section 17, Nigeria Tax Act 2025" in r.reasons[0] for r in got[:5])
    assert [r.distance for r in got[:5]] == sorted(r.distance for r in got[:5])
    assert not any(matches(r.chunk_id, "nta2025:s17") for r in got[5:])


def test_section_without_act_is_looked_up_in_both_acts(loaded):
    in_nta = [c for c, v in CHUNKS.items() if v["act_id"] == "nta2025" and v["section"] == "11"]
    in_ntaa = [c for c, v in CHUNKS.items() if v["act_id"] == "ntaa2025" and v["section"] == "11"]
    assert in_nta and in_ntaa
    n = len(in_nta) + len(in_ntaa)
    got = retriever(loaded).retrieve("What does section 11 say?", k=n + 2, query_vector=QUERY)
    assert sorted(r.chunk_id for r in got[:n]) == sorted(in_nta + in_ntaa)
    assert all("(no Act named)" in r.reasons[0] for r in got[:n])
    assert [r.distance for r in got[:n]] == sorted(r.distance for r in got[:n])


def test_section_outside_the_corpus_and_lookup_off_fall_back_to_vector(loaded):
    plain = retriever(loaded, section_lookup=False).retrieve("x", k=5, query_vector=QUERY)
    missing = retriever(loaded).retrieve("What does section 999 of the NTA say?", k=5, query_vector=QUERY)
    off = retriever(loaded, section_lookup=False).retrieve("section 17 of the NTA", k=5, query_vector=QUERY)
    assert [r.chunk_id for r in missing] == [r.chunk_id for r in plain] == [r.chunk_id for r in off]


@pytest.mark.parametrize("filters, check", [
    ({"act": "ntaa2025"}, lambda c: c["act_id"] == "ntaa2025"),
    ({"unit_type": "definition"}, lambda c: c["unit_type"] == "definition"),
    ({"act": "nta2025", "chapter": 6}, lambda c: c["act_id"] == "nta2025" and (c["chapter"] or {}).get("number") == "6"),
    ({"tax_area": "vat"}, lambda c: c["act_id"] == "nta2025" and (
        (c["chapter"] or {}).get("number") in ("6", "NINE")
        or ((c["chapter"] or {}).get("number") == "8" and c["part"]["number"] == "IV")
        or (c["schedule"] or {}).get("number") == "Eleventh")),
])
def test_filters_limit_every_component(loaded, filters, check):
    allowed = [c for c, v in CHUNKS.items() if check(v)]
    r = retriever(loaded, keyword=True)
    got = r.retrieve("section 148 and section 41 about value added tax", k=len(allowed) + 5,
                     filters=filters, query_vector=QUERY)
    assert got and all(check(CHUNKS[x.chunk_id]) for x in got)
    assert len(got) == len(allowed)


def test_keyword_index_and_fusion(loaded):
    index = rt.KeywordIndex(loaded)
    assert len(index.tf) == len(CHUNKS)
    rare = sorted(lexeme for lexeme, df in index.df.items() if df == 1 and lexeme.isalpha() and len(lexeme) > 6)[0]
    [(only, _)] = [(c, t) for c, t in index.tf.items() if rare in t]
    assert index.search(rare, 5)[0][0] == only
    fused = retriever(loaded, section_lookup=False, keyword=True).retrieve(rare, k=5, query_vector=QUERY)
    top = fused[0]
    assert top.chunk_id == only or any(x.chunk_id == only for x in fused)
    hit = next(x for x in fused if x.chunk_id == only)
    assert any(reason.startswith("keyword rank 1") for reason in hit.reasons)
    assert hit.distance is not None


# D-54 revision: shared parser, Act-name lookup, keyword slot (G2 fix design, section 2.5) --------

OTHER_ACTS = references.load_other_acts()
V2 = json.loads((ROOT / "eval" / "retrieval" / "config_v2.json").read_text(encoding="utf-8"))
NAMING_VAT_ACT = ["nta2025:s196", "nta2025:s198"]  # the corpus chunks that name the Value Added Tax Act


def test_config_v2_settings():
    assert {k: V2[k] for k in ("other_act_refs", "act_lookup", "keyword_slots", "passages")} == \
        {"other_act_refs": "skip", "act_lookup": {"max_chunks": 2}, "keyword_slots": 1, "passages": 6}
    final = json.loads(run_eval.FINAL_CONFIG.read_text(encoding="utf-8"))
    assert {k: v for k, v in V2.items() if k in final and k not in ("name", "based_on")} == \
        {k: v for k, v in final.items() if k not in ("name", "based_on")}


def test_reference_to_another_act_is_not_looked_up():
    question = "Is section 23 of the Companies Income Tax Act still in force?"
    assert references.parse_references(question, OTHER_ACTS) == [
        references.Reference("section", 23, "other:Companies Income Tax Act")]
    assert rt.parse_section_refs(question, OTHER_ACTS) == []
    assert rt.parse_section_refs(question) == [rt.SectionRef("23", None)]  # the D-21 reading, unchanged


def test_same_number_with_a_corpus_act_is_looked_up():
    question = "Is section 23 of the Nigeria Tax Act still in force?"
    assert rt.parse_section_refs(question, OTHER_ACTS) == [rt.SectionRef("23", "nta2025")]


def test_mixed_question_keeps_both_behaviours():
    question = "Does section 196 of the Nigeria Tax Act replace section 40 of CITA?"
    refs = references.parse_references(question, OTHER_ACTS)
    assert [(r.number, r.act) for r in refs] == [(196, "nta2025"), (40, "other:Companies Income Tax Act")]
    assert rt.parse_section_refs(question, OTHER_ACTS) == [rt.SectionRef("196", "nta2025")]


def test_guardrails_read_references_with_the_same_parser():
    from generation import guardrails

    policy = guardrails.load_guardrails_policy()
    for question in ("Is section 23 of the Companies Income Tax Act still in force?",
                     "Does section 196 of the Nigeria Tax Act replace section 40 of CITA?",
                     "What do sections 20 and 21 of the Tax Administration Act and the Third Schedule say?"):
        assert [(r.kind, r.number, r.act) for r in guardrails.parse_references(question, policy)] == \
            [(r.kind, r.number, r.act) for r in references.parse_references(question, OTHER_ACTS)]


def test_other_act_reference_skipped_in_retrieval(loaded):
    question = "What does section 17 of the Companies Income Tax Act say?"
    d21 = retriever(loaded).retrieve(question, k=5, query_vector=QUERY)
    assert all("section lookup: Section 17" in r.reasons[0] for r in d21)  # D-21 looked it up
    skip = retriever(loaded, other_act_refs="skip").retrieve(question, k=5, query_vector=QUERY)
    plain = retriever(loaded, section_lookup=False).retrieve(question, k=5, query_vector=QUERY)
    assert [r.chunk_id for r in skip] == [r.chunk_id for r in plain]
    assert not any(reason.startswith("section lookup") for r in skip for reason in r.reasons)


def test_act_name_lookup_nearest_first_and_capped(loaded):
    question = "Which law replaced the Value Added Tax Act?"
    got = retriever(loaded, other_act_refs="skip", act_lookup={"max_chunks": 2}).retrieve(
        question, k=5, query_vector=QUERY)
    head = got[:2]
    assert sorted(r.chunk_id for r in head) == NAMING_VAT_ACT
    assert [r.distance for r in head] == sorted(r.distance for r in head)
    assert all(r.reasons[0].startswith("act-name lookup: names the Value Added Tax Act") for r in head)
    assert len(got) == 5 and len({r.chunk_id for r in got}) == 5
    one = retriever(loaded, other_act_refs="skip", act_lookup={"max_chunks": 1}).retrieve(
        question, k=5, query_vector=QUERY)
    assert one[0].chunk_id == head[0].chunk_id and not one[1].reasons[0].startswith("act-name lookup")
    # Two outside Acts named: still at most 2 chunks in all.
    both = retriever(loaded, other_act_refs="skip", act_lookup={"max_chunks": 2}).retrieve(
        "Did the new law repeal the VAT Act and CITA?", k=5, query_vector=QUERY)
    assert sum(r.reasons[0].startswith("act-name lookup") for r in both) == 2
    # An Act that no corpus chunk names adds nothing.
    none = retriever(loaded, other_act_refs="skip", act_lookup={"max_chunks": 2}).retrieve(
        "What does the Finance Act say about levies?", k=5, query_vector=QUERY)
    assert not any(r.reasons[0].startswith("act-name lookup") for r in none)


def test_act_name_lookup_follows_the_section_lookup(loaded):
    question = "Does section 17 of the Nigeria Tax Act depend on section 9 of the VAT Act?"
    got = retriever(loaded, other_act_refs="skip", act_lookup={"max_chunks": 2}).retrieve(
        question, k=8, query_vector=QUERY)
    section17 = sorted(c for c in CHUNKS if c.startswith("nta2025:s17("))
    assert sorted(r.chunk_id for r in got[:5]) == section17
    assert sorted(r.chunk_id for r in got[5:7]) == NAMING_VAT_ACT
    assert not any("Section 9" in reason for r in got for reason in r.reasons)


def test_keyword_slot_is_appended_and_never_reorders(loaded):
    question = "registration of a taxable person for value added tax"
    first = retriever(loaded).retrieve(question, k=5, query_vector=QUERY)
    slotted = retriever(loaded, keyword_slots=1).retrieve(question, k=5, query_vector=QUERY)
    assert [(r.chunk_id, r.rank, r.reasons) for r in slotted[:5]] == [(r.chunk_id, r.rank, r.reasons) for r in first]
    assert len(slotted) == 6 and slotted[5].rank == 6
    slot = slotted[5]
    assert slot.chunk_id not in {r.chunk_id for r in first}
    assert slot.reasons[0].startswith("keyword slot: BM25 rank")
    best_unshown = next(c for c, _ in rt.KeywordIndex(loaded).search(question, 60) if c not in {r.chunk_id for r in first})
    assert slot.chunk_id == best_unshown
    two = retriever(loaded, keyword_slots=2).retrieve(question, k=5, query_vector=QUERY)
    assert len(two) == 7 and len({r.chunk_id for r in two}) == 7 and two[5].chunk_id == slot.chunk_id


@pytest.mark.parametrize("question", [
    "What does section 17 of the Nigeria Tax Act say?",
    "What does section 11 say?",
    "Is section 23 of the Companies Income Tax Act still in force?",
    "Which law replaced the Value Added Tax Act?",
    "value added tax on exported services",
])
def test_options_off_rank_exactly_as_d21(loaded, question):
    """With other_act_refs, act_lookup and keyword_slots off, ranking equals D-21 exactly."""
    d21 = retriever(loaded).retrieve(question, k=8, query_vector=QUERY)
    off = retriever(loaded, other_act_refs=None, act_lookup=None, keyword_slots=0).retrieve(
        question, k=8, query_vector=QUERY)
    assert [(r.chunk_id, r.score, r.reasons) for r in off] == [(r.chunk_id, r.score, r.reasons) for r in d21]


def test_first_five_unchanged_by_v2_when_no_other_act_is_named(loaded):
    for question in ("What does section 17 of the Nigeria Tax Act say?", "value added tax on exported services"):
        d21 = retriever(loaded).retrieve(question, k=5, query_vector=QUERY)
        v2 = rt.Retriever(loaded, {**V2, "model": FakeClient.model}, FakeClient()).retrieve(
            question, k=5, query_vector=QUERY)
        assert [r.chunk_id for r in v2[:5]] == [r.chunk_id for r in d21] and len(v2) == 6


def test_unknown_other_act_refs_value_is_refused(loaded):
    with pytest.raises(ValueError, match="other_act_refs"):
        retriever(loaded, other_act_refs="ignore")


# D-98 to D-100: config_v3.json, attachments after P6 (G2 fix design 3, sections 2 to 4) ----------

V3 = json.loads((ROOT / "eval" / "retrieval" / "config_v3.json").read_text(encoding="utf-8"))


def test_config_v3_is_config_v2_plus_the_attachments():
    new = {"commencement_lookup": True, "definition_attachment": {"max": 1, "min_similarity": 0.40, "sources": 5},
           "retrieval_policy": {"path": "data/curated/retrieval_policy.yml",
                                "sha256": hashlib.sha256((ROOT / "data" / "curated" / "retrieval_policy.yml")
                                                         .read_bytes()).hexdigest()}}
    assert (V3["name"], V3["based_on"], V3["passages"]) == ("v3", "v2", 8)
    assert {k: v for k, v in V3.items() if k in new} == new
    assert {k: v for k, v in V3.items() if k not in new and k not in ("name", "based_on", "passages")} == \
        {k: v for k, v in V2.items() if k not in ("name", "based_on", "passages")}


@pytest.mark.parametrize("question", [
    "What does section 56 of the Nigeria Tax Act say about a small company?",
    "When did the NTA kick in for company profits?",
    "Is a small business registered for VAT?",
    "value added tax on exported services",
])
def test_attachments_are_appended_and_never_reorder(loaded, question):
    v2 = rt.Retriever(loaded, {**V2, "model": FakeClient.model}, FakeClient()).retrieve(question, k=5, query_vector=QUERY)
    v3 = rt.Retriever(loaded, {**V3, "model": FakeClient.model}, FakeClient()).retrieve(question, k=5, query_vector=QUERY)
    assert [(r.chunk_id, r.rank, r.reasons) for r in v3[:6]] == [(r.chunk_id, r.rank, r.reasons) for r in v2]
    assert len(v3) <= 8 and [r.rank for r in v3] == list(range(1, len(v3) + 1))
    assert all(r.reasons[0].startswith(("commencement lookup", "definition attachment")) for r in v3[6:])
    assert len({r.chunk_id for r in v3}) == len(v3)


def test_commencement_chunk_is_attached_on_the_throwaway_load(loaded):
    v3 = rt.Retriever(loaded, {**V3, "model": FakeClient.model}, FakeClient()).retrieve(
        "When did the NTA kick in for company profits?", k=5, query_vector=QUERY)
    assert v3[6].chunk_id == "nta2025:s203" and v3[6].reasons[0].startswith("commencement lookup")


def test_config_v3_first_six_equal_config_v2_on_dev():
    """Design section 6 with the real code: on the 20 dev questions, against the project database with
    cached query vectors (no embedding), the first six passages are config_v2.json's."""
    from check_pgvector import connect
    cache = run_eval.QueryCache(V3["model"])
    key = clients.CLIENTS.get(cache.model)
    with connect() as conn:
        old, new = rt.Retriever(conn, V2), rt.Retriever(conn, V3)
        attached = 0
        for q in run_eval.load_questions("dev"):
            digest = cache.key(key, q["question"])
            assert digest in cache.records, q["id"]
            vector = cache.records[digest]
            a = [r.chunk_id for r in old.retrieve(q["question"], 5, query_vector=vector)]
            b = new.retrieve(q["question"], 5, query_vector=vector)
            assert [r.chunk_id for r in b[:6]] == a and len(a) == 6, q["id"]
            attached += len(b) > 6
    assert attached == 6  # design section 2.3: a definition on 6 of 20 dev questions, no commencement chunk


# Relevance floor under D-54 (section 2.5, item 2) -------------------------------------------------


def _item(chunk_id, score, *reasons):
    return rt.Retrieved(chunk_id, 0, score, 1 - score, list(reasons))


def test_floor_act_name_lookup_is_not_exempt():
    from generation.guardrails import relevance_floor

    items = [_item("nta2025:s196", 0.9, "act-name lookup: names the Companies Income Tax Act (cosine distance 0.1)"),
             _item("a", 0.35, "vector rank 1 (cosine distance 0.65)"),
             _item("b", 0.30, "vector rank 2 (cosine distance 0.70)")]
    floor = relevance_floor(items, 0.40)
    assert (floor.status, floor.top1) == ("below", 0.35)
    # An Act-name result that is also the top vector result is read through its vector rank.
    items[0].reasons.append("vector rank 1 (cosine distance 0.1)")
    assert relevance_floor(items, 0.40).status == "pass"


def test_floor_never_reads_the_keyword_slot():
    from generation.guardrails import relevance_floor

    items = [_item("a", 0.35, "vector rank 1 (cosine distance 0.65)"),
             _item("k", 0.95, "keyword slot: BM25 rank 1 (BM25 9.000, cosine distance 0.05)")]
    floor = relevance_floor(items, 0.40)
    assert (floor.status, floor.top1) == ("below", 0.35)


# Embedding clients ---------------------------------------------------------------------------


def test_bge_client_is_registered():
    cls = clients.CLIENTS["BAAI/bge-small-en-v1.5"]
    assert cls.dims == 384 and cls.max_tokens == 512 and len(cls.revision) == 40
    assert cls.query_prefix == "Represent this sentence for searching relevant passages: "


@pytest.mark.skipif(importlib.util.find_spec("sentence_transformers") is None,
                    reason="requirements-eval.txt is not installed")
def test_bge_client_embeds_normalised_vectors():
    client = clients.BgeSmallEmbeddingClient()
    vectors = client.embed_documents(["Value added tax", "Development levy"])
    assert [len(v) for v in vectors] == [384, 384]
    assert all(abs(sum(x * x for x in v) - 1) < 1e-4 for v in vectors)
    assert len(client.embed_query("What is the VAT rate?")) == 384
    with pytest.raises(clients.EmbeddingError, match="512-token limit"):
        client.embed_documents(["tax " * 600])


# Evaluation guard ----------------------------------------------------------------------------


def test_load_questions_keeps_one_split_only():
    dev = run_eval.load_questions("dev")
    assert len(dev) == 20 and {q["split"] for q in dev} == {"dev"}
    with pytest.raises(run_eval.GuardError, match="frozen sha256"):
        run_eval.load_questions("dev", expected_sha256="0" * 64)


def test_final_config_hash_is_recorded_and_matches():
    assert run_eval.FINAL_CONFIG_SHA256 == load.sha256_file(run_eval.FINAL_CONFIG)
    report = (ROOT / "docs" / "phase04_retrieval" / "retrieval_report.md").read_text(encoding="utf-8")
    assert run_eval.FINAL_CONFIG_SHA256 in report


def guard(tmp_path, **overrides):
    final = tmp_path / "config_final.json"
    final.write_text('{"name": "final"}\n', encoding="utf-8")
    args = {"final_config": final, "final_sha256": load.sha256_file(final),
            "test_result": tmp_path / "test_final.json"}
    args.update(overrides)
    return final, args


def test_guard_rules(tmp_path):
    final, args = guard(tmp_path)
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    assert run_eval.check_guard("dev", False, other, **args) == other
    assert run_eval.check_guard("test", True, None, **args) == final
    assert run_eval.check_guard("test", True, final, **args) == final
    with pytest.raises(run_eval.GuardError, match="needs --final"):
        run_eval.check_guard("test", False, other, **args)
    with pytest.raises(run_eval.GuardError, match="--config is required"):
        run_eval.check_guard("dev", False, None, **args)
    with pytest.raises(run_eval.GuardError, match="runs only"):
        run_eval.check_guard("test", True, other, **args)
    with pytest.raises(run_eval.GuardError, match="no frozen configuration hash"):
        run_eval.check_guard("test", True, None, **{**args, "final_sha256": None})
    final.write_text('{"name": "final", "keyword": true}\n', encoding="utf-8")
    with pytest.raises(run_eval.GuardError, match="does not match the recorded sha256"):
        run_eval.check_guard("test", True, None, **args)


def test_guard_refuses_a_second_test_run(tmp_path):
    _, args = guard(tmp_path)
    args["test_result"].write_text("{}", encoding="utf-8")
    with pytest.raises(run_eval.GuardError, match="already exists"):
        run_eval.check_guard("test", True, None, **args)
    assert run_eval.check_guard("dev", True, None, **args)  # dev reruns stay allowed


def test_refused_run_loads_and_embeds_nothing(tmp_path, monkeypatch):
    def fail(*_, **__):
        raise AssertionError("questions were loaded")
    monkeypatch.setattr(run_eval, "load_questions", fail)
    monkeypatch.setattr(run_eval, "QueryCache", fail)
    for split, final, config in (("test", False, run_eval.FINAL_CONFIG), ("test", True, tmp_path / "x.json")):
        with pytest.raises(run_eval.GuardError):
            run_eval.run(split, config, final=final, results_dir=tmp_path)
    assert run_eval.main(["--split", "test"]) == 2
    assert not list(tmp_path.glob("test_*.json"))


# No test-split question in a Phase 4b artefact ------------------------------------------------


def _test_questions():
    return run_eval.load_questions("test")


def _artefacts():
    paths = [run_eval.FINAL_CONFIG, ROOT / "eval" / "retrieval" / "run_eval.py",
             ROOT / "docs" / "phase04_retrieval" / "retrieval_report.md",
             ROOT / "requirements-eval.txt", ROOT / "python" / "embed" / "clients.py"]
    paths += sorted((ROOT / "eval" / "retrieval" / "configs").glob("*.json"))
    paths += sorted((ROOT / "eval" / "retrieval" / "results").glob("*.json"))
    paths += sorted((ROOT / "python" / "retrieval").glob("*.py"))
    return [p for p in paths if p.exists()]


def test_no_test_question_text_in_artefacts():
    questions = _test_questions()
    assert len(questions) == 30
    leaks = []
    for path in _artefacts():
        text = path.read_text(encoding="utf-8").lower()
        leaks += [(path.name, q["id"]) for q in questions if q["question"].lower() in text]
    assert not leaks, leaks
    for path in sorted((ROOT / "eval" / "retrieval" / "results").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert all("question" not in row for row in record["questions"]), path.name
        if path.name.startswith("dev_"):
            assert {row["id"] for row in record["questions"]} <= {q["id"] for q in run_eval.load_questions("dev")}


def test_no_test_question_embedded_before_the_final_run():
    if run_eval.TEST_RESULT.exists():
        pytest.skip("the final test run has been made; test questions are now in the query cache")
    questions = _test_questions()
    embedded = []
    for path in sorted(run_eval.QUERY_CACHE_DIR.glob("*.jsonl")):
        cache = run_eval.QueryCache(json.loads(path.read_text(encoding="utf-8").splitlines()[0])["model"])
        cls = clients.CLIENTS.get(cache.model)
        embedded += [(path.name, q["id"]) for q in questions if cache.key(cls, q["question"]) in cache.records]
    assert not embedded, embedded
