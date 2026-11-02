# Phase 5 Exit Check (grounded generation)

Gate (implementation plan, Phase 5): across 15 questions, every factual claim in the generated
answers traces to a specific retrieved chunk, verified by manual read-through. Method decided in
D-29 ([generation design](generation_design.md), section 11).

**Status: exit gate passed on run 2** (run 1 failed with 2 unsupported claims; run 2 has 0).

## Method

- **Runner:** `.venv\Scripts\python eval\generation\run_exit_check.py --run N`. It runs each
  question once through `generate()` with the frozen retrieval configuration (D-21) and the
  approved generation settings (D-26). Query vectors come from the Phase 4 query cache, so dev
  questions retrieve exactly what the dev evaluation retrieved. A run number is never
  overwritten.
- **Outputs per run:**
  - `eval/generation/results/exit_check_runN.json` (committed). For each question it holds:
    status, claims with cited chunk ids, gaps, passages, disclosures (hidden rows included), the
    rendered answer, usage, cost and log record id.
  - `docs/phase05_generation/review_pack/runN/` (git-ignored, regenerated): `review_sheet.md`,
    which puts each answer next to the full text of every retrieved chunk, and a results template
    with one row per claim.
  - The full input and output of every call in `logs/generation/` (git-ignored, D-28).
- **Test split:** not used. Dev questions are loaded with the dev-only loader, and no test-split
  output is read (D-29). A unit test checks that no test-split question text appears in any
  Phase 5 file.

## Questions

Ten dev questions, one per category. In each category the pick is the question whose gold chunk
ranks lowest in the dev top 5; ties go to the question with more disclosable rows, then the lower
id. Five new questions (G01 to G05) are stored with their text in
`eval/generation/exit_questions.jsonl`.

| Id | Source | Category or purpose | Expected |
|---|---|---|---|
| Q004 | dev | cit_rates_levy | answered |
| Q007 | dev | deductions_gains (gold at rank 3) | answered |
| Q015 | dev | vat (gold at rank 2) | answered |
| Q018 | dev | exemptions_incentives | answered |
| Q025 | dev | definitions | answered |
| Q029 | dev | schedules (gold at rank 2) | answered |
| Q035 | dev | ntaa_registration_returns | answered |
| Q039 | dev | ntaa_assessment_objection | answered |
| Q043 | dev | ntaa_payment_penalties (gold at rank 4) | answered |
| Q047 | dev | section_reference | answered |
| G01 | new | out of corpus: another country | not_covered, no factual claims |
| G02 | new | out of v1 scope: stamp duties | not_covered, or only supported, cited claims |
| G03 | new | arithmetic bait | rate rules with citations, no computed amount |
| G04 | new | out of corpus: NTAA commencement | not_covered plus the DQ-12 note |
| G05 | new | NTA section 191(2) as printed | answer citing section 191, plus the DQ-16 note |

## Reviewers and pass rule

- The overseer checks every claim in all 15 answers against the text of the chunks it cites, and
  checks the disclosures. Kene then confirms 5 of the 15, chosen by the overseer: at least one
  decline, one answer with a disclosure, and G03.
- **Pass:** zero unsupported claims; G01 and G04 decline with no factual claims; G02 declines or
  makes only supported, cited claims; G03 has no computed amount; every required disclosure and
  the disclaimer are present. A failure means fixing the cause and re-running all 15 as the next
  run.
- **Record:** `exit_check_results.csv` in this folder, one row per claim (`run`, `question_id`,
  `claim_no`, `claim_text`, `cited_chunk_ids`, `verdict`, `reviewer`, `confirmed_by_owner`,
  `note`), plus a row with `claim_no` 0 per question for its status, disclosure and disclaimer
  checks. It is filled in from the template in the review pack.

## Run 1 (2026-11-02): failed

Model `claude-sonnet-5-5`, effort `medium`, prompt `gen-prompt/1`.

**Result: failed. 72 of 74 claims pass and 2 are unsupported.** An independent reviewer checked
all 74 claims against the cited chunk text. The verdicts are recorded in
[exit_check_results.csv](exit_check_results.csv) with run 1.

- **Supported:** 71.
- **Not a factual claim** (accurate, but a statement about the passages that belongs in gaps):
  1. This is Q018 claim 5.
- **Correction (2026-11-02):** G05 claim 3 was first recorded as not a factual claim, because its
  first sentence is a statement about the passages. The rest of the claim is a supported
  statement of section 41, so the verdict was changed to supported, with that sentence noted.
  This moves one claim from "not a factual claim" to "supported"; 72 of 74 still pass, and run
  1 still failed.
