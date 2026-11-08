# Verifier Calibration (Phase 6, step B2)

Status: calibration complete on 2026-11-03; every target in design section 3.8 is met on the final
settings. Re-calibrated on 2026-11-04 after tuning run 1 (run 8, section 8): every target still met. These are tuning numbers on the calibration set (design section 3.8), not a test of the
whole system; gate G2 (design section 6) is that test.

This is a research and reference tool, not tax advice.

## 1. Method

- **Set:** `eval/guardrails/verifier_calibration_v1.jsonl` (sha256
  `7b929677724a9b6df6505bc986d3d67ac05bf60eeb9f3a03c6539b3bc391ec37`, D-36), reviewed by the
  overseer (`verifier_calibration_v1_review.json`, no exclusions). 195 rows: 135 real Phase 5
  claims (131 human-supported, 2 unsupported, 2 not a factual claim) and 60 mutations, 10 of each
  type. No test-split question and no adversarial question was used (D-29, D-40).
- **Runner:** `eval/guardrails/run_calibration.py`. Each claim goes through `check_claim` exactly as
  in the generation flow (rule check, Haiku 5.5 call, evidence check), except that the LLM call is
  always made, so the two checks are measured apart. At most 4 calls at once. Each run is written to
  `eval/guardrails/results/calibration_run<N>.json` and never overwritten. From run 3 the record
  also holds the verifier prompt version and its sha256; runs 1 and 2 used `ver-prompt/1` (the
  committed prompt at b639ba9).
- **Model:** `claude-haiku-5-5`, structured output, adaptive thinking, `max_tokens` 16,000
  (D-31). Only the effort changes between runs.
- **Tuning rule:** one change per run; the lowest effort that meets every target wins; stop if two
  changes in a row do not improve the result, or if spend passes $3.
- **Measures:** a mutation or a real unsupported claim is caught when its final verdict is not
  `supported`. A false removal is a human-supported claim whose final verdict is not `supported`.

**Targets (design section 3.8, D-36):** at least 57 of 60 mutations caught and at least 9 of 10 in
each type; both real unsupported claims caught; at most 6 of 131 false removals (5%); verdict
changes between two passes reported, above 3% a risk to review.

## 2. Runs

| Run | Prompt | Normaliser | Effort | Mutations caught | Real unsupported | False removals | Claims lost to evidence check | Cost | Latency per claim (median / max) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | ver-prompt/1 | norm/1 | medium | 60 / 60 | 2 / 2 | **32 / 131** | 12 | $0.0468 | 2.2 s / 7.8 s |
| 2 | ver-prompt/1 | norm/2 | medium | 60 / 60 | 2 / 2 | **20 / 131** | 0 | $0.0471 | 2.2 s / 7.0 s |
| 3 | ver-prompt/2 | norm/2 | medium | 60 / 60 | 2 / 2 | **7 / 131** | 0 | $0.0440 | 1.9 s / 6.1 s |
| 4 | ver-prompt/2 | norm/2 | low | 60 / 60 | 2 / 2 | **8 / 131** | 0 | $0.0315 | 1.6 s / 5.5 s |
| 5 | ver-prompt/3 | norm/2 | medium | 60 / 60 | 2 / 2 | 3 / 131 | 0 | $0.0432 | 1.8 s / 7.2 s |
| 6 | ver-prompt/3 | norm/2 | low | 60 / 60 | 2 / 2 | 4 / 131 | 0 | $0.0301 | 1.5 s / 4.1 s |
| 7, pass 1 | ver-prompt/3 | norm/2 | low | 60 / 60 | 2 / 2 | 3 / 131 | 0 | $0.0307 | 1.5 s / 4.1 s |
| 7, pass 2 | ver-prompt/3 | norm/2 | low | 60 / 60 | 2 / 2 | 3 / 131 | 0 | $0.0304 | 1.5 s / 4.1 s |
| 8, pass 1 | ver-prompt/4, ver-schema/2 | norm/3 | low | 60 / 60 | 2 / 2 | 3 / 131 | 0 | $0.0465 | 2.1 s / 5.7 s |
| 8, pass 2 | ver-prompt/4, ver-schema/2 | norm/3 | low | 60 / 60 | 2 / 2 | 1 / 131 | 0 | $0.0464 | 2.1 s / 5.2 s |
| 9, pass 1 | ver-prompt/5, ver-schema/2 (not adopted, D-90) | norm/5 | low | 60 / 60 | 2 / 2 | 2 / 131 | 0 | $0.0478 | 1.8 s / 6.1 s |
| 9, pass 2 | ver-prompt/5, ver-schema/2 (not adopted, D-90) | norm/5 | low | 60 / 60 | **1 / 2** | 2 / 131 | 0 | $0.0469 | 1.8 s / 5.0 s |

