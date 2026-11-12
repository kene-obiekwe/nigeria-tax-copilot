# Phase 8 Evaluation Report: End-to-End Measurement and Gate G3

**Result: G3 passed (D-149).** Both final runs met every threshold on their own (D-134, D-137).
**Date:** 2026-11-12. **System measured:** the shipped configuration, `config_p7.json` with tools on,
frozen as `eval/e2e/config_p8.json` (D-140, D-144). **Corpus snapshot:** 18 October 2026.

> **Disclaimer.** This is a research and reference tool. It is **not tax advice** and is not a
> substitute for a licensed tax professional. It must not be used for an actual filing or
> compliance decision. The corpus is the Nigeria Tax Act 2025 and selected procedural parts of the
> Nigeria Tax Administration Act 2025 (v1 scope, D-10), as a dated snapshot of 18 October 2026.
> **Personal income tax is out of scope (D-64):** questions about it, about PAYE and about an
> individual's tax bands and rates are declined. The figures below describe this system on this
> question set; they are not a guarantee about any other question.

## 1. G3 result

Gate G3 (D-134): on a held-out end-to-end set run with the frozen configuration, (a) at least 95% of
the items shown to the user are supported by a retrieved passage they cite; (b) at least 95% of shown
citations point to a section that says what the claim states; (c) no case that must be refused or is
outside the corpus is answered; (d) at most 10% of in-scope answerable questions are falsely
refused (at most 5 of 50 per run). Under D-137 each of the two runs must meet all four on its own;
the pooled figures are reported, not gated. Intervals are Wilson 95% intervals.

| | Metric | Threshold | Run 1 | Run 2 | Pooled (not gated) |
|---|---|---|---|---|---|
| (a) | Groundedness: shown items supported | at least 95% | **244 of 244** (100%; 98.5% to 100%) | **245 of 245** (100%; 98.5% to 100%) | 489 of 489 (99.2% to 100%) |
| (b) | Citation accuracy: shown citations accurate | at least 95% | **246 of 246** (100%; 98.5% to 100%) | **245 of 245** (100%; 98.5% to 100%) | 491 of 491 (99.2% to 100%) |
| (c) | Must-refuse cases answered | none | **0 of 40** (upper bound 8.8%) | **0 of 40** (upper bound 8.8%) | 0 of 80 (upper bound 4.6%) |
| (d) | False refusals of in-scope questions | at most 5 of 50 | **1 of 50** (2.0%; 0.4% to 10.5%) | **1 of 50** (2.0%; 0.4% to 10.5%) | 2 of 100 (2.0%; 0.6% to 7.0%) |
| | Run verdict | | **pass** | **pass** | |

**G3 verdict: pass** (`eval/e2e/results/g3_result.json`, verdict `pass`).

Notes on reading the table:

- The bootstrap interval over questions (10,000 resamples of the 50 in-scope questions, seed
  20261113) is 100% to 100% for (a) and (b) in both runs. It collapses because no item failed, so it
  adds nothing beyond the Wilson interval. The Wilson lower bound (98.5% a run) is the honest floor.
- No item in either run was judged not supported or inaccurate, and none was supported only by a
  shown passage the claim did not cite ("support not cited": 0 in both runs). Section 3.2 therefore
  has no failed items to list.
- Under D-139 the (a) count includes gaps that state law and computed items. Run 1 counts 238 claims,
  1 gap that states law and 5 computed items (244). Run 2 counts 236 claims, 4 gaps that state law
  and 5 computed items (245). The (b) count adds the rate sections the computed items name: run 1
  has 239 claim citations and 7 rate citations; run 2 has 238 and 7.
- (c) is a bound, not a proof: zero failures in 40 cases only bounds the true answer rate of the
  system to about 7.5% (rule of three; Wilson 8.8%). See Section 6.
- Spend: $1.6527 of the $3.00 cap (D-133): smoke run $0.0325, run 1 $0.8177, run 2 $0.8025. No run
  was void. The Anthropic Console figure was not recorded in the run records.

## 2. Method

### 2.1 The question set

Ninety cases per run, the same 90 in both runs (the evaluation design, Sections 1.5 and 2).

