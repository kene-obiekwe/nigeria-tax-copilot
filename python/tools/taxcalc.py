"""The calculation tool (Phase 7, D-119 to D-122; tools design, section 4).

    from tools.taxcalc import calculate, load_rates
    computation = calculate("C1", "vat_on_supply", {"amount": "2600000", "amount_basis": "value_before_vat"},
                            question, load_rates())

Six scenarios, a closed list (design section 4.1): `vat_on_supply`, `company_income_tax`,
`development_levy`, `late_payment_addition`, `monthly_admin_penalty` and `fiscalisation_penalty`.
Anything else is not computed.

Where each figure comes from:

- **Inputs** come only from the question. Before any arithmetic, code checks I1 (an amount is plain
  digits with at most two decimal places), I2 (every amount and month count equals a figure that
  `verify.extract_tokens` reads in the question, the rule check's own normaliser) and I3 (no
  foreign-currency mark in the question). A failed check declines the calculation with its reason.
- **Rates and thresholds** come only from data/curated/calc_rates.yml (D-120), whose sha256 is
  pinned here (`RATES_SHA256`). The model never passes a rate.
- **Default assumptions** are a closed list (I5), each stated on the result. Every other missing
  input declines.

Arithmetic uses `decimal.Decimal` in a local context of 34 digits, parsed from digit strings and
never through binary floating point. The result is rounded once, at the end, to the kobo
(`ROUND_HALF_UP`); the corpus states no rounding rule. Every step is recorded with the function that
did it, so the log shows that code produced each number (`calc/1`).

This is a research and reference tool, not tax advice. A calculation shows how a stated rule applies
to the figures a question gives; it is not an assessment.
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from generation.verify import extract_tokens  # noqa: E402

CALC_VERSION = "calc/1"
RATES = ROOT / "data" / "curated" / "calc_rates.yml"
RATES_SHA256 = "111f3f6f5de526f60231fb52b721bf01f9cd8f97c5253a83b1a3ecbac992c700"  # D-120: data/curated/calc_rates.yml as built 2026-11-11
PRECISION = 34
KOBO = Decimal("0.01")
ROUNDING_TEXT = "0.01 ROUND_HALF_UP"
ROUNDED_LINE = "Rounded once, to the nearest kobo, half up."
NOT_ASSESSMENT_LINE = "This shows how the stated rule applies to the figures given. It is not an assessment."

SCENARIOS = ("vat_on_supply", "company_income_tax", "development_levy", "late_payment_addition",
             "monthly_admin_penalty", "fiscalisation_penalty")
AMOUNT_BASES = ("value_before_vat", "consideration_including_vat")
PROVISIONS = ("ntaa2025:s100", "ntaa2025:s101")
FIGURE_FIELDS = ("amount", "gross_turnover", "total_fixed_assets")  # I1 and I2 apply
FLAG_FIELDS = ("professional_services", "mne_constituent", "non_resident")
INPUT_FIELDS = ("scenario", "amount", "amount_basis", "gross_turnover", "total_fixed_assets",
                "professional_services", "mne_constituent", "non_resident", "provision", "months")
_DIGITS = re.compile(r"\d+(?:\.\d{1,2})?")

# I5: the closed list of default assumptions, each shown on the result it applies to.
ASSUME_RESIDENT = "the company is resident in Nigeria (the question does not say otherwise)"
ASSUME_NOT_MNE = "the company is not a constituent entity of an MNE group (the question does not say otherwise)"
ASSUME_STANDARD_RATE = ("the supply is a taxable supply at the standard rate; exemption (section 186) and zero "
                        "rating (section 187) were not checked")
ASSUME_TOTAL_PROFITS = "the amount given as profits is the company's total profits (section 56)"
ASSUME_ASSESSABLE_PROFITS = "the amount given as profits is the company's assessable profits (section 59)"
DEFAULT_ASSUMPTIONS = (ASSUME_RESIDENT, ASSUME_NOT_MNE, ASSUME_STANDARD_RATE, ASSUME_TOTAL_PROFITS,
                       ASSUME_ASSESSABLE_PROFITS)

NOT_COMPUTED_S65_INTEREST = "interest under section 65(1)(b), because its rate is not in the corpus"
NOT_COMPUTED_S104_INTEREST = ("interest at the Central Bank of Nigeria Monetary Policy Rate under section 104, "
                              "because its rate is not in the corpus")

ACT_NAMES = {"nta2025": "Nigeria Tax Act 2025", "ntaa2025": "Nigeria Tax Administration Act 2025"}


class RatesError(ValueError):
    """The rate table is missing, malformed or not the pinned file."""


# Rate table ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Constant:
    id: str
    value: str
    unit: str            # percent | naira
    act_id: str
    section: str
    chunk_id: str
    quote: str
    chunk_text_sha256: str
    states: str

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.value)

    @property
    def token(self) -> str:
        """The rule check's token for this constant ("pct:7.5", "amt:50000000")."""
        return f"{'pct' if self.unit == 'percent' else 'amt'}:{self.value}"

    @property
    def shown(self) -> str:
        return f"{self.value}%" if self.unit == "percent" else naira(self.decimal)


