"""The section lookup tool (Phase 7, D-118; tools design, sections 3 and 11).

The database tests read the loaded corpus and fail, not skip, when the database is down. They never
embed: the query vector is a stored chunk embedding read from the database, so "nearest to the
question" is nearest to that chunk, and no API is called."""
import json

import psycopg
import pytest

from check_pgvector import connect
from generation import generate as gen, guardrails
from generation.prompt import passage_block
from retrieval.retrieve import Retriever
from tools import lookup
from tools.lookup import input_problem, lookup_section

POLICY = guardrails.load_guardrails_policy()
LABELS = ["P9", "P10", "P11", "P12", "P13", "P14"]


@pytest.fixture(scope="module")
def db():
    try:
        conn = connect()
    except psycopg.OperationalError as exc:
        pytest.fail(f"local pgvector database unreachable on the configured port: {type(exc).__name__}")
    with conn:
        retriever = Retriever(conn, gen.retrieval_config())
        store = gen.DbCorpusStore(conn)
        yield {"conn": conn, "retriever": retriever, "store": store, "corpus": store.sections()}


def stored_vector(db, chunk_id):
    row = db["conn"].execute("SELECT embedding::text FROM chunk_embeddings WHERE chunk_id = %s AND model = %s",
                             (chunk_id, db["retriever"].model)).fetchone()
    assert row is not None, f"no stored embedding for {chunk_id}"
    return json.loads(row[0])


def run(db, act, number, subsection=None, *, near="nta2025:s202:def:small-company", shown=None, budget=3):
    return lookup_section({"act": act, "section_number": number, "subsection": subsection},
                          retriever=db["retriever"], store=db["store"], policy=POLICY, corpus=db["corpus"],
                          query_vector=stored_vector(db, near), shown=shown or {}, free_labels=list(LABELS),
                          chunk_budget=budget)


def test_one_chunk_section(db):
    r = run(db, "nta2025", "148")
    assert (r.is_error, r.verdict, r.added) == (False, "ok", [("P9", "nta2025:s148")])
    chunk = db["store"].chunks(["nta2025:s148"])["nta2025:s148"]
    assert r.content == ("Section 148 of the Nigeria Tax Act 2025: 1 chunk, returned below.\n"
                         + passage_block("P9", chunk))


def test_long_section_returns_the_three_nearest_with_a_count(db):
    r = run(db, "nta2025", "202")
    assert r.verdict == "ok" and len(r.added) == 3 and r.section_chunks == 120
    assert r.added[0] == ("P9", "nta2025:s202:def:small-company")  # the query vector is its own embedding
    assert r.content.startswith("Section 202 of the Nigeria Tax Act 2025 has 120 chunks in the corpus; 3 new "
                                "chunks are returned below, nearest to the question first.")
    assert [label for label, _ in r.added] == ["P9", "P10", "P11"]


def test_subsection_of_a_one_chunk_section(db):
    r = run(db, "nta2025", "59", "1", near="nta2025:s59")
    assert r.verdict == "ok" and r.added == [("P9", "nta2025:s59")]
    assert r.content.startswith("Section 59 of the Nigeria Tax Act 2025, subsection (1): 1 chunk")


def test_subsection_of_a_split_section_keeps_its_own_chunk(db):
    r = run(db, "nta2025", "17", "(4)", near="nta2025:s17(4)-(5)")
    assert r.verdict == "ok" and [c for _, c in r.added] == ["nta2025:s17(4)-(5)"]


def test_subsection_not_printed_gets_the_d81_text(db):
    r = run(db, "ntaa2025", "100", "4")
    assert (r.is_error, r.verdict) == (True, "no_such_subsection")
    assert r.content == ("As printed, section 100 of the Nigeria Tax Administration Act 2025 has subsections (1) "
                         "and (2) only; there is no subsection (4).")


def test_out_of_corpus_section_named_by_its_corrected_heading(db):
    r = run(db, "ntaa2025", "67")
    assert (r.is_error, r.verdict, r.added) == (True, "section_outside_v1", [])
    assert r.content == ('Section 67 of the Nigeria Tax Administration Act 2025 ("Recovery of tax") is outside '
                         "the corpus this tool covers (v1).")


def test_personal_income_tax_section_is_outside_the_corpus(db):
    r = run(db, "nta2025", "58")
    assert (r.is_error, r.verdict) == (True, "section_outside_v1")
    assert r.content == ('Section 58 of the Nigeria Tax Act 2025 ("Rates of tax for individuals") is outside the '
                         "corpus this tool covers (v1).")


@pytest.mark.parametrize("number,text", [
    ("57A", "The Nigeria Tax Act 2025 has no section 57A. Its sections are numbered 1 to 203, none with a letter."),
    ("204", "The Nigeria Tax Act 2025 has no section 204. It has 203 sections.")])
def test_no_such_section(db, number, text):
    r = run(db, "nta2025", number)
    assert (r.is_error, r.verdict, r.content) == (True, "no_such_section", text)


def test_a_chunk_already_shown_is_not_repeated(db):
    r = run(db, "nta2025", "148", shown={"nta2025:s148": "P3"})
    assert (r.is_error, r.verdict, r.added) == (False, "already_shown", [])
    assert r.content == "Section 148 of the Nigeria Tax Act 2025 is already among the passages as P3."
    part = run(db, "nta2025", "202", shown={"nta2025:s202:def:small-company": "P2"})
    assert "nta2025:s202:def:small-company" not in [c for _, c in part.added] and len(part.added) == 3
    assert part.content.startswith("Section 202 of the Nigeria Tax Act 2025 is already among the passages as P2.")


def test_the_chunk_budget_caps_new_chunks(db):
    assert len(run(db, "nta2025", "202", budget=1).added) == 1
    spent = run(db, "nta2025", "202", budget=0)
    assert (spent.is_error, spent.verdict, spent.content) == (True, "limit", lookup.PASSAGE_LIMIT_TEXT)


# Input checks (no database) ---------------------------------------------------------------------


@pytest.mark.parametrize("inputs,problem", [
    ({"act": "nta2025", "section_number": "148"}, "exactly the fields"),
    ({"act": "cita", "section_number": "148", "subsection": None}, "act must be"),
    ({"act": "nta2025", "section_number": "sixty", "subsection": None}, "section_number must be"),
    ({"act": "nta2025", "section_number": 148, "subsection": None}, "section_number must be"),
    ({"act": "nta2025", "section_number": "148", "subsection": "(a)"}, "subsection must be"),
])
def test_input_problems(inputs, problem):
    assert problem in input_problem(inputs)


def test_check_section_is_the_pre_check_code():
    """D-118: the refactor made `check_section` public; `pre_check` calls it, with no change."""
    ref = guardrails.Reference("section", 67, "ntaa2025")
    guardrails.check_section(ref, POLICY, {"nta2025": set(), "ntaa2025": set()})
    assert (ref.verdict, ref.text) == ("section_outside_v1", 'Section 67 of the Nigeria Tax Administration Act 2025 '
                                       '("Recovery of tax") is outside the corpus this tool covers (v1).')
    assert not hasattr(guardrails, "_check_section")