Run 8 is the re-calibration after tuning run 1 (section 8).

Bold marks a missed target. In every run, every mutation type was caught 10 of 10, both real
unsupported claims (run 1 Q018 claim 4, Q043 claim 5) were caught, both real "not a factual claim"
rows were returned as `not_a_claim`, and no call failed (0 `call_failed`).

## 3. Misses and fixes

### 3.1 Run 1: evidence labels (fixed by `norm/2`)

12 of the 32 false removals were `unverified / evidence_not_found` although the LLM said
`supported` and the quote was verbatim. The verifier wrote the evidence label with a section
suffix, such as `P1 s11(1)(a)` or `P2 section 151(2)`, instead of the passage label `P1`, so no
cited passage matched. Re-scoring run 1 offline with the leading passage label found all 12 quotes
in their passages.

Claims: CAL-R1-Q015-2, R1-Q015-4, R1-Q035-5, R1-Q039-1, R1-Q039-3, R1-Q043-6, R2-Q004-6,
R2-Q015-1, R2-Q035-6, R2-Q039-2, R2-Q047-1, R2-Q047-7.

**Fix (`norm/2`, `python/generation/verify.py`):** `passage_label` reads an evidence or citation
label as its leading `P<n>` token. The quote must still be found verbatim in that passage, and
that passage must be one the claim cites. This is normalisation of the label, not a looser match
(design section 3.4). The same reading applies to the per-citation `supports` labels, so a citation
whose label carried a suffix is no longer kept by default. Unit test:
`test_evidence_label_with_a_section_suffix_is_read_as_its_passage`.

After the fix, quotes not found fell from 25 of 144 (run 1) to 0 to 2 per run, and no claim was
lost to the evidence check in runs 2 to 7.

### 3.2 Runs 1 and 2: left-out exceptions (fixed by `ver-prompt/2`)

In run 2, 18 of the 20 false removals were `unsupported / overstated_condition`: the verifier
removed a correct statement of one rule because it did not repeat the provision's exceptions or
provisos. `ver-prompt/1` listed "a limit or exception left out" as an overstated condition, and the
verifier applied it to every claim. Examples: the dividend charge in NTA section 9(1) without the
section 9(2) exclusions (R1-Q004-1 to -3, R2-Q004-1, -2); collection in abeyance without the duty to
pay undisputed tax (R1-Q043-1, R2-Q043-1); NTA section 191 without its condition on tax-reducing
transactions (R1-G05-1). Some claims continue an earlier one ("the same applies", "the charge"),
and the verifier read them without that earlier context (R1-Q004-2, R2-Q004-2).

**Fix (`ver-prompt/2`):** an overstated condition now means the statement makes the rule
stronger or wider than the passage ("may" to "must", "where X" to "only where X", or "always",
"in all cases", "without exception"). A new paragraph says a statement may state one rule without
repeating exceptions given elsewhere, unless it says or implies there are none, and that
back-references are read as referring to what the passages describe. False removals fell from 20
to 7. Mutations stayed 60 of 60 (hardening 10 of 10).

