"""The calculation tool and its rate table (Phase 7; tools design, sections 4 and 11). Pure: no
database, no key, no model call. Questions here are the build session's own wording; no exit-check,
adversarial or test-split question is used."""
import ast
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from generation.verify import evidence_found, extract_tokens
from tools import taxcalc
from tools.taxcalc import INPUT_FIELDS, calculate, load_rates, naira, structural_problem

ROOT = Path(__file__).resolve().parents[1]
TABLE = load_rates()
CHUNKS = {json.loads(line)["chunk_id"]: json.loads(line)
          for line in (ROOT / "data" / "derived" / "chunks" / "v1_chunks.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip()}


def run(scenario, question, **inputs):
    call = dict.fromkeys(INPUT_FIELDS)
    call.update(inputs, scenario=scenario)
    assert structural_problem(call) is None
    return calculate("C1", scenario, call, question, TABLE)


# Rate table (D-120) -------------------------------------------------------------------------------


def test_rate_table_sha256_is_pinned():
    assert hashlib.sha256(taxcalc.RATES.read_bytes()).hexdigest() == taxcalc.RATES_SHA256 == TABLE.sha256


def test_changed_rate_table_is_refused(tmp_path):
    copy = tmp_path / "calc_rates.yml"
    copy.write_bytes(taxcalc.RATES.read_bytes().replace(b'value: "7.5"', b'value: "8"'))
    with pytest.raises(taxcalc.RatesError, match="does not match the pinned"):
        load_rates(copy)


@pytest.mark.parametrize("key", sorted(TABLE.constants))
def test_every_quote_is_in_its_chunk_and_carries_the_value(key):
    c = TABLE[key]
    chunk = CHUNKS[c.chunk_id]
    assert chunk["act_id"] == c.act_id and chunk["section"] == c.section
    assert chunk["text_sha256"] == c.chunk_text_sha256  # a changed corpus fails here
    assert hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest() == c.chunk_text_sha256
    assert evidence_found(c.quote, chunk["text"])  # copied from the chunk (norm/6)
    assert c.token in extract_tokens(c.quote)  # the quote's token equals the value
    assert c.token in extract_tokens(c.states)  # the text the model sees names the same figure


def test_every_constant_a_scenario_reads_exists():
    source = taxcalc.Path(taxcalc.__file__).read_text(encoding="utf-8")
    named = {node.args[0].value for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "const"
             and node.args and isinstance(node.args[0], ast.Constant)}
    named |= {f"s{n}_{part}_month" for n in (100, 101) for part in ("first", "later")}  # built from the provision
    named |= {"cit_rate_small", "cit_rate_other"}  # chosen by the small-company test
    assert named == set(TABLE.constants)  # every constant read exists, and no row is unused


def test_no_binary_floating_point_in_the_calculator():
    tree = ast.parse(Path(taxcalc.__file__).read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "float"]


# Scenarios on hand values ---------------------------------------------------------------------------


def test_vat_before_vat():
    c = run("vat_on_supply", "We sold taxable goods worth ₦1,200,000 before VAT. What VAT is charged?",
            amount="1200000", amount_basis="value_before_vat")
    assert (c.status, c.result, c.result_text) == ("computed", "90000.00", "VAT of ₦90,000")
    assert c.relies_on == ["vat_rate"] and c.sources == ["nta2025:s148"]
    assert c.input_matches == {"amount": "amt:1200000"} and taxcalc.ASSUME_STANDARD_RATE in c.assumptions
    assert [s.by for s in c.steps] == ["taxcalc.vat_on_supply"] * 2 and c.steps[-1].op == "round"


def test_vat_including_vat_rounds_once_at_the_end():
    c = run("vat_on_supply", "The customer paid ₦100,000 including VAT. How much is VAT?",
            amount="100000", amount_basis="consideration_including_vat")
    # 100,000 x 7.5 / 107.5 = 6,976.744186...
    assert (c.result, c.result_text) == ("6976.74", "VAT of ₦6,976.74")
    assert [s.op for s in c.steps] == ["add", "mul", "div", "round"] and c.steps[0].result == "107.5"
    assert c.sources == ["nta2025:s148", "nta2025:s149"]


def test_vat_without_a_basis_declines():
    c = run("vat_on_supply", "We sold goods for ₦1,200,000. What VAT applies?", amount="1200000")
    assert (c.status, c.declined_code, c.result, c.steps) == ("declined", "missing_input", None, [])
    assert "149(1)(a)" in c.declined_reason


def test_company_income_tax_not_small_by_turnover():
    c = run("company_income_tax", "Turnover was ₦75 million and total profits ₦10 million. What CIT is due?",
            amount="10000000", gross_turnover="75000000")
    assert (c.result, c.result_text) == ("3000000.00", "company income tax of ₦3,000,000")
    assert c.relies_on == ["small_turnover_max", "cit_rate_other"]
    assert {x["id"]: x["role"] for x in c.constants} == {"etr_turnover_min": "gate", "small_turnover_max": "test",
                                                         "cit_rate_other": "rate"}
    assert taxcalc.ASSUME_NOT_MNE in c.assumptions and taxcalc.ASSUME_TOTAL_PROFITS in c.assumptions


# The small-company truth table (section 202): turnover <= ₦50m, fixed assets <= ₦250m, no professional services.
SMALL_TABLE = [  # (turnover, fixed assets, professional services) -> small?
    ("40000000", "100000000", False, True),
    ("40000000", "100000000", True, False),
    ("40000000", "300000000", False, False),
    ("40000000", "300000000", True, False),
    ("60000000", "100000000", False, False),
    ("60000000", "100000000", True, False),
    ("60000000", "300000000", False, False),
    ("60000000", "300000000", True, False),
]


@pytest.mark.parametrize("turnover,assets,professional,small", SMALL_TABLE)
def test_small_company_truth_table(turnover, assets, professional, small):
    question = (f"A company with turnover of ₦{int(turnover):,}, fixed assets of ₦{int(assets):,} and total "
                "profits of ₦8,000,000. What company income tax applies?")
    c = run("company_income_tax", question, amount="8000000", gross_turnover=turnover, total_fixed_assets=assets,
            professional_services=professional)
    assert c.status == "computed"
    assert c.result == ("0.00" if small else "2400000.00")
    assert ("cit_rate_small" if small else "cit_rate_other") in c.relies_on


def test_small_company_needs_every_input_when_turnover_is_low():
    q = "Turnover was ₦20,000,000 and total profits ₦2,000,000, fixed assets ₦9,000,000. CIT?"
    assert run("company_income_tax", q, amount="2000000", gross_turnover="20000000").declined_code == "missing_input"
    c = run("company_income_tax", q, amount="2000000", gross_turnover="20000000", total_fixed_assets="9000000")
    assert c.declined_code == "missing_input" and "professional services" in c.declined_reason
    assert run("company_income_tax", q, amount="2000000", gross_turnover="20000000",
               professional_services=True).result == "600000.00"  # professional services settle it


def test_section_57_declines_at_twenty_billion_and_for_an_mne():
    q = "Turnover was ₦20,000,000,000 and total profits ₦3,000,000,000. What CIT applies?"
    c = run("company_income_tax", q, amount="3000000000", gross_turnover="20000000000")
    assert (c.status, c.declined_code) == ("declined", "not_covered") and "Section 57" in c.declined_reason
    below = run("company_income_tax", "Turnover ₦19,999,999,999 and total profits ₦3,000,000,000. CIT?",
                amount="3000000000", gross_turnover="19999999999")
    assert below.result == "900000000.00"
    mne = run("company_income_tax", "Turnover ₦900,000,000, total profits ₦30,000,000, MNE member. CIT?",
              amount="30000000", gross_turnover="900000000", mne_constituent=True)
    assert mne.declined_code == "not_covered"


def test_development_levy():
    q = "Our turnover was ₦400 million and assessable profits ₦60 million. What development levy applies?"
    c = run("development_levy", q, amount="60000000", gross_turnover="400000000")
    assert (c.result, c.result_text) == ("2400000.00", "development levy of ₦2,400,000")
    assert c.relies_on == ["dev_levy_rate", "small_turnover_max"] and taxcalc.ASSUME_RESIDENT in c.assumptions
    stated = run("development_levy", q, amount="60000000", gross_turnover="400000000", non_resident=False)
    assert taxcalc.ASSUME_RESIDENT not in stated.assumptions
    away = run("development_levy", q, amount="60000000", gross_turnover="400000000", non_resident=True)
    assert (away.status, away.result, away.result_kind) == ("computed", None, "not_charged")
    small = run("development_levy", "Turnover ₦30,000,000, fixed assets ₦5,000,000, assessable profits ₦4,000,000.",
                amount="4000000", gross_turnover="30000000", total_fixed_assets="5000000", professional_services=False)
    assert small.result_kind == "not_charged" and "small_fixed_assets_max" in small.relies_on


def test_late_payment_addition_leaves_interest_uncomputed():
    c = run("late_payment_addition", "We paid ₦3,000,000 of tax late. What is added?", amount="3000000")
    assert c.result == "300000.00" and c.not_computed == [taxcalc.NOT_COMPUTED_S65_INTEREST]


@pytest.mark.parametrize("provision,months,expected", [
    ("ntaa2025:s100", 1, "50000.00"), ("ntaa2025:s100", 3, "100000.00"),
    ("ntaa2025:s101", 1, "100000.00"), ("ntaa2025:s101", 6, "350000.00")])
def test_monthly_penalties(provision, months, expected):
    c = run("monthly_admin_penalty", f"The failure lasted {months} months. What penalty applies?",
            provision=provision, months=months)
    assert c.result == expected
    section = provision.split(":s")[1]
    assert c.relies_on == ([f"s{section}_first_month"] if months == 1
                           else [f"s{section}_first_month", f"s{section}_later_month"])


def test_monthly_penalty_months_must_be_stated_and_whole():
    assert run("monthly_admin_penalty", "It went on for three months.", provision="ntaa2025:s101",
               months=3).result == "200000.00"  # number words count (M3)
    assert run("monthly_admin_penalty", "It went on for 3 months.", provision="ntaa2025:s101",
               months=4).declined_code == "not_in_question"
    assert run("monthly_admin_penalty", "It went on for 0 months.", provision="ntaa2025:s101",
               months=0).declined_code == "bad_format"
    assert run("monthly_admin_penalty", "It went on for 3 months.", months=3).declined_code == "missing_input"


def test_fiscalisation_penalty():
    c = run("fiscalisation_penalty", "The tax due on the supplies was ₦150,000. What is the penalty?", amount="150000")
    assert c.result == "350000.00" and c.not_computed == [taxcalc.NOT_COMPUTED_S104_INTEREST]


def test_half_a_kobo_rounds_up():
    c = run("late_payment_addition", "Tax of ₦0.05 was paid late. What is added?", amount="0.05")
    assert c.steps[-2].result == "0.005" and c.result == "0.01"  # half-even would give 0.00
    assert run("late_payment_addition", "Tax of ₦0.25 was paid late.", amount="0.25").result == "0.03"


def test_naira_formatting():
    assert naira(Decimal("195000.00")) == "₦195,000" and naira(Decimal("34883.7209")) == "₦34,883.72"
    assert naira(Decimal("0")) == "₦0" and naira(Decimal("0.5")) == "₦0.50"


# Input checks I1 to I3 ------------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["4m", "-5", "1e6", "4,000,000", "4000000.505", " 4000000", "₦4000000"])
def test_i1_only_plain_digits(amount):
    c = run("late_payment_addition", "Tax of ₦4,000,000 was paid late.", amount=amount)
    assert c.declined_code == "bad_format"


@pytest.mark.parametrize("question,amount,token", [
    ("A sale of ₦7.5 million before VAT.", "7500000", "amt:7500000"),
    ("Goods worth ₦1.8 million, before VAT.", "1800000", "amt:1800000"),
    ("A sale of N250,000 before VAT.", "250000", "amt:250000"),
    ("A sale of 2,000,000 naira before VAT.", "2000000", "amt:2000000"),
])
def test_i2_figures_read_as_the_rule_check_reads_them(question, amount, token):
    c = run("vat_on_supply", question, amount=amount, amount_basis="value_before_vat")
    assert c.status == "computed" and c.input_matches["amount"] == token


def test_i2_a_figure_the_model_worked_out_is_declined():
    q = "We sold a building for ₦300 million that had cost ₦120 million. What tax is due on the gain?"
    c = run("company_income_tax", q, amount="180000000", gross_turnover="300000000")
    assert (c.declined_code, c.declined_reason) == ("not_in_question",
                                                    "the total profits 180000000 is not a figure stated in the question")


@pytest.mark.parametrize("question", [
    "We invoiced US$12,000 before VAT.", "The supply was worth 12,000 dollars before VAT.",
    "We were paid £12,000 before VAT.", "We were paid €12,000 before VAT.", "Paid 12,000 USD before VAT.",
    "The invoice was 12,000 euros before VAT."])
def test_i3_foreign_currency_declines(question):
    c = run("vat_on_supply", question, amount="12000", amount_basis="value_before_vat")
    assert c.declined_code == "foreign_currency"


def test_i3_does_not_fire_on_words_inside_other_words():
    q = "Compound interest aside, we sold goods of ₦12,000 before VAT."
    assert run("vat_on_supply", q, amount="12000", amount_basis="value_before_vat").status == "computed"


def test_structural_problems_are_tool_errors_not_declines():
    good = dict.fromkeys(INPUT_FIELDS, None) | {"scenario": "vat_on_supply"}
    assert structural_problem(good) is None
    assert "exactly the fields" in structural_problem({"scenario": "vat_on_supply"})
    assert "unknown scenario" in structural_problem(good | {"scenario": "chargeable_gains"})
    assert "string of digits" in structural_problem(good | {"amount": 4000000})
    assert "whole number" in structural_problem(good | {"months": 2.5})
    assert "whole number" in structural_problem(good | {"months": True})
    assert "amount_basis" in structural_problem(good | {"amount_basis": "net"})
    assert structural_problem([]) == "the input is not an object"


def test_computation_log_shape():
    c = run("vat_on_supply", "Taxable goods of ₦2,000 before VAT.", amount="2000", amount_basis="value_before_vat")
    log = c.as_log()
    assert log["code"] == {"calc_version": "calc/1", "rates_sha256": taxcalc.RATES_SHA256}
    assert log["rounding"] == "0.01 ROUND_HALF_UP" and log["result"] == "150.00"
    assert log["steps"][0] == {"op": "mul_pct", "a": "2000", "b": "7.5", "result": "150.0",
                               "by": "taxcalc.vat_on_supply"}
    assert json.loads(json.dumps(log)) == log


# D-129: the Section 149(1)(a) value rule, a text-only relies_on item ------------------------------


def test_vat_inclusive_relies_on_the_section_149_value_rule():
    c = run("vat_on_supply", "A buyer paid ₦500,000, VAT included, for a taxable supply. What is the VAT in it?",
            amount="500000", amount_basis="consideration_including_vat")
    assert (c.status, c.result) == ("computed", "34883.72")
    assert c.relies_on == ["vat_rate", "vat_inclusive_value"] and c.sources == ["nta2025:s148", "nta2025:s149"]
    rule = taxcalc.relied_item("vat_inclusive_value", TABLE)
    assert isinstance(rule, taxcalc.Rule) and rule.token is None and rule.chunk_id == "nta2025:s149"
    before = run("vat_on_supply", "We sold taxable goods worth ₦1,200,000 before VAT. What VAT is charged?", amount="1200000",
                 amount_basis="value_before_vat")
    assert before.relies_on == ["vat_rate"]  # the rule is added only for a VAT-inclusive amount


def test_rule_quotes_are_in_their_chunks_and_pinned():
    for rule in taxcalc.RULES.values():
        chunk = CHUNKS[rule.chunk_id]
        assert hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest() == rule.chunk_text_sha256
        assert rule.quote in " ".join(chunk["text"].split())
        assert rule.id not in TABLE.constants and rule.act_id == chunk["act_id"]
