"""Spot-check sample (2b-7a): determinism, strata and constraints. The page pack is not built here."""
import pytest

from ingest import spot_check_sample as sc


@pytest.fixture(scope="module")
def chunks():
    return sc.load_chunks()


@pytest.fixture(scope="module")
def sample(chunks):
    return sc.draw_sample(chunks, sc.DEFAULT_SEED)


def ids(s):
    return [c["chunk_id"] for _, c in s]


def test_same_seed_same_ids(chunks, sample):
    assert ids(sc.draw_sample(chunks, sc.DEFAULT_SEED)) == ids(sample)


def test_different_seed_different_sample(chunks, sample):
    assert ids(sc.draw_sample(chunks, sc.DEFAULT_SEED + 1)) != ids(sample)


def test_order_of_the_chunk_file_does_not_matter(chunks, sample):
    assert ids(sc.draw_sample(list(reversed(chunks)), sc.DEFAULT_SEED)) == ids(sample)


def test_sampled_ids_exist_and_are_unique(chunks, sample):
    got = ids(sample)
    assert len(got) == 20 and len(set(got)) == 20
    assert set(got) <= {c["chunk_id"] for c in chunks}


@pytest.mark.parametrize("seed", [sc.DEFAULT_SEED, 1, 2, 3])
def test_constraints_hold(chunks, seed):
    s = sc.draw_sample(chunks, seed)
    for name in sc.STRATA:
        part = [c for st, c in s if st == name]
        assert len(part) == sc.SIZES[name]
        assert sum(c["discrepancy"]["flag"] for c in part) >= 2
        assert sum(c["unit_type"] == "definition" for c in part) <= sc.MAX_DEFINITIONS[name]
    nta = [c for st, c in s if st == "nta_sections"]
    ntaa = [c for st, c in s if st == "ntaa_sections"]
    sched = [c for st, c in s if st == "nta_schedules"]
    assert all(c["act_id"] == "nta2025" and not c["schedule"] for c in nta)
    assert all(c["act_id"] == "ntaa2025" for c in ntaa)
    assert sum(c["text_source"] in sc.VERIFIED_SOURCES for c in ntaa) >= 2
    assert all(c["act_id"] == "nta2025" and c["schedule"] for c in sched)
    assert any(c["unit_type"] == "schedule_table" for c in sched)


def test_impossible_constraint_stops_with_an_error(chunks):
    no_tables = [c for c in chunks if c["unit_type"] != "schedule_table"]
    with pytest.raises(sc.SampleError):
        sc.draw_sample(no_tables, sc.DEFAULT_SEED)


def test_blank_template_only_written_when_no_review_data(tmp_path, monkeypatch, sample):
    results = tmp_path / "results.csv"
    monkeypatch.setattr(sc, "RESULTS", results)
    assert sc.write_results(sample, sc.DEFAULT_SEED) is True       # missing: template written
    assert not sc.results_hold_review()
    assert sc.write_results(sample, sc.DEFAULT_SEED) is True       # blank: may be rewritten
    text = results.read_text(encoding="utf-8").replace("20261018,nta_sections,", "20261018,nta_sections,Pass,", 1)
    results.write_text(text, encoding="utf-8")
    assert sc.results_hold_review()
    assert sc.write_results(sample, sc.DEFAULT_SEED) is False      # review data: left alone
    assert results.read_text(encoding="utf-8") == text


def test_subsection_cross_ref_resolves_to_the_chunk_holding_it(chunks):
    ref = {"raw": "subsection (5)", "act_id": "nta2025", "target": "s27(5)"}
    assert "section 27(5) to (6)" in sc.resolve_ref(ref, chunks)


def test_recorded_results_meet_the_pass_rule(sample):
    import csv
    with sc.RESULTS.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["chunk_id"] for r in rows] == ids(sample)
    assert {r["seed"] for r in rows} == {str(sc.DEFAULT_SEED)}
    names = [c for c, _ in sc.CHECKS]
    for r in rows:
        assert all(r[k] in ("Pass", "Fail") for k in names)
        assert r["reviewer"].strip() and r["date"].strip()
    assert all(r[k] == "Pass" for r in rows for k in names[:2])
    assert sum(r[k] == "Fail" for r in rows for k in names[2:]) <= 2