### 3.3 Run 3: definitions and the statute's own "only" (fixed by `ver-prompt/3`)

The 7 left in run 3:

| Claim | Verdict | Cause |
|---|---|---|
| CAL-R1-Q025-1, R1-Q035-3, R2-Q025-1, R2-Q035-4 | unsupported / overstated_condition | The small-company definition (turnover and fixed-asset limits) without the professional-services proviso. The verifier read a definition as claiming to be complete. In each answer, a sibling claim states the proviso (R1-Q025-2, R1-Q035-4, R2-Q025-2, R2-Q035-5). |
| CAL-R1-Q035-1 | unsupported / overstated_condition | The audited-accounts rule without the small-company proviso; the sibling claim R1-Q035-2 states it. |
| CAL-R1-G05-4 | unsupported / overstated_condition | "Valid only if" delivered within 30 days: the verifier treated "only" as overstated because NTAA section 41(3) allows an extension. The passage itself says "shall only be valid if", and the sibling claim R1-G05-5 states the extension. |
| CAL-R1-G05-3 | unsupported / reference_not_in_source | Expected: the rule check finds `chap:4` ("not Chapter Four"), which no cited chunk contains. The claim is partly about the passages. |

**Run 4 (effort `low`, `ver-prompt/2`)** gave 8, no improvement on run 3. This was one change with
no improvement; the next change did improve the result, so the stop rule did not apply.

**Fix (`ver-prompt/3`):** the exceptions rule is extended to definitions (a definition's main test
without its exclusions is not unsupported for that reason alone), and "only" or "must" overstates
the rule only when the passage does not use that word for that rule. Run 5 (medium) gave 3 and run 6
(low) gave 4, both within the target.

### 3.4 What is left on the final settings (run 7, both passes)

| Claim | Verdict | Cause |
|---|---|---|
| CAL-R1-G05-3 | unsupported / reference_not_in_source | Expected rule-check removal (`chap:4`), as above. The LLM said `supported` in pass 1 and `not_a_claim` in pass 2; the rule check decides. |
| CAL-R1-Q004-3 | unsupported / overstated_condition | "The company is charged at the rate prescribed in section 56 ... as if the dividend were the company's total profits". The verifier reads the charge as unconditional; the conditions are in the earlier claims of the same answer. |
| CAL-R2-Q035-4 | unsupported / overstated_condition | The small-company definition without its proviso (sibling R2-Q035-5 states it). |

Across runs 5 to 7 the remaining false removals vary between these claims, R1-Q035-1 and
R1-Q029-3. Apart from G05-3 and R1-Q029-3, each is a correct statement of one rule whose
exception or condition is in another claim of the same answer; the verifier sees one claim at a
time (design section 3.3), so it cannot see the sibling claim. R1-Q029-3 says a capital allowance
is made "only if" the company owns and uses the asset; the verifier finds "only" in paragraph 8(2)
for the in-use condition but not for ownership. That is a fair reading, and the human verdict on
it is arguable.

## 4. Final settings

| Setting | Value |
|---|---|
| Verifier model | `claude-haiku-5-5` |
| Effort | **`low`** (`VERIFIER_EFFORT` in `python/generation/llm.py`; it was `medium` as the starting point) |
| Verifier prompt | **`ver-prompt/3`**, system prompt sha256 `d3af02e852632c58c1de8648c027379fcd427864194ab8a386fe894a16de8dde` |
| Verifier schema | `ver-schema/1` (unchanged) |
| Normaliser | **`norm/2`** |
| Gap prompt and schema | `gap-prompt/1`, `gap-schema/1` (not calibrated here; tested in the tuning runs) |

`low` wins under the rule "the lowest effort that meets all targets": runs 6 and 7 meet every
target at `low`. `medium` was slightly better in run 5 (3 against 4 false removals), but both are
within 6 of 131, and `low` costs about 30% less and is faster.

The text added in `ver-prompt/2` and `ver-prompt/3` (in `python/generation/prompt.py`):

