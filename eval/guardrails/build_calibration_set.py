"""Build the verifier calibration set (D-36; guardrails design, section 3.8).

    .venv\\Scripts\\python eval\\guardrails\\build_calibration_set.py

Writes eval/guardrails/verifier_calibration_v1.jsonl, one object per claim, and prints its
sha256. It refuses to overwrite the file: the set is frozen before the first verifier run, and the
overseer's review of the mutations is recorded in a separate file
(verifier_calibration_v1_review.json), so the frozen file never changes.

- **Real claims (135):** every claim of Phase 5 exit-check runs 1 and 2 (10 dev questions by id,
  G01 to G05), with the human verdict from docs/phase05_generation/exit_check_results.csv and the
  passages it cited, under the labels it was shown with.
- **Mutated claims (60):** 10 of each type, made from human-supported claims with a fixed seed. No
  base claim is used twice. Types: figure changed; hardening ("may" to "must", "only" added);
  bridging fact added; subject swapped (another country, tax or taxpayer); negation flipped;
  citation moved to a passage retrieved for the same question that no claim of that answer cited.
  Every mutation is expected to be not supported, and the overseer checks each one before the
  first run, because a mutation can stay true by accident.
- **Overrides (overseer review, 2026-11-03):** after the seeded build, the slots in OVERRIDES are
  replaced by hand-written mutations of the same type, each checked against its cited chunk text.
  Four replace mutations the overseer found could stay true; five make bridging mutations
  realistic (a plausible fact linking the claim to a situation, which the cited chunk does not
  state, as in run 1 Q018). Every other slot is unchanged.

Only Phase 5 data allowed under D-29 is used; no test-split question.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

OUT = HERE / "verifier_calibration_v1.jsonl"
RESULTS_CSV = ROOT / "docs" / "phase05_generation" / "exit_check_results.csv"
RUNS = {1: ROOT / "eval" / "generation" / "results" / "exit_check_run1.json",
        2: ROOT / "eval" / "generation" / "results" / "exit_check_run2.json"}
SEED = 20261103
PER_TYPE = 10
HUMAN = {"supported": "supported", "unsupported": "unsupported", "not a factual claim": "not_a_claim"}

# Bridging facts: each links a passage to a fact the corpus passages do not state (design 3.3).
BRIDGES = (
    "This includes software licences sold by foreign companies.",
    "Bottled water and soft drinks fall within this rule.",
    "Mobile phones and laptops are treated the same way.",
    "This covers income from cryptocurrency trading.",
    "Rent paid on a home office counts for this purpose.",
    "Farm produce sold at local markets is included.",
    "Payments to a company's directors are covered by the same rule.",
    "This also applies to a sole trader's household expenses.",
    "Donations to religious bodies count for this purpose.",
    "This includes services supplied by ride-hailing drivers.",
)
# Hand-written replacements by slot: (base claim id, mutated text). Checked against the cited
# chunk: hardening-03, -09 and -10 and subject_swap-05 could stay true (overseer review); the
# bridging slots now state a realistic linking fact the cited chunk does not contain.
OVERRIDES = {
    # s151(6): "may appoint a representative".
    "CAL-M-hardening-03": ("CAL-R2-Q015-8", "A non-resident person that makes a taxable supply to a person in "
                           "Nigeria must appoint a representative for the purpose of compliance with its VAT "
                           "obligations."),
    # s192(3): "may make rules or regulations".
    "CAL-M-hardening-09": ("CAL-R2-Q047-5", "The relevant tax authority must make rules or regulations for the "
                           "administration of the section."),
    # NTAA s41(3): "may, for any satisfactory and good cause shown, extend the time".
    "CAL-M-hardening-10": ("CAL-R1-G05-5", "The relevant tax authority must extend that time for satisfactory and "
                           "good cause shown."),
    # s8(2): "Dividends received by a Nigerian company".
    "CAL-M-subject_swap-05": ("CAL-R1-G03-5", "A company must include dividend income in its profits, gross of any "
                              "tax paid or deducted at source. Dividends a Ghanaian company receives by way of shares "
                              "of the paying company are not included in its profits chargeable to tax."),
    # s145 says nothing about what a taxable supply is.
    "CAL-M-bridging-03": ("CAL-R1-Q018-1", "VAT is paid on all taxable supplies in Nigeria, subject to the exemptions "
                          "in Part IV of Chapter Eight of the Act. Bottled drinking water sold by a shop is a taxable "
                          "supply for this purpose."),
    # s20(1) lists no advertising or sponsorship expense; s21 does not mention it either.
    "CAL-M-bridging-05": ("CAL-R1-Q007-2", "The general rule allows deduction of expenses wholly and exclusively "
                          "incurred in producing the income, but section 21 separately lists items for which no "
                          "deduction is allowed, and fines are on that list. A sponsorship fee paid to a football "
                          "club for stadium advertising is an expense wholly and exclusively incurred in producing "
                          "the income."),
    # s9(2)(b) does not say which profits are exempt.
    "CAL-M-bridging-06": ("CAL-R1-Q004-5", "It also does not apply to dividends declared out of profits or gains that "
                          "are exempt from income tax by the Act, or to franked investment income as provided in the "
                          "Act. Profits from a company's agricultural business in its first five years of operation "
                          "are exempt profits for this purpose."),
    # s155(1) names governments and persons appointed by the Service, collecting on supplies made to them.
    "CAL-M-bridging-08": ("CAL-R2-Q015-6", "VAT collected, withheld or self-accounted under section 155 is to be "
                          "remitted to the Service on or before the 14th day of the month immediately following the "
                          "month of the transaction, or as may be prescribed by the Service. A supermarket that adds "
                          "VAT to its customers' receipts is one of the persons who collect VAT under section 155."),
    # s151(3)-(4) do not say who has been appointed.
    "CAL-M-bridging-10": ("CAL-R1-Q015-4", "If such an appointed person has made the supply to a taxable person in "
                          "Nigeria, the Nigerian taxable person has no obligation to withhold the VAT, unless the "
                          "appointed person has failed to collect it. A foreign streaming service that sells "
                          "subscriptions to Nigerian customers is such an appointed person."),
}
SWAPS = (  # (pattern, replacement): first that matches is used
    (r"\bNigerian company\b", "Ghanaian company"), (r"\bNigerian companies\b", "Ghanaian companies"),
    (r"\bin Nigeria\b", "in Ghana"), (r"\bvalue added tax\b", "excise duty"), (r"\bVAT\b", "excise duty"),
    (r"\bcompanies\b", "individuals"), (r"\bcompany\b", "individual"),
)
NEGATIONS = (
    (r"\bdoes not\b", "does"), (r"\bis not\b", "is"), (r"\bare not\b", "are"), (r"\bshall not\b", "shall"),
    (r"\bcannot\b", "can"), (r"\bno\b", "a"),
    (r"\bis\b", "is not"), (r"\bare\b", "are not"), (r"\bmust\b", "need not"), (r"\bshall\b", "shall not"),
    (r"\bcan\b", "cannot"),
)
HARDENING = ((r"(?<!\bas )\bmay\b", "must"), (r"(?<!\bexcept )\bwhere\b", "only where"), (r"\bif\b", "only if"))
# Figures only: an amount after the naira sign, or a number before a rate or period unit. Section
# numbers and years are references, which the rule check treats separately.
_FIGURE = re.compile(r"(?:(?<=₦)|(?<=N))(\d{1,3}(?:,\d{3})+|\d+)(?![\w,])|"
                     r"(?<![\w.(])(\d+(?:\.\d+)?)(?=\s*(?:%|per cent\b|days?\b|months?\b|years?\b))")
_ARTICLE = re.compile(r"\b([Aa]) (?=(?:objection|individual|appeal|excise|exempt)\b)")


def _sub_first(patterns, text: str) -> tuple[str, str] | None:
    for pattern, replacement in patterns:
        m = re.search(pattern, text)
        if m and not re.search(r"\bonly\s+$", text[:m.start()]):
            new = replacement[0].upper() + replacement[1:] if m.start() == 0 else replacement
            out = _ARTICLE.sub(lambda a: a.group(1) + "n ", text[:m.start()] + new + text[m.end():])
            return out, f"{m.group(0)!r} -> {replacement!r}"
    return None


def mutate_figure(text: str, rng: random.Random):
    m = _FIGURE.search(text)
    if not m:
        return None
    old = m.group(1) or m.group(2)
    value = float(old.replace(",", ""))
    factor = rng.choice((2, 3))
    new_value = value * factor if value >= 1 else value + 1
    new = f"{int(new_value):,}" if "," in old else (str(int(new_value)) if new_value.is_integer() else f"{new_value:g}")
    start, end = m.span(1) if m.group(1) else m.span(2)
    return text[:start] + new + text[end:], f"{old} -> {new}"


def mutate_hardening(text, rng):
    return _sub_first(HARDENING, text)


def mutate_bridging(text, rng):
    bridge = BRIDGES[rng.randrange(len(BRIDGES))]
    return text.rstrip() + " " + bridge, f"added: {bridge}"


def mutate_subject(text, rng):
    return _sub_first(SWAPS, text)


def mutate_negation(text, rng):
    if "whether or not" in text.lower():
        return None  # a flipped verb inside "whether or not" can leave the claim true
    return _sub_first(NEGATIONS, text)


MUTATORS = {"figure": mutate_figure, "hardening": mutate_hardening, "bridging": mutate_bridging,
            "subject_swap": mutate_subject, "negation": mutate_negation}
TYPES = ("figure", "hardening", "bridging", "subject_swap", "negation", "citation_move")


def real_rows() -> list[dict]:
    verdicts = {(int(r["run"]), r["question_id"], int(r["claim_no"])): r for r in
                csv.DictReader(RESULTS_CSV.open(encoding="utf-8")) if r["claim_no"] != "0"}
    out = []
    for run, path in RUNS.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        for q in data["questions"]:
            shown = {p["chunk_id"]: p["label"] for p in q["passages"]}
            cited_by_answer = {c for claim in q["claims"] for c in claim["chunk_ids"]}
            for claim in q["claims"]:
                human = verdicts[(run, q["id"], claim["no"])]
                assert human["claim_text"] == claim["text"], (run, q["id"], claim["no"])
                out.append({"id": f"CAL-R{run}-{q['id']}-{claim['no']}", "kind": "real", "run": run,
                            "question_id": q["id"], "claim_no": claim["no"], "text": claim["text"],
                            "cited": [[shown[c], c] for c in claim["chunk_ids"]],
                            "human_verdict": HUMAN[human["verdict"]],
                            "expected": {"supported": "supported", "unsupported": "not_supported",
                                         "not_a_claim": "not_a_claim"}[HUMAN[human["verdict"]]],
                            "_uncited": [[p["label"], p["chunk_id"]] for p in q["passages"]
                                         if p["chunk_id"] not in cited_by_answer]})
    return out


def build() -> list[dict]:
    rng = random.Random(SEED)
    reals = real_rows()
    pool = [r for r in reals if r["human_verdict"] == "supported"]
    rng.shuffle(pool)
    used: set[str] = set()
    mutations = []
    for kind in TYPES:
        made = 0
        for base in pool:
            if made == PER_TYPE:
                break
            if base["id"] in used:
                continue
            if kind == "citation_move":
                if not base["_uncited"]:
                    continue
                label, chunk_id = base["_uncited"][rng.randrange(len(base["_uncited"]))]
                text, cited, change = base["text"], [[label, chunk_id]], f"cited {base['cited']} -> [{label}, {chunk_id}]"
            else:
                result = MUTATORS[kind](base["text"], rng)
                if result is None or result[0] == base["text"]:
                    continue
                (text, change), cited = result, base["cited"]
            used.add(base["id"])
            made += 1
            mutations.append({"id": f"CAL-M-{kind}-{made:02d}", "kind": "mutation", "mutation_type": kind,
                              "base_id": base["id"], "text": text, "base_text": base["text"], "change": change,
                              "cited": cited, "expected": "not_supported"})
        if made < PER_TYPE:
            raise SystemExit(f"only {made} {kind} mutations could be made")
    by_id = {r["id"]: r for r in reals}
    for row in mutations:
        if row["id"] in OVERRIDES:
            base_id, text = OVERRIDES[row["id"]]
            base = by_id[base_id]
            assert base["human_verdict"] == "supported" and text != base["text"], row["id"]
            row.update({"base_id": base_id, "text": text, "base_text": base["text"], "cited": base["cited"],
                        "change": "hand-written (overseer review 2026-11-03)"})
    assert len({row["base_id"] for row in mutations}) == len(mutations), "a base claim is used twice"
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in reals] + mutations


def main() -> int:
    if OUT.exists():
        raise SystemExit(f"{OUT.name} already exists; the calibration set is frozen")
    rows = build()
    with OUT.open("x", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    real = sum(r["kind"] == "real" for r in rows)
    print(f"{OUT.name}: {real} real claims, {len(rows) - real} mutations, seed {SEED}, "
          f"sha256 {hashlib.sha256(OUT.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