| Subset | Cases | What | Feeds |
|---|---|---|---|
| T | 30 | The retrieval test split (Q001, Q002, ... Q049), used once end to end (D-135). In-scope, answerable, written in Phase 4 before any generation existed. | (a), (b), (d), hit@5 |
| N | 20 | New and sealed: 14 statute questions across the 10 categories and 6 calculation questions, one per calculator scenario (D-136). | (a), (b), (d), hit@5 |
| R | 40 | New and sealed: must-refuse and out-of-corpus cases in six categories (R1 to R6, Section 3.3). | (c) |

The new cases (`eval/e2e/e2e_v1.jsonl`) and the labels for T (`labels_test_v1.jsonl`) were written by
a separate session that never saw the system's answers, label-checked by a further session that read
no system output, pinned by sha256 and not opened by the build, run or overseer sessions before both
runs were made (D-136). They are published unchanged with this report. **After Phase 8 the retrieval
test split is spent for generation as well (D-135), and every Phase 8 set is tuning data if the
result is ever acted on (D-133).**

**The T bias.** Retrieval is known to find a gold unit for every T question (G1: hit@5 30 of 30,
D-21), so T is easier than unseen questions on retrieval and therefore on false refusals. This is why
(a), (b), (d) and hit@5 are reported for T and N separately as well as together (Section 3.1). The
hit@5 on T here is a repeat of G1 under the shipped retrieval, not a new held-out result.

### 2.2 Labels and definitions

Each label row names the expected status, the key points a correct answer states, the units it should
cite, the gold units for hit@5, a hand-worked figure for calculation questions and the statements a
correct answer must not make. Only the third purpose gates: expected status defines which cases count
in (c) and (d); expected answers and citations measure usefulness (Section 4), and gold units measure
hit@5. The definitions are those of the evaluation design, Section 3.4.

- **(a) Groundedness.** The unit is one shown item in one answer in one run: every shown claim, every
  kept gap the reviewer classes as stating law, and every shown computed item. A claim is supported
  when everything it states is stated by (or follows without outside knowledge from) the text, and the
  context header, of the chunks it cites. A computed item is supported when the calculator's checks K1
  to K6 passed and its result equals the hand-worked figure of the label. Only the verdict `supported`
  counts.
- **(b) Citation accuracy.** The unit is one shown (claim, cited chunk) pair, plus one (computed item,
  rate chunk) pair for each section a shown computed item names. Each citation is judged on its own.
- **(c) Refusal correctness.** An R case fails when its status is `answered` or any claim or computed
  item is shown, whatever the status.
- **(d) False refusals.** A T or N case is a false refusal when its status is `refused`,
  `not_covered` or `error`, or it is `answered` with nothing shown. An answer that shows at least one
  claim or computed item is not a false refusal, however incomplete.
- Fixed texts (notes, disclosures, refusal texts, the disclaimer) are code-rendered and are not
  counted in (a) or (b).

### 2.3 The configuration

`eval/e2e/config_p8.json` (sha256 `28e205155c10ef6702f569cf005df6a4f819084e53aa24e597a8b120141cf36a`)
records `eval/tools/config_p7.json` (sha256 `e2f61fe680759c0778719c01dfc30d2a463d6f484f54e8f7d7989e5a02ceec25`)
as its base with no overrides. In short (the stack is described in the [README](../../README.md) and
the earlier phase reports): vector search plus exact section lookup, top 5 passages
(`config_final.json`, D-21); generation with Claude Sonnet 5.5 and verification with Claude Haiku 5.5
(D-03); the Phase 6 guardrails (pre-check, claim verifier, gap check, scope notes, refusal rules); and
the Phase 7 calculator and section lookup tools, with the model choosing automatically whether to use
a tool. Both runs checked the live configuration against the frozen file before the first paid call.
The input files are pinned in the run records:

| File | sha256 |
|---|---|
| `eval/e2e/e2e_v1.jsonl` | `dcab23f9cd14cfa45734d3029a202a5ec9d46a248d50425d533b29ca27c96d52` |
| `eval/e2e/labels_test_v1.jsonl` | `03773fc77a6f8ad787130ca6da7baa03d2115dc36b2681d8d9a1912dc92dd3f5` |
| `eval/e2e/labels_dev_v1.jsonl` | `ad3d89cd228fb5b1225d4a577dc1cf42d2c33e75890e86c06e489b7d8374c94d` |
| `eval/e2e/blind_calibration_v1.jsonl` | `49afc6dabfd3afbbc4dbcd9d4cfea2fb794f21e77835f37e4ee7d540e5537499` |