```text
Exceptions and earlier statements
The statement is one of several in an answer, and other statements may give the exceptions,
provisos and further conditions of the same provision. A statement may state one rule of a
provision without repeating them. Leaving them out is not unsupported, unless the statement says
or implies that there are none (for example "only", "always", "in all cases", "without
exception") or states a condition differently from the passage. This holds for definitions too: a
statement that gives a definition's main test without its exclusions or provisos is not
unsupported for that reason alone. A word such as "only" or "must" overstates the rule only when
the passage does not use it for that rule; a statement that repeats the passage's own "only" is
not overstated because an exception appears elsewhere. The statement may also continue an
earlier one ("the same applies", "the company", "such a person", "the charge"). Read such words as
referring to what the passages describe, and do not mark the statement unsupported only because it
does not repeat the earlier statement's conditions.
```

`ver-prompt/2` also narrowed the overstated-condition example in the Verdicts list (it no longer
lists "a limit or exception left out").

## 5. Results on the final settings (run 7)

| Measure | Target | Pass 1 | Pass 2 |
|---|---|---|---|
| Mutations caught | at least 57 of 60 | 60 of 60 | 60 of 60 |
| Per type (figure, hardening, bridging, subject swap, negation, citation move) | at least 9 of 10 each | 10 of 10 each | 10 of 10 each |
| Real unsupported claims caught | both | 2 of 2 | 2 of 2 |
| False removals | at most 6 of 131 | 3 (2.3%) | 3 (2.3%) |
| Evidence quotes not found | reported | 1 of 164 (no claim lost) | 0 of 160 |
| Verdict stability | report; above 3% is a risk | 1 of 195 changed (0.51%) | |

- **Stability:** one final verdict changed between the passes: CAL-M-negation-09 was `unsupported`
  in pass 1 and `contradicted` in pass 2. It is caught in both. The difference matters at answer
  level, because a `contradicted` claim refuses the whole answer (design section 3.7, rule 1).
- **How mutations are caught (pass 1):** 13 of 60 by the rule check (figures and references), the
  rest by the LLM. Negation mutations come back mostly `contradicted` (9 of 10), subject swaps half
  `contradicted` and half `unsupported`, the other four types `unsupported`.
- **No human-supported claim was `contradicted`** in any pass, so no false whole-answer refusal
  would come from the verifier on this set.

## 6. Cost and latency

| Item | Value |
|---|---|
| Total spend, runs 1 to 7 (8 passes, 1,560 calls) | **$0.3036** (budget estimate in design section 8.3: about $0.38) |
| Per claim at `low` (run 7) | about $0.00016 (195 claims for $0.0307) |
| Per answered question, claims only (6 claims) | about $0.001, at the low end of the design estimate ($0.001 to $0.003) |
| Latency per claim call at `low` | median 1.5 s, max 4.1 s |
| Cache | the system prompt was read from cache on every call of run 7, still warm from run 6 (each pass: 312,585 cache-read tokens, 117,888 uncached input, no cache writes) |

Per-answer latency is not measured here, since the calibration runner checks claims, not answers.
With 6 claims and 4 calls at a time, two rounds of about 1.5 s each plus the gap call suggest
roughly 3 to 5 s on top of generation. The tuning runs measure it.

## 7. Risks and notes for review

- **Lenient on left-out exceptions by design.** `ver-prompt/2` and `/3` accept a correct statement
  of one rule without its exceptions. This matches the human verdicts, because in every case here
  a sibling claim gave the exception. But if the generator leaves an exception out of the whole
  answer, the verifier will no longer remove the claim for that. Claims that add "only", "must",
  "always" or similar are still caught (hardening 10 of 10, real Q043 claim 5 caught). The tuning
  runs should check for answers that drop a material proviso entirely.
- **The calibration set has no mutation of the type "exception left out of the whole answer"**, so
  the change in section 3.2 is not measured against that failure. Adding such mutations would
  need a new calibration file (`v2`), reviewed and frozen as v1 was.
