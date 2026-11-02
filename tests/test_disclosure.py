"""Answer disclosure rule (D-23 to D-25): the pinned policy against the register, the decision for
every register row, and table-driven cases on real rows. No key and no database needed; the
register and chunks come from the committed files. Questions here are new wording, never
test-split questions."""
import pytest

from generation import disclosure as ds
from generation.generate import FileCorpusStore

STORE = FileCorpusStore()
REGISTER = {row["register_id"]: row for row in STORE.all_register_rows()}
POLICY = ds.load_policy()


def run(cited, question="What does this provision say?"):
    rows = STORE.register_rows(cited, sorted(POLICY.anchor_terms))
    citations = {c: STORE.chunks([c])[c]["citation"] for c in cited}
    return ds.disclose(cited, rows, question, POLICY, citations)


def by_ref(items):
    return {d.ref: d for d in items}


def unit(register_id):
    return REGISTER[register_id]["cited_unit"]


# The pinned policy file against the register ---------------------------------------------------


def test_policy_ids_exist_in_the_register():
    named = set(POLICY.always) | set(POLICY.cross_reference) | set(POLICY.merge) | set(POLICY.merge.values())
    named |= set(POLICY.display_text) | set(POLICY.anchor_terms)
    assert named <= set(REGISTER), sorted(named - set(REGISTER))


def test_cross_reference_list_is_every_cross_reference_row():
    by_note = {rid for rid, row in REGISTER.items() if (row["notes"] or "").startswith("Cross-reference")}
    assert all(REGISTER[rid]["type"] == "numbering" for rid in by_note)
    assert set(POLICY.cross_reference) == by_note | {"DR-008", "DR-014", "DR-199"}


def test_anchor_terms_cover_exactly_the_anchor_rows():
    anchors = {rid for rid, row in REGISTER.items() if row["cited_unit"] and ds.is_anchor(row)}
    assert anchors == set(POLICY.anchor_terms) == {"DR-095", "DR-137", "DR-262"}


def test_merge_pairs_share_a_chunk_and_display_text_fills_empty_notes():
    for child, parent in POLICY.merge.items():
        assert unit(child) == unit(parent), child
    for rid in POLICY.display_text:
        assert not (REGISTER[rid]["notes"] or "").strip(), rid


def test_decision_for_every_register_row_matches_the_pinned_counts():
    counts = {}
    for rid, row in REGISTER.items():
        if not row["cited_unit"]:
            key = "no cited unit"
        else:
            shown, reason = ds.decide(row, POLICY)
            key = "merged" if shown and rid in POLICY.merge else f"{'shown' if shown else 'hidden'}:{reason}"
        counts[key] = counts.get(key, 0) + 1
    assert counts == {"no cited unit": 1, "shown:always": 5, "shown:cross_reference": 18, "merged": 2,
                      "shown:type": 64, "hidden:wording": 181, "hidden:anchor": 3}
    assert sum(counts.values()) == len(REGISTER) == 274


def test_every_shown_row_has_display_text():
    for rid, row in REGISTER.items():
        if row["cited_unit"] and ds.decide(row, POLICY)[0]:
            assert ds.row_text(row, POLICY).strip(), rid


# Cases on real rows ----------------------------------------------------------------------------


@pytest.mark.parametrize("register_id,reason", [
    ("DR-002", "always"),       # wording row, DQ-14 printing slip
    ("DR-206", "always"),       # wording row, NRS print omits "a month"
    ("DR-141", "always"),       # substantive row, DQ-14
    ("DR-029", "always"),       # numbering and cross-reference row, DQ-14
    ("DR-050", "type"),         # missing_content
    ("DR-001", "type"),         # substantive
    ("DR-199", "cross_reference"),  # wording row on the cross-reference list
])
def test_rows_that_are_shown(register_id, reason):
    item = by_ref(run([unit(register_id)]))[register_id]
    assert (item.kind, item.reason) == ("register", reason)
    assert item.text.strip()


def test_plain_wording_row_is_hidden_but_recorded():
    assert REGISTER["DR-060"]["type"] == "wording"
    item = by_ref(run([unit("DR-060")]))["DR-060"]
    assert (item.kind, item.reason) == ("hidden", "wording")


def test_duplicate_cross_reference_rows_are_shown_once():
    items = run(["nta2025:s8"])
    refs = by_ref(items)
    assert refs["DR-007"].merged == ["DR-008"]
    assert "DR-008" not in refs
    for parent, child in (("DR-013", "DR-014"),):
        refs = by_ref(run([unit(parent)]))
        assert refs[parent].merged == [child] and child not in refs


def test_dr199_uses_the_policy_display_text():
    assert by_ref(run([unit("DR-199")]))["DR-199"].text == POLICY.display_text["DR-199"]


def test_anchor_row_is_hidden_with_its_chunk_unless_the_question_names_the_term():
    calendar = "ntaa2025:s147:def:calendar-year"
    items = by_ref(run([calendar], "Which day ends a calendar year under the Administration Act?"))
    assert (items["DR-262"].kind, items["DR-262"].reason) == ("hidden", "anchor")
    items = run([calendar], "Who is the Chief Executive Officer under the tax administration law?")
    assert [(d.kind, d.reason) for d in items if d.ref == "DR-262"] == [("note", "question")]
    items = run([], "Who is the chief executive officer of a tax authority?")
    assert by_ref(items)["DR-262"].kind == "note"


def test_dq16_note_when_section_191_is_cited():
    items = by_ref(run(["nta2025:s191"]))
    assert items["DQ-16"].kind == "note"
    assert items["DR-102"].kind == "hidden"  # wording row on the same chunk
    assert "DQ-16" not in by_ref(run(["nta2025:s190(2)-(7)"]))


@pytest.mark.parametrize("question,shown", [
    ("When did the Nigeria Tax Administration Act 2025 come into force?", True),
    ("When does the NTAA commence?", True),
    ("On what date did the new tax law take effect?", True),
    ("Is the Administration Act in force yet?", True),
    ("When does the Nigeria Tax Act 2025 come into effect?", False),
    ("What is the rate of VAT on services?", False),
])
def test_dq12_note_on_administration_act_commencement_questions(question, shown):
    assert ("DQ-12" in by_ref(run([], question))) is shown


def test_only_cited_chunks_contribute():
    cited = run(["nta2025:s56"])
    assert {d.ref for d in cited if d.kind != "note"} == {
        rid for rid, row in REGISTER.items() if row["cited_unit"] == "nta2025:s56"}
    assert run([]) == []


def test_order_follows_first_citation_then_register_id():
    items = [d for d in run(["nta2025:s56", "nta2025:s4(1)-(5)"]) if d.kind != "note"]
    units = [REGISTER[d.ref]["cited_unit"] for d in items]
    assert units == sorted(units, key=["nta2025:s56", "nta2025:s4(1)-(5)"].index)
    for u in set(units):
        ids = [d.ref for d in items if REGISTER[d.ref]["cited_unit"] == u]
        assert ids == sorted(ids)