@dataclass(frozen=True)
class Rule:
    """A text-only `relies_on` item (D-129): a rule with no figure that a result rests on. K4 needs a
    kept claim that cites its chunk among its kept citations; there is no token to check. Kept in
    code, not in the rate table, because it holds no rate or threshold. `quote` is copied from the
    chunk (whitespace aside) and `chunk_text_sha256` pins the chunk text (a test)."""
    id: str
    act_id: str
    section: str
    chunk_id: str
    quote: str
    chunk_text_sha256: str
    states: str
    token = None


RULES: dict[str, Rule] = {
    "vat_inclusive_value": Rule(
        "vat_inclusive_value", "nta2025", "149(1)(a)", "nta2025:s149",
        "for a money consideration, its value shall be the amount which with the addition of the VAT chargeable "
        "is equal to the consideration",
        "9afa1aef9f021459a4bc2d8e5d069ad3e1edf539fdaa9321ea82c08f64b96b95",
        "for a supply for a money consideration, the value of the supply is the amount which, with the VAT "
        "chargeable added, equals the consideration"),
}


def relied_item(key: str, table: "RateTable") -> "Constant | Rule":
    """A `relies_on` item: a text rule (D-129) or a constant of the rate table."""
    return RULES[key] if key in RULES else table[key]