- **The remaining false removals are one pattern** (rule correct, exception in a sibling claim).
  Showing the verifier the other claims of the answer as context would address it, but it changes
  the isolation design in section 3.3 and is not proposed here.
- **Design section 3.3 still shows the `ver-prompt/1` draft.** It is the draft as approved; this
  report records the calibrated versions, and section 3.3 now points here (D-42).
- **Follow-up (D-43):** the left-out-exception risk above is handled by a generator rule
  (`gen-prompt/4`) and by the reviewer's pass rule (design section 6.2), with an automated
  answer-level check only if tuning shows it is needed.

## 8. Re-calibration after tuning run 1 (run 8, 2026-11-04)

Tuning run 1 led to three verifier changes: `ver-schema/2` with `ver-prompt/4` (the verifier
writes its reasoning before the verdict) and a second call on every `contradicted` verdict (D-46),
and `norm/3` (a Schedule chunk's line-start "1.", "2." numbering counts as paragraph 1, 2). Run 8
measures them on the same frozen set, at effort `low`, two passes
(`eval/guardrails/results/calibration_run8.json`). These settings are recorded as D-47.

| Measure | Target | Pass 1 | Pass 2 |
|---|---|---|---|
| Mutations caught | at least 57 of 60 | 60 of 60 | 60 of 60 |
| Per type | at least 9 of 10 each | 10 of 10 each | 10 of 10 each |
| Real unsupported claims caught | both | 2 of 2 | 2 of 2 |
| False removals | at most 6 of 131 | 3 (2.3%) | 1 (0.8%) |
| Evidence quotes not found | reported | 3 of 215 (no claim lost) | 3 of 218 (no claim lost) |
| Verdict stability | report; above 3% is a risk | 6 of 195 changed (**3.08%**) | |

- **Every target is met.** False removals: pass 1 CAL-R1-Q029-3 (`overstated_condition`),
  CAL-R1-G05-3 (rule check, `chap:4`, as in every run) and CAL-R2-Q039-1 (`not_a_claim`); pass 2
  only CAL-R1-G05-3.
- **Second calls (D-46).** Pass 1 had 11 first-call `contradicted` verdicts and pass 2 had 11; the
  second call confirmed 9 and 10 of them. The 3 it did not confirm (CAL-M-subject_swap-08 in both
  passes, CAL-M-negation-04 in pass 1) became `unsupported`, so they are still caught. No
  human-supported claim was `contradicted` by either call in either pass.
- **Stability is above the 3% line (risk to review).** Of the 6 changes, 3 are mutations moving
  between `unsupported` and `contradicted` (caught both times; the difference matters only at
  answer level, where `contradicted` refuses), 2 are human-supported claims removed in pass 1 and
  kept in pass 2 (CAL-R1-Q029-3, CAL-R2-Q039-1), and 1 is the real unsupported claim
  CAL-R1-Q018-4, `unsupported` in pass 1 and `not_a_claim` in pass 2. Changes that alter whether a
  claim is shown: 2 of 195 (1.0%). In the answer flow a `not_a_claim` verdict moves the text to
  gaps, where the gap check judges it, so CAL-R1-Q018-4 would not be shown as a claim in either
  pass.
- **Cost and latency.** Writing the reasoning roughly doubles output tokens (about 61,000 per
  pass, against about 31,000 in run 7), so a pass costs $0.0465 against $0.0307, about $0.00024
  per claim. Median latency per claim call rose from 1.5 s to 2.1 s. Runs 1 to 8 spent $0.3964 in
  all.

## 9. Case-insensitive evidence check (`norm/4`, D-48, 2026-11-04)

Tuning run 2 lost one correct claim (Q029 claim 3, First Schedule paragraph 7) to the evidence check: the
verifier's quote began "a capital allowance" and the passage reads "A capital allowance". `norm/4` compares
the case-folded quote with the case-folded passage; whitespace, punctuation handling and the 12-character
minimum are unchanged.

**Offline re-check, no API calls.** `verify.evidence_found` (`norm/4`) was re-applied to every stored quote
against the chunk of its cited label, and a final verdict was counted as changed where a claim removed as
`unverified/evidence_not_found` would now have a quote found. Counts are over every stored quote of every
row and pass, so they differ from the section 5 counts, which were taken differently.

| Run | Quotes not found before | Found now | Final verdicts that change |
|---|---|---|---|
| Calibration run 7 (2 passes) | 3 | 0 | none |
| Calibration run 8 (2 passes) | 6 | 2 | none (both are CAL-R1-Q029-3, which had another quote found in each pass) |
| Tuning run 2 | 1 | 1 | Q029 claim 3: `unverified` to `supported` |

No re-calibration run and no tuning run 3 were made (D-48): the change can only recover claims the verifier
already judged `supported`.

## 10. Run 9: the link rule (`ver-prompt/5`, D-90, 2026-11-08)

Tuning run 11 kept a claim that invented a link between provisions (ADV-085 claim 3: "the exemption list in
section 186(1) is subject to section 186(2)"; tuning report, section 17). `ver-prompt/5` is `ver-prompt/4`
verbatim plus one paragraph, "Links between provisions" (4,502 characters, sha256 `cf443329...2c49`; the text is
in G2 fix design 2, section 15). Run 9 measures it on the same frozen set with the live settings (effort `low`,
`norm/5`), two passes (`eval/guardrails/results/calibration_run9.json`).

| Measure | Gate (D-90) | Pass 1 | Pass 2 |
|---|---|---|---|
| Mutations caught | 60 of 60 | 60 of 60 (10 of 10 each type) | 60 of 60 (10 of 10 each type) |
| Real unsupported claims caught | 2 of 2 | 2 of 2 | **1 of 2: missed** |
| False removals | at most 3 of 131 | 2 (CAL-R1-Q035-1, CAL-R1-G05-3) | 2 (CAL-R1-Q015-6, CAL-R1-G05-3) |
| Claims lost to the evidence check | 0 | 0 (1 of 210 quotes not found) | 0 (1 of 216 quotes not found) |
| Verdict stability | reported | 6 of 195 changed (3.08%) | |

- **The gate fails in pass 2.** CAL-R1-Q018-4 ("The definition of basic food items includes tubers of ... cassava
  ... Garri is made from cassava, but the passages do not name garri.") is a human-unsupported claim that mixes
  a rule with a statement about the passages. It was `unsupported` in every pass of runs 1 to 7, `not_a_claim` in
  run 8 pass 2 and run 9 pass 1 (counted as caught), and `supported` in run 9 pass 2. The verifier's reasoning
  does not mention links between provisions; it judged "the main content is a legal rule". The claim sits on
  the "about the passages" boundary, and two passes cannot show whether the new paragraph moved it. The gate
  was fixed before the run, so `ver-prompt/5` is not adopted.
- **False removals are older patterns**, not the new rule: CAL-R1-Q035-1 (the audited-accounts rule without the
  small-company proviso, as in section 3.3) and CAL-R1-Q015-6 (an `overstated_condition` for a dropped "in
  Nigeria"); CAL-R1-G05-3 is the rule-check removal of every run.
- **Second calls (D-46).** 10 and 11 second calls; 10 and 9 confirmed, the same as run 8. No human-supported claim
  was `contradicted`.
- **Stability.** The 6 changes: 3 mutations moving between `unsupported` and `contradicted` (caught both times),
  CAL-R1-Q015-6 and CAL-R1-Q035-1 (human-supported, removed in one pass each) and CAL-R1-Q018-4.
- **Cost.** $0.0947 for both passes (about $0.00024 per claim, as run 8). Runs 1 to 9 spent $0.4911 in all.

The verifier stays at `ver-prompt/4` (sha256 `b5e2f49c...d918`), as D-90 set out for a failed gate.