The two runs were made back to back on 2026-11-12 (12:31 to 12:55 UTC) at commit a6b6c21, before
either was reviewed. Their records are in commit 8ff3b16; the reviews are in bd59e73.

### 2.4 The judge and how it was checked

(a) and (b) are judged by a check independent of the pipeline's own verifier (D-134): a model session,
the Phase 8 run session, read every shown item against the text of each cited chunk and recorded its
verdict before looking at the pipeline verifier's output (D-138). Both review files name this one
reviewer. No successor session was recorded (D-146).

- **Blind calibration (D-148).** Before the runs, the reviewer judged the 195 calibration claims in a
  seeded random order with the labels removed. Attempt 1 met every fixed target: 60 of 60 mutated
  claims judged not supported (10 of 10 per type; target at least 58 of 60 and at least 9 of 10 per
  type); 2 of 2 real unsupported claims judged not supported; 1 of 131 human-supported claims judged
  not supported (BC-105; target at most 4). The 2 items labelled "not a claim" were judged as claims
  (reported, not a target). Review sha256 `5e7e0cfe85dcd6454d9265eed6efeba5c48559a118319a805b5b7f273b800d45`,
  scores `107f8fe04f54410f99d4fd42e01c2b86e0957f6753763538a74c344130c56c6f`. D-147 (a definition chunk's
  citation label counts as part of its context header) was applied.
- **Overseer check (design Section 4.1, point 3).** The overseer read a seeded random 10% sample of the
  shown claims, 24 per run (seed 20261112 plus the run number), against the chunk text: **48 of 48
  agree**. Four close calls were read in full and agree (run 2 Q009 claim 2, run 2 N08 claim 7, run 1
  N17 gap 1, run 2 Q024 gap 2). 16 sampled gaps judged "not stating law" agree. Every R case and every
  false refusal was read. There were no adjudications, so the reviewer's own figures and the
  adjudicated figures are identical.
- **Kene's spot check (design Section 4.1, point 4).** Ten cases, five a run, all confirmed on
  2026-11-12: run 1 N15, Q028, Q049, N05, R21; run 2 N16, Q009, N12, R07, R13. Kene first questioned
  R07 and R13, because an outside AI model had judged them against the whole Act. The overseer then
  showed that Section 13, Section 58 and the Fourth Schedule are outside the v1 corpus (D-10, D-64;
  [v1 corpus definition](../phase01_corpus/v1_corpus_definition.md)), and Kene confirmed both as
  correct declines. The record is in `eval/e2e/results/g3_check.json`.

## 3. Results by subset and category

### 3.1 T and N

| Run | Subset | Cases | (a) supported items | (b) accurate citations | (d) false refusals | hit@5 |
|---|---|---|---|---|---|---|
| 1 | T | 30 | 158 of 158 (97.6% to 100%) | 159 of 159 (97.6% to 100%) | 0 of 30 (0% to 11.4%) | 30 of 30 |
| 1 | N | 20 | 86 of 86 (95.7% to 100%) | 87 of 87 (95.8% to 100%) | 1 of 20 (0.9% to 23.6%) | 19 of 20 |
| 2 | T | 30 | 155 of 155 (97.6% to 100%) | 155 of 155 (97.6% to 100%) | 0 of 30 (0% to 11.4%) | 30 of 30 |
| 2 | N | 20 | 90 of 90 (95.9% to 100%) | 90 of 90 (95.9% to 100%) | 1 of 20 (0.9% to 23.6%) | 19 of 20 |

Pooled over both runs, (a) is 313 of 313 on T and 176 of 176 on N; (b) is 314 of 314 on T and
177 of 177 on N. Hit@5 counts a case when a gold unit is among passages P1 to P5; the same counts
result when any shown passage (P1 to P8 and lookups) is used. N is the unseen part: it is the better
guide to new questions, and its false-refusal interval is wide (1 of 20).

### 3.2 Failed items and false refusals