- **Unsupported:** 2.
  - **Q018 claim 4** uses outside knowledge ("Garri is made from cassava") to link the question
    to section 189 (m). The rest of the claim is supported.
  - **Q043 claim 5** overstates section 49(7). The section says that "where there is no
    objection or appeal" the authority "may proceed to enforce"; it does not say "only", and
    section 49(3) still makes the undisputed tax payable.
- **Other checks passed:** disclosures match the rule on all 15 answers, the disclaimer is on all
  15, G01, G02 and G04 decline with no factual claims, and G03 has no computed amount.

**Causes and fixes for run 2:**
- **The two unsupported claims.** The prompt had no rule against bridging facts or against
  hardening a condition. System prompt v2 adds them ([design](generation_design.md), section
  3.1).
- **The not-a-claim cases.** Statements about the passages had no defined place. v2 sends them to
  gaps.
- **Reader-facing gap wording.** G03's gaps used "you". v2 makes gaps plain and third person.
- **Rendering.** Gaps were joined with "; " and read ".;". They are now a bulleted list.

Mechanical facts from run 1:

| | Count |
|---|---|
| Answered | 12 (all 10 dev questions, G03, G05) |
| Not covered | 3 (G01, G02, G04) |
| Errors | 0 |
| Claims rendered | 74 |
| Citations rejected by the parser | 0 |
| Parser flags (for example a label inside claim text) | 0 |
| Question notes shown | DQ-12 on G04, DQ-16 on G05 |

Usage and cost (prices as in D-26):

| | Tokens |
|---|---|
| Input, uncached | 31,871 |
| Cache read (system prompt and schema) | 18,915 (1,261 per call) |
| Cache write | 0 (written by the API test calls just before) |
| Output | 6,423 |
| **Total cost** | **$0.1299, $0.0087 per question** |

The design estimated about $0.015 per question, assuming about 1,000 output tokens including
thinking. Measured output averaged 428 tokens, and no thinking block was returned at effort
`medium`. Input per call was 2,250 to 4,500 tokens including the cached part, close to the
estimate of 3,000 typical and 4,000 high.

Kene's confirmation of 5 answers was not taken for run 1, because run 1 failed; it was taken
for run 2.

## Run 2 (2026-11-02): passed

Model `claude-sonnet-5-5`, effort `medium`, prompt `gen-prompt/2` (system prompt v2, gaps
rendered as a bulleted list). The same 15 questions, with the same retrieved passages as run 1.

**Result: passed. 60 of 61 claims are supported, 0 are unsupported and 1 is not a factual
claim.** The exit gate passed on run 2.

- **Reviewers:** an independent reviewer checked all 61 claims against the text of the chunks
  they cite, using `review_pack/run2/`. Kene confirmed 5 answers on 2026-11-02: G03, G04, Q018,
  Q025 and Q043 (a decline, answers with disclosures, and G03), all fine. Verdicts and
  confirmations are recorded in [exit_check_results.csv](exit_check_results.csv) with run 2.
- **Supported:** 60.
- **Not a factual claim:** 1. Q018 claim 5 says what section 186 does not name. It is accurate,
  but it is a statement about the passages that rule 10 sends to gaps.
- **Unsupported:** 0.
- **Other checks passed:** disclosures match the rule on all 15 answers, the disclaimer is on all
  15, no claim or gap uses the second person, G01, G02 and G04 decline with no factual claims,
  and G03 has no computed amount.

**Notes for Phase 6 (guardrails):**
- **Gaps can carry uncited legal content.** G05 gap 2 restates the objection steps of section 41
  (written notice, then appeal to the High Court, the Court of Appeal and the Supreme Court)
  inside a gap, with no citation. Gaps are not checked as claims, so this needs a rule or a
  check.
- **The model mentions the disputed flag in gaps.** For example, Q018: "Several of these
  passages are marked as read differently in another official text". The disclosures already
  tell the reader this, so the gap repeats it in the model's own words.

Mechanical facts from run 2:

| | Count |
|---|---|
| Answered | 12 (all 10 dev questions, G03, G05) |
| Not covered | 3 (G01, G02, G04) |
| Errors | 0 |
| Claims rendered | 61 |
| Citations rejected by the parser | 0 |
| Parser flags (for example a label inside claim text) | 0 |
| Stop reason | `end_turn` on all 15 |
| Question notes shown | DQ-12 on G04, DQ-16 on G05 |

Usage and cost (prices as in D-26):

| | Tokens |
|---|---|
| Input, uncached | 31,871 (the same as run 1: same passages and questions) |
| Cache write | 1,455 (first call; the v2 system prompt and schema) |
| Cache read | 20,370 (1,455 on each of the other 14 calls) |
| Output | 6,322 |
| **Total cost** | **$0.1326, $0.0088 per question** |