@dataclass(frozen=True)
class RateTable:
    constants: dict[str, Constant]
    currency_symbols: tuple[str, ...]
    currency_words: tuple[str, ...]
    version: str
    sha256: str
    path: Path

    def __getitem__(self, key: str) -> Constant:
        return self.constants[key]

    def currency_mark(self, text: str) -> str | None:
        """The first foreign-currency mark in `text` (I3), or None."""
        for symbol in self.currency_symbols:
            if symbol in text:
                return symbol
        for word in self.currency_words:
            plural = "s?" if word.isalpha() and word.islower() else ""
            m = re.search(rf"\b{re.escape(word)}{plural}\b", text, re.IGNORECASE)
            if m:
                return m.group(0)
        return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rates(path: Path = RATES, pinned: str | None = RATES_SHA256) -> RateTable:
    """The rate table. With `pinned` (the default), a file whose sha256 differs is refused, so an
    edited table never computes until the pin, its tests and the configuration are updated."""
    digest = sha256_file(path)
    if pinned is not None and digest != pinned:
        raise RatesError(f"{path.name} sha256 {digest} does not match the pinned {pinned}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("version") != CALC_VERSION:
        raise RatesError(f"{path.name} is not {CALC_VERSION}")
    constants: dict[str, Constant] = {}
    for row in data["constants"]:
        c = Constant(**{k: str(row[k]) for k in Constant.__dataclass_fields__})
        if c.unit not in ("percent", "naira") or c.id in constants:
            raise RatesError(f"bad or duplicate constant {c.id}")
        Decimal(c.value)  # must parse
        constants[c.id] = c
    marks = data["currency_marks"]
    return RateTable(constants, tuple(marks["symbols"]), tuple(marks["words"]), data["version"], digest, path)


# Formatting --------------------------------------------------------------------------------------


def naira(amount: Decimal) -> str:
    """₦ with thousands separators; ".00" dropped, kobo kept otherwise (design section 4.4)."""
    text = f"{amount.quantize(KOBO, rounding=ROUND_HALF_UP):,.2f}"
    return "₦" + (text[:-3] if text.endswith(".00") else text)


# Computation record ------------------------------------------------------------------------------


@dataclass
class Step:
    op: str
    a: str
    b: str
    result: str
    by: str

    def as_log(self) -> dict:
        return {"op": self.op, "a": self.a, "b": self.b, "result": self.result, "by": self.by}


@dataclass
class Computation:
    id: str
    scenario: str
    status: str = "declined"                 # computed | declined
    inputs: dict = field(default_factory=dict)
    input_matches: dict = field(default_factory=dict)
    constants: list[dict] = field(default_factory=list)   # every constant read, with its role
    relies_on: list[str] = field(default_factory=list)    # constant ids a kept claim must confirm (K4)
    steps: list[Step] = field(default_factory=list)
    result: str | None = None                # "195000.00"; None when declined or not charged
    result_kind: str | None = None           # amount | not_charged
    assumptions: list[str] = field(default_factory=list)
    not_computed: list[str] = field(default_factory=list)
    declined_code: str | None = None         # missing_input | bad_format | not_in_question | foreign_currency | not_covered
    declined_reason: str | None = None
    sources: list[str] = field(default_factory=list)      # chunk ids the result rests on
    what: str = ""
    from_question: list[str] = field(default_factory=list)
    working: list[str] = field(default_factory=list)
    result_text: str = ""
    rates_sha256: str = ""

    @property
    def computed(self) -> bool:
        return self.status == "computed"

    def as_log(self) -> dict:
        return {"id": self.id, "scenario": self.scenario, "status": self.status, "inputs": self.inputs,
                "input_matches": self.input_matches, "constants": self.constants, "relies_on": self.relies_on,
                "steps": [s.as_log() for s in self.steps], "rounding": ROUNDING_TEXT, "result": self.result,
                "result_kind": self.result_kind, "assumptions": self.assumptions, "not_computed": self.not_computed,
                "declined_code": self.declined_code, "declined_reason": self.declined_reason,
                "sources": self.sources, "what": self.what, "from_question": self.from_question,
                "working": self.working, "result_text": self.result_text,
                "code": {"calc_version": CALC_VERSION, "rates_sha256": self.rates_sha256}}


class _Decline(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


# Input checks (design section 4.2) -----------------------------------------------------------------


def structural_problem(inputs) -> str | None:
    """Why a tool input is not a well-formed calculate_liability call, or None. Strict tool use
    makes these impossible from the API; they are checked because a fake client can send anything.
    A problem here is an `is_error` result, not a decline."""
    if not isinstance(inputs, dict):
        return "the input is not an object"
    if set(inputs) != set(INPUT_FIELDS):
        return "the input must have exactly the fields " + ", ".join(INPUT_FIELDS)
    if inputs["scenario"] not in SCENARIOS:
        return f"unknown scenario {inputs['scenario']!r}; the scenarios are " + ", ".join(SCENARIOS)
    for name in FIGURE_FIELDS:
        if inputs[name] is not None and not isinstance(inputs[name], str):
            return f"{name} must be a string of digits or null"
    for name in FLAG_FIELDS:
        if inputs[name] is not None and not isinstance(inputs[name], bool):
            return f"{name} must be true, false or null"
    if inputs["amount_basis"] not in (*AMOUNT_BASES, None):
        return "amount_basis must be " + " or ".join(AMOUNT_BASES) + " or null"
    if inputs["provision"] not in (*PROVISIONS, None):
        return "provision must be " + " or ".join(PROVISIONS) + " or null"
    months = inputs["months"]
    if months is not None and (isinstance(months, bool) or not isinstance(months, int)):
        return "months must be a whole number or null"
    return None


def _token_value(token: str) -> Decimal | None:
    try:
        return Decimal(token.split(":", 1)[1])
    except (InvalidOperation, IndexError):
        return None


def _question_figures(question: str) -> list[tuple[str, Decimal]]:
    """(token, value) for every amt: and num: token of the question, amt: first (I2)."""
    tokens = extract_tokens(question)
    out = []
    for kind in ("amt", "num"):
        for token in sorted(t for t in tokens if t.startswith(kind + ":")):
            value = _token_value(token)
            if value is not None:
                out.append((token, value))
    return out


class _Inputs:
    """The checked inputs of one call: I1 and I2 per figure, I3 once."""

    def __init__(self, comp: Computation, inputs: dict, question: str, table: RateTable):
        self.comp, self.raw, self.figures = comp, inputs, _question_figures(question)
        mark = table.currency_mark(question)
        if mark is not None:
            raise _Decline("foreign_currency", f"the question carries a foreign currency mark ({mark}); only naira "
                                               "amounts are computed")

    def figure(self, name: str, label: str, required: bool = True) -> Decimal | None:
        raw = self.raw.get(name)
        if raw is None:
            if required:
                raise _Decline("missing_input", f"{label} is not stated in the question")
            return None
        if not _DIGITS.fullmatch(raw):
            raise _Decline("bad_format", f"{label} {raw!r} is not plain digits with at most two decimal places")
        value = Decimal(raw)
        match = next((token for token, v in self.figures if v == value), None)
        if match is None:
            raise _Decline("not_in_question", f"{label} {raw} is not a figure stated in the question")
        self.comp.inputs[name] = raw
        self.comp.input_matches[name] = match
        return value

    def months(self) -> int:
        months = self.raw.get("months")
        if months is None:
            raise _Decline("missing_input", "the number of months is not stated in the question")
        if months < 1:
            raise _Decline("bad_format", f"the number of months ({months}) is not a whole number of at least 1")
        match = next((token for token, v in self.figures if token.startswith("num:") and v == Decimal(months)), None)
        if match is None:
            raise _Decline("not_in_question", f"the number of months ({months}) is not a figure stated in the question")
        self.comp.inputs["months"] = months
        self.comp.input_matches["months"] = match
        return months

    def flag(self, name: str) -> bool | None:
        value = self.raw.get(name)
        if value is not None:
            self.comp.inputs[name] = value
        return value


# Arithmetic ---------------------------------------------------------------------------------------


class _Calc:
    def __init__(self, comp: Computation, table: RateTable, by: str):
        self.comp, self.table, self.by = comp, table, by

    def const(self, key: str, role: str = "rate") -> Decimal:
        c = self.table[key]
        if not any(item["id"] == key for item in self.comp.constants):
            self.comp.constants.append({"id": c.id, "value": c.value, "chunk_id": c.chunk_id, "role": role})
        if role in ("rate", "test") and key not in self.comp.relies_on:
            self.comp.relies_on.append(key)
        if role in ("rate", "test") and c.chunk_id not in self.comp.sources:
            self.comp.sources.append(c.chunk_id)
        return c.decimal

    def _step(self, op: str, a: Decimal, b: Decimal, result: Decimal) -> Decimal:
        self.comp.steps.append(Step(op, str(a), str(b), str(result), self.by))
        return result

    def mul_pct(self, a: Decimal, pct: Decimal) -> Decimal:
        return self._step("mul_pct", a, pct, a * pct / Decimal(100))

    def mul(self, a: Decimal, b: Decimal) -> Decimal:
        return self._step("mul", a, b, a * b)

    def div(self, a: Decimal, b: Decimal) -> Decimal:
        return self._step("div", a, b, a / b)

    def add(self, a: Decimal, b: Decimal) -> Decimal:
        return self._step("add", a, b, a + b)

    def sub(self, a: Decimal, b: Decimal) -> Decimal:
        return self._step("sub", a, b, a - b)

    def finish(self, value: Decimal, result_text: str) -> None:
        rounded = value.quantize(KOBO, rounding=ROUND_HALF_UP)
        self.comp.steps.append(Step("round", str(value), str(KOBO), str(rounded), self.by))
        self.comp.status, self.comp.result, self.comp.result_kind = "computed", str(rounded), "amount"
        self.comp.result_text = result_text.format(amount=naira(rounded))


def _section(act_id: str, sections: str) -> str:
    return f"{ACT_NAMES[act_id]}, {sections}"


# Scenarios ----------------------------------------------------------------------------------------


def vat_on_supply(comp: Computation, inp: _Inputs, table: RateTable) -> None:
    calc = _Calc(comp, table, "taxcalc.vat_on_supply")
    basis = inp.raw.get("amount_basis")
    amount = inp.figure("amount", "the amount of the supply")
    if basis is None:
        raise _Decline("missing_input", "the question does not say whether the amount includes VAT (section 149(1)(a))")
    comp.inputs["amount_basis"] = basis
    rate = calc.const("vat_rate")
    comp.assumptions.append(ASSUME_STANDARD_RATE)
    if basis == "value_before_vat":
        comp.what = f"VAT on a taxable supply ({_section('nta2025', 'section 148')})"
        comp.from_question.append(f"value of the supply before VAT, {naira(amount)}")
        vat = calc.mul_pct(amount, rate)
        comp.working.append(f"{naira(amount)} x {table['vat_rate'].shown} = {naira(vat)}")
    else:
        comp.what = (f"VAT included in the consideration for a taxable supply "
                     f"({_section('nta2025', 'sections 148 and 149(1)(a)')})")
        comp.from_question.append(f"consideration including VAT, {naira(amount)}")
        rule = RULES["vat_inclusive_value"]  # D-129: the value rule must be confirmed too (K4, citation only)
        comp.relies_on.append(rule.id)
        comp.sources.append(rule.chunk_id)
        gross = calc.add(Decimal(100), rate)
        vat = calc.div(calc.mul(amount, rate), gross)
        comp.working.append(f"{naira(amount)} x {table['vat_rate'].value} / {gross} = {naira(vat)}")
    calc.finish(vat, "VAT of {amount}")


def _small_company(comp: Computation, calc: _Calc, inp: _Inputs, turnover: Decimal) -> bool:
    """The small-company test of section 202: small only if turnover <= the limit, fixed assets <=
    the limit and no professional services; not small as soon as one of them fails."""
    turnover_max = calc.const("small_turnover_max", "test")
    if turnover > turnover_max:
        comp.working.append(f"Small-company test (section 202): gross turnover of {naira(turnover)} is more than "
                            f"{naira(turnover_max)}, so the company is not a small company")
        return False
    assets = inp.figure("total_fixed_assets", "total fixed assets", required=False)
    professional = inp.flag("professional_services")
    if assets is not None:
        assets_max = calc.const("small_fixed_assets_max", "test")
        if assets > assets_max:
            comp.working.append(f"Small-company test (section 202): total fixed assets of {naira(assets)} are more "
                                f"than {naira(assets_max)}, so the company is not a small company")
            return False
    if professional is True:
        comp.working.append("Small-company test (section 202): the business provides professional services, so the "
                            "company is not a small company")
        return False
    if assets is None:
        raise _Decline("missing_input", "total fixed assets are not stated, and gross turnover alone does not settle "
                                        "the small-company test (section 202)")
    if professional is None:
        raise _Decline("missing_input", "the question does not say whether the business provides professional "
                                        "services, which the small-company test needs (section 202)")
    comp.working.append(f"Small-company test (section 202): gross turnover of {naira(turnover)} is not more than "
                        f"{naira(turnover_max)}, total fixed assets of {naira(assets)} are not more than "
                        f"{naira(calc.table['small_fixed_assets_max'].decimal)}, and no professional services are "
                        "provided, so the company is a small company")
    return True


def company_income_tax(comp: Computation, inp: _Inputs, table: RateTable) -> None:
    calc = _Calc(comp, table, "taxcalc.company_income_tax")
    profits = inp.figure("amount", "the total profits")
    turnover = inp.figure("gross_turnover", "the gross turnover")
    mne = inp.flag("mne_constituent")
    if mne is True:
        raise _Decline("not_covered", "the company is a constituent entity of an MNE group, so the Section 57 minimum "
                                      "effective tax rate applies, which this tool does not compute")
    if turnover >= calc.const("etr_turnover_min", "gate"):
        raise _Decline("not_covered", "gross turnover of ₦20,000,000,000 or more brings in the Section 57 minimum "
                                      "effective tax rate, which this tool does not compute")
    small = _small_company(comp, calc, inp, turnover)
    comp.what = f"Company income tax ({_section('nta2025', 'sections 56 and 202')})"
    comp.from_question += [f"total profits, {naira(profits)}", f"gross turnover, {naira(turnover)}"]
    if "total_fixed_assets" in comp.inputs:
        comp.from_question.append(f"total fixed assets, {naira(Decimal(comp.inputs['total_fixed_assets']))}")
    if mne is None:
        comp.assumptions.append(ASSUME_NOT_MNE)
    comp.assumptions.append(ASSUME_TOTAL_PROFITS)
    key = "cit_rate_small" if small else "cit_rate_other"
    tax = calc.mul_pct(profits, calc.const(key))
    comp.working.append(f"{naira(profits)} x {table[key].shown} = {naira(tax)}")
    calc.finish(tax, "company income tax of {amount}")


def development_levy(comp: Computation, inp: _Inputs, table: RateTable) -> None:
    calc = _Calc(comp, table, "taxcalc.development_levy")
    profits = inp.figure("amount", "the assessable profits")
    turnover = inp.figure("gross_turnover", "the gross turnover")
    non_resident = inp.flag("non_resident")
    comp.what = f"Development levy ({_section('nta2025', 'sections 59 and 202')})"
    comp.from_question += [f"assessable profits, {naira(profits)}", f"gross turnover, {naira(turnover)}"]
    rate = calc.const("dev_levy_rate")
    if non_resident is True:
        comp.working.append("Section 59(1): the levy is not imposed on non-resident companies")
        _not_charged(comp, calc)
        return
    if non_resident is None:
        comp.assumptions.append(ASSUME_RESIDENT)
    small = _small_company(comp, calc, inp, turnover)
    if "total_fixed_assets" in comp.inputs:
        comp.from_question.append(f"total fixed assets, {naira(Decimal(comp.inputs['total_fixed_assets']))}")
    comp.assumptions.append(ASSUME_ASSESSABLE_PROFITS)
    if small:
        comp.working.append("Section 59(1): the levy is not imposed on small companies")
        _not_charged(comp, calc)
        return
    levy = calc.mul_pct(profits, rate)
    comp.working.append(f"{naira(profits)} x {table['dev_levy_rate'].shown} = {naira(levy)}")
    calc.finish(levy, "development levy of {amount}")


def _not_charged(comp: Computation, calc: _Calc) -> None:
    comp.steps.append(Step("not_charged", "", "", "", calc.by))
    comp.status, comp.result, comp.result_kind = "computed", None, "not_charged"
    comp.result_text = "the development levy is not charged (section 59(1))"


def late_payment_addition(comp: Computation, inp: _Inputs, table: RateTable) -> None:
    calc = _Calc(comp, table, "taxcalc.late_payment_addition")
    amount = inp.figure("amount", "the amount of tax not paid in time")
    comp.what = f"Sum added to tax not paid in time ({_section('ntaa2025', 'section 65(1)(a)')})"
    comp.from_question.append(f"tax not paid in time, {naira(amount)}")
    rate = calc.const("late_payment_addition_rate")
    added = calc.mul_pct(amount, rate)
    comp.working.append(f"{naira(amount)} x {table['late_payment_addition_rate'].shown} = {naira(added)}")
    comp.not_computed.append(NOT_COMPUTED_S65_INTEREST)
    calc.finish(added, "a sum of {amount} added to the tax")


def monthly_admin_penalty(comp: Computation, inp: _Inputs, table: RateTable) -> None:
    calc = _Calc(comp, table, "taxcalc.monthly_admin_penalty")
    provision = inp.raw.get("provision")
    if provision is None:
        raise _Decline("missing_input", "the provision (section 100 or section 101) is not given")
    months = inp.months()
    comp.inputs["provision"] = provision
    section = provision.split(":s", 1)[1]
    failure = "failing to register" if section == "100" else "failing to file returns"
    comp.what = f"Administrative penalty for {failure} ({_section('ntaa2025', f'section {section}')})"
    comp.from_question.append(f"months of failure, {months}")
    first = calc.const(f"s{section}_first_month")
    if months == 1:  # the first month only
        total = first
        comp.working.append(f"{naira(first)} for the first month = {naira(total)}")
    else:
        later = calc.const(f"s{section}_later_month")
        extra = calc.sub(Decimal(months), Decimal(1))
        total = calc.add(first, calc.mul(later, extra))
        comp.working.append(f"{naira(first)} for the first month + {naira(later)} x {extra} later months "
                            f"= {naira(total)}")
    calc.finish(total, "a total administrative penalty of {amount}")


def fiscalisation_penalty(comp: Computation, inp: _Inputs, table: RateTable) -> None:
    calc = _Calc(comp, table, "taxcalc.fiscalisation_penalty")
    amount = inp.figure("amount", "the tax due on the supplies")
    comp.what = f"Administrative penalty for failing to use the fiscalisation system ({_section('ntaa2025', 'section 104')})"
    comp.from_question.append(f"tax due on the supplies, {naira(amount)}")
    fixed = calc.const("s104_fixed")
    part = calc.mul_pct(amount, calc.const("s104_rate"))
    total = calc.add(fixed, part)
    comp.working.append(f"{naira(fixed)} + {naira(amount)} x {table['s104_rate'].shown} = {naira(total)}")
    comp.not_computed.append(NOT_COMPUTED_S104_INTEREST)
    calc.finish(total, "an administrative penalty of {amount}")


SCENARIO_FUNCTIONS = {"vat_on_supply": vat_on_supply, "company_income_tax": company_income_tax,
                      "development_levy": development_levy, "late_payment_addition": late_payment_addition,
                      "monthly_admin_penalty": monthly_admin_penalty, "fiscalisation_penalty": fiscalisation_penalty}


def calculate(call_id: str, scenario: str, inputs: dict, question: str, table: RateTable) -> Computation:
    """Run one scenario. Never raises for a decline: the record says `declined` with its reason, and
    nothing is computed. `inputs` must already pass `structural_problem`."""
    comp = Computation(call_id, scenario, rates_sha256=table.sha256)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        try:
            SCENARIO_FUNCTIONS[scenario](comp, _Inputs(comp, inputs, question, table), table)
        except _Decline as decline:
            comp.status, comp.declined_code, comp.declined_reason = "declined", decline.code, decline.reason
            comp.result = comp.result_kind = None
            comp.steps, comp.working, comp.result_text = [], [], ""
    return comp