**Failed items: none** in either run, for (a) or (b).

**False refusals: one, the same case in both runs.** N05 ("My company owns and runs a professional
football club. Are the club's profits free of company income tax?") was declined with status
`not_covered`. The cause is a **retrieval miss**: the gold unit (`nta2025:s163(1)(m)-(v)`, which holds
Section 163(1)(r), "profits of a company engaged in sporting activities") was not among the
six passages shown (P1 to P6; of Section 163 only `s163(1)(a)-(c)`, an acceptable citation, was
retrieved). The overseer's reading, which Kene confirmed, is that the law answers this question, so
the decline counts against the system.

**Answers that are incomplete (reported, not gated).** Four answers show claims but miss a key point,
in both runs (Section 4):

| Case | Missing | Why |
|---|---|---|
| Q032 (Thirteenth Schedule) | both key points (the first only in part) | Lists 2 of the 6 Schedule activities (run 1: crop production and cocoa processing; run 2: crops and livestock processing); five claims were moved to gaps by the verifier; the five-year exemption is not stated. |
| N17 (development levy) | the figure ₦2,900,000 | The calculator declined (`missing_input`: gross turnover). The question says the company is not a small company, but the tool wanted turnover; the answer states the 4% rule without the figure. |
| N11 | one key point in part | Some grounds of the objection are stated; the disputed issues with their values are not. |
| Q009 (share-disposal gain) | one key point | States the exemption thresholds but does not apply them to the ₦80 million and ₦5 million in the question. |

### 3.3 R cases (refusal correctness)

Both runs gave the same result: **no R case was answered and no claim or computed item was shown for
any of them (40 of 40 correct in each run).** Exact status matched on 34 of 40 in each run.

| Category | Cases | Answered | Exact status | Status returned |
|---|---|---|---|---|
| R1 Non-existent provision | 6 | 0 | 4 | 3 `no_such_section`, 1 `section_outside_v1`, 2 `not_covered` (expected `refused`) |
| R2 Real section outside v1 | 6 | 0 | 6 | 6 `section_outside_v1` |
| R3 Taxes and law outside the corpus | 8 | 0 | 7 | 7 `not_covered`, 1 `refused` (`low_relevance`; expected `not_covered`) |
| R4 Other countries | 6 | 0 | 3 | 3 `foreign_law`, 3 `not_covered` (expected `refused`) |
| R5 Unrelated | 6 | 0 | 6 | 6 `low_relevance` |
| R6 In-domain but not in the text | 8 | 0 | 8 | 8 `not_covered` |

The six exact-status misses (R05, R06, R18, R22, R23, R26) are identical in both runs. (c) passes on
either `refused` or `not_covered`, as in D-37, so none is a failure.

### 3.4 By category (T and N together, both runs pooled)

Counts are over both runs. Every shown item was supported and every citation accurate in every
category. The only false refusals are N05 in each run.

| Category | T cases | N cases | (a) supported items | (b) accurate citations | False refusals |
|---|---|---|---|---|---|
| Company income tax rates and levy | 3 | 1 | 36 of 36 | 37 of 37 | 0 |
| Deductions and gains | 4 | 1 | 51 of 51 | 50 of 50 | 0 |
| VAT | 4 | 2 | 71 of 71 | 71 of 71 | 0 |
| Exemptions and incentives | 4 | 1 | 22 of 22 | 24 of 24 | 2 (N05, both runs) |
| Definitions | 3 | 2 | 30 of 30 | 29 of 29 | 0 |
| Schedules | 2 | 1 | 32 of 32 | 32 of 32 | 0 |
| Administration Act: registration and returns | 3 | 2 | 44 of 44 | 44 of 44 | 0 |
| Assessment and objection | 2 | 1 | 39 of 39 | 39 of 39 | 0 |
| Payment and penalties | 2 | 2 | 41 of 41 | 41 of 41 | 0 |
| Section reference | 3 | 1 | 75 of 75 | 75 of 75 | 0 |
| Calculation (6 scenarios) | 0 | 6 | 48 of 48 | 49 of 49 | 0 |
| **All** | **30** | **20** | **489 of 489** | **491 of 491** | **2** |

### 3.5 Calculation questions

Five of the six calculation questions in N were computed in each run, and each shown figure equals
the hand-worked figure of its label with checks K1 to K6 passed (N15: ₦60,000 VAT; N16: ₦13,500,000
company income tax; N18: ₦340,000; N19: ₦300,000; N20: ₦290,000). The sixth, N17, was declined by the
tool (Section 3.2). In each run six answers (all N) used a tool and the other 43 answered without one.

## 4. Reported metrics (not gated)

| Metric | Run 1 | Run 2 | Pooled |
|---|---|---|---|
| Answered cases (T and N) | 49 of 50 | 49 of 50 | 98 of 100 |
| Hit@5, T | 30 of 30 | 30 of 30 | 60 of 60 |
| Hit@5, N | 19 of 20 | 19 of 20 | 38 of 40 |
| Answer coverage (key points stated) | 89 of 94 | 89 of 94 | 178 of 188 |
| Answers complete (all key points, nothing forbidden, no omitted proviso) | 45 of 49 | 45 of 49 | 90 of 98 |
| Citation recall (answer cites an expected or acceptable unit) | 49 of 49 | 49 of 49 | 98 of 98 |
| Claims shown per answered question | 4.86 (238 of 49) | 4.82 (236 of 49) | 4.84 |
| Claims removed by the verifier / moved to gaps | 4 / 6 | 1 / 6 | 5 / 12 |
| Disclaimer at the end of every answer | 49 of 49 | 49 of 49 | 98 of 98 |
| Scope notes shown on answered cases | 4 (Q031, Q016, Q020, N17) | the same 4 | 8 |

- **Answer coverage and completeness.** The five missing key points and the four incomplete answers
  are the cases of Section 3.2; the two runs agree case for case. No answer stated anything on its
  `must_not` list and none omitted a proviso that changes the answer.
- **Usefulness.** Run 1 removed claims in Q016, N08, N11 and Q023 and moved claims in Q032 (five) and
  Q023 (one); run 2 removed one claim (N11) and moved claims in Q032 (five) and Q023 (one). The claim
  count per answer is about 4.8, close to the Phase 7 dev figure of 5.2 (design Section 1.3).
- **Notes.** The labels require no scope notes (D-143: `required_notes` is empty in every Phase 8
  label row), so note recall is not measured here. Four scope notes were shown on answered cases in
  each run (surcharge, state and local taxes, economic development incentives, petroleum taxes).
  Notes also appear on R cases. Whether any note was useful or correctly chosen was not judged.
- **Latency and cost per query.** Measured end to end around `generate()` on the project laptop with
  the database in Docker, as the design specified; deployed latency is a Phase 12 measurement.
  Cost is the computed `total_cost_usd` (generation including the tool loop, verifier and the three
  tagger votes) at the prices pinned in `python/generation/llm.py`; the query embedding (under
  $0.000001) is not summed.

| Cases (both runs) | n | Latency mean | median | 90th pct | max | Cost mean |
|---|---|---|---|---|---|---|
| All | 180 | 7.9 s | 8.3 s | 12.9 s | 46.8 s | $0.0090 |
| Answered | 98 | 11.3 s | 10.2 s | 14.0 s | 46.8 s | $0.0134 |
| Declined by the model (`not_covered`) | 42 | 5.5 s | 5.2 s | 5.9 s | 14.0 s | $0.0071 |
| Refused before retrieval (`refused`) | 40 | 2.0 s | 2.0 s | 2.6 s | 3.2 s | $0.0002 |

Per run: run 1 mean latency 8.2 s (median 8.3 s, 90th percentile 13.0 s, maximum 46.8 s) and mean
cost $0.0091, $0.8177 in all; run 2 mean latency 7.5 s (median 8.3 s, 90th percentile 12.4 s, maximum
21.4 s) and mean cost $0.0089, $0.8025 in all. By subset the mean cost is $0.0128 on T, $0.0141 on N
and $0.0036 on R. Each run cost less than the design estimate of $0.928.

## 5. Gate history

| Gate | Result | Where |
|---|---|---|
| G1 retrieval hit@5 of at least 90% on the 30 held-out test questions | **Passed 2026-11-02: 30 of 30** (hit@1 22 of 30); the test split was then spent for retrieval | D-20, D-21; [retrieval report](../phase04_retrieval/retrieval_report.md) |
| G2 adversarial guardrail pass | **Not met** (decision runs 1 to 4 all failed; no further fixes or runs, D-97, D-109, D-115). In decision run 4 all 603 rendered claims in the 3 repeats were supported by their cited text and every refusal and decline was safe; the run failed on one missing scope note (ADV4-009) and one retrieval miss (ADV4-044). Spend about $6.29 of $8.00 | D-115; [G2 results](../phase06_guardrails/g2_results_v4.md), Section 11 |
| Phase 7 exit check | **Passed on run 1** (run 2 not used); $0.2916 | D-132; [tools report](../phase07_tools/tools_report.md) |
| G3 end-to-end groundedness, citation accuracy, refusals and false refusals | **Passed 2026-11-12**: both runs meet (a) to (d) | D-149 (this report) |

G2 and G3 measure different things on different sets. G3 is a held-out end-to-end measurement of the
shipped system; G2 closed as not met and its failure classes are carried below.

## 6. Known limitations

**The main gap for users: personal income tax is out of scope (D-64).** The system does not cover
personal income tax, PAYE, an individual's tax bands and rates, or any section outside the v1 corpus
(for example Section 13 and Section 58 of the Nigeria Tax Act, and the Fourth Schedule). Those
questions are declined, which is the intended behaviour (R07 and R13 in this
evaluation), but a sole trader or an employee asking about their own tax gets no answer. Other taxes
outside the corpus are also declined: stamp duties, state and local levies, customs and excise,
petroleum taxes, subsidiary legislation and periods before 2026.

### 6.1 Phase 8's own

1. **The T set has a retrieval bias.** Every T question had a gold unit that retrieval was known to
   find (G1, 30 of 30), so T is easier than unseen questions on retrieval and false refusals. N is
   the unseen part: it had 1 false refusal in 20 and 19 of 20 hit@5 in each run.
2. **Zero tolerance on 40 R cases bounds the leak rate only to about 7.5%.** 0 of 40 answered gives a
   95% upper bound of 7.5% (Wilson 8.8%) on the true rate at which a must-refuse case is answered a
   run; 0 of 80 pooled gives 4.6%. A system answering 5% of such cases would pass one run by chance
   13% of the time. (c) is a check for systematic leaks, not a proof of a rate below 2%.
3. **The reviewer is a calibrated model session, not an independent person.** It was calibrated blind
   (60 of 60 mutations, 2 of 2 real unsupported claims, 1 of 131 supported claims over-flagged), and
   checked by an overseer session (48 of 48 sampled claims) and by Kene on 10 of the 180 case-runs.
   But a model reviewer can share blind spots with the model that generated the claims, and no
   independent person reviewed the claims. A 100% result is a statement about this reviewer's reading.
4. **N05 is a retrieval miss.** Section 163(1)(r) (profits of a company engaged in sporting
   activities) was not retrieved in either run, so a football-club question was declined. This is one
   of 50 questions a run; it is the only false refusal, and it is caused by retrieval, not by the
   guardrails.
5. **Q032 lists 2 of the 6 Thirteenth Schedule activities.** In both runs the answer states two of
   the six exempt agricultural activities and not the five-year exemption, after the verifier moved
   five claims to gaps. The shown claims are supported; the answer is incomplete.
6. **The N17 levy calculation was declined** because the calculator wanted gross turnover, although
   the question says the company is not a small company. The answer states the 4% rule but not the
   figure. The same input check applied in both runs.
7. **Incomplete answers.** Q009 and N11 are also incomplete (Section 3.2). Incompleteness is reported
   as answer coverage (89 of 94 key points, 45 of 49 answers complete) and is not a false refusal
   under (d).
8. **What G3 does not measure.** Scope-note recall (the labels require no notes, D-143), usefulness
   of the notes shown, latency and cost on a deployed service (Phase 12), tools off against tools on
   (D-140), and any question outside these 90 cases. The smoke run was accepted as incomplete (D-145;
   the pre-check path ran live in the 40 R cases of the final runs).
9. **Spent sets.** The test split and `e2e_v1` are now spent. A further measurement needs a fresh
   held-out set (D-133).

### 6.2 Carried from G2 (D-115)

- Subsidiary legislation named "Regulations" gets no scope note (ADV4-009).
- Exported-services questions miss Section 187 and the exported-service definition (ADV4-044).

### 6.3 Carried from Phase 6 (D-112 and earlier)

- True claims removed as bridging facts or misread (ADV-094, ADV2-025, ADV3-047, Q050, ADV-060;
  D-71, D-93, D-103, D-112).
- Kept gaps carrying accurate, passage-backed legal content (D-71): gaps have no citation field, so
  in this evaluation a gap that states law counts in (a) and is supported only when a shown passage
  states it (D-139). Run 1 had 1 such gap and run 2 had 4.
- The gap reference check binding a section to the wrong Act (D-85(1), ADV3-033).
- ADV-085's invented provision link and provision-link misattribution (D-89, D-90, D-103).
- ADV2-047's unverified claim and ADV2-018's carried condition (D-91, D-112).
- The D-57 limit held at 2 of 2 by tagger votes.
- Foreign-law wordings the pre-check misses (D-112).
- Retrieval misses behind ADV2-027, ADV2-028 and ADV2-046 (D-82).

### 6.4 Carried from Phase 7 (D-132; [tools report](../phase07_tools/tools_report.md), Section 5)

- A duplicate calculation call (E03).
- An extra lookup after a declined calculation (X2).
- The X1 claim 4 scope (Schedule 1 Part I paragraph 9 shown without "In this Part").
- The Q003 and Q025 claim-count watch (D-131). In this evaluation the claim count per answer was 4.8.
- Interest is never computed: the interest rates are not in the corpus, and the tool says so.
- A tool call can be skipped: the choice is automatic, and nothing forces a call.

## 7. How to reproduce

Everything in this report can be re-checked for free from committed files. Only a new evaluation
needs paid API calls.

1. Check the pinned inputs: `.venv\Scripts\python eval\e2e\run_e2e.py --status` (free; prints the pins,
   the freeze, the records and the ledger).
2. Re-score from the committed run and review files (free; no model call, no database). The scorer
   writes each result once and refuses to overwrite it, so do this in a copy of the repository with
   `e2e_run1_scores.json`, `e2e_run2_scores.json` and `g3_result.json` removed:

   ```
   .venv\Scripts\python eval\e2e\score_e2e.py --run 1
   .venv\Scripts\python eval\e2e\score_e2e.py --run 2
   .venv\Scripts\python eval\e2e\score_e2e.py --g3
   ```

   Expected: run 1 a 244/244, b 246/246, c failed 0/40, d 1/50; run 2 a 245/245, b 245/245, c failed
   0/40, d 1/50; G3 pass. The score files should match the sha256 values in D-149.
3. The records: `eval/e2e/results/e2e_run1.json`, `e2e_run2.json` (what the system showed), the review
   files `e2e_run1_review.json`, `e2e_run2_review.json` (every verdict with its reason), the scores
   and `g3_result.json`, `g3_check.json` (overseer and Kene checks) and the per-item
   [results CSV](../../eval/e2e/results/g3_results.csv).
4. A new run (paid, about $0.82 a run) needs a fresh results folder and a new held-out set, because
   this repository's runner refuses a second run once the markers exist and the sets are spent
   (D-133, D-135). The system's answers are not deterministic, so a new run gives close but not
   identical figures. Setup: `docker compose up -d`, the embedding and LLM keys in `.env`, and the
   corpus loaded as described in the README.

## 8. Files

| File | What it is |
|---|---|
| [evaluation_design.md](evaluation_design.md) | The design, approved before any measurement (D-133 to D-142). |
| [../../eval/e2e/results/g3_results.csv](../../eval/e2e/results/g3_results.csv) | One row per case per run and one row per shown item (claim, gap, computed item), with the reviewer's verdict. |
| [../../eval/e2e/results/g3_check.json](../../eval/e2e/results/g3_check.json) | The overseer's sample and close calls, and Kene's 10 cases. |
| [../../eval/e2e/results/g3_result.json](../../eval/e2e/results/g3_result.json) | The G3 verdict from the two run scores. |
| [../00_planning/decision_log.md](../00_planning/decision_log.md) | D-133 to D-149. |
