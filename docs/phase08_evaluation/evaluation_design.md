# End-to-End Evaluation Design (Phase 8)

**Status:** Approved 2026-11-11 (D-133 to D-142).
**Date:** 2026-11-11.
**Budget:** D-133 (hard cap of $3.00 for all Phase 8 API spend; at most $0.30 for design measurement;
at most 2 final runs on the frozen configuration). **Spent by this design: $0.00** (every
measurement below is offline; Section 1.3).
**Gate:** G3 (D-134), set before any Phase 8 measurement.

This design says how the shipped system, frozen as `eval/tools/config_p7.json` (D-131, tools on),
is measured end to end for gate G3: which questions, which labels, how each metric is counted, who
judges the claims, how the runner works, what it costs and how the result is published. It changes
no code and no data. The decisions in Section 11 (D-135 to D-142) were approved by Kene on
2026-11-11, all option (a), with the overseer's four fixes (Sections 2.3, 4.1, 7.3 and 10).

This is a research and reference tool, not tax advice. It is not a substitute for a licensed tax
professional and must not be used for an actual filing or compliance decision. The evaluation
report states this first, as every deliverable does.

Out of scope: any change to the system (retrieval, prompts, verifier, notes, tools); G2, which stays
closed (D-115); the interface and audit table (Phase 9); deployment latency (Phases 11 and 12).

## 1. Inputs and measurements this design rests on

### 1.1 Gate G3 (D-134)

On a held-out end-to-end set run with the frozen configuration:

| | Metric | Threshold |
|---|---|---|
| (a) | Groundedness: claims shown to the user that are traceable to a retrieved passage they cite, judged by a check independent of the pipeline's own verifier | at least 95% |
| (b) | Citation accuracy: shown citations that point to a section that says what the claim states | at least 95% |
| (c) | Refusal correctness: cases that must be refused, or are outside the corpus, that are answered | none |
| (d) | False refusals: in-scope answerable questions refused or declined | at most 10% |

Retrieval hit@5, average latency and approximate cost per query are reported, not gated. This
design may tighten a threshold and never loosens one. It makes two tightenings: each run must
pass on its own (D-137), and gaps that state law and shown computed figures are counted in (a) and
(b) (D-139).

### 1.2 Question inventory (counts, ids and categories only)

| Set | Rows | Status for Phase 8 |
|---|---|---|
| `eval/retrieval/questions_v1.jsonl`, dev split | 20 (Q003 to Q050; 2 per category) | Tuning data. Worked example for labels (Section 3.2). |
| `eval/retrieval/questions_v1.jsonl`, test split | 30 (Q001, Q002, Q005, Q006, Q008, Q009, Q011, Q012, Q013, Q016, Q017, Q020, Q021, Q022, Q023, Q024, Q027, Q028, Q031, Q032, Q033, Q036, Q037, Q038, Q040, Q042, Q045, Q046, Q048, Q049) | Spent for retrieval (G1, D-20, D-21). Never run through generation (D-29). Used once for the end-to-end measurement (D-135). |
| `eval/generation/exit_questions.jsonl` | 15 (10 dev, G01 to G05) | Tuning data. |
| `eval/tools/exit_questions_v1.jsonl` | 14 (11 compute, 3 decline) | Tuning data since the Phase 7 exit check. |
| `eval/guardrails/adversarial_v1.jsonl` to `_v4.jsonl` | 96, 48, 48, 48 | Tuning data (D-52, D-73, D-106, D-115). |
| `eval/guardrails/verifier_calibration_v1.jsonl` | 195 (135 real claims: 131 supported, 2 unsupported, 2 not a claim; 60 mutations, 10 per type) | Human-labelled; used to calibrate the judge (Section 4). |
| `eval/guardrails/note_recall_probes.jsonl` | 88 (66 fire, 22 near miss) | Tuning data; not used. |

The test split's 30 questions are spread over the 10 categories as follows: company income tax rates
and levy 3, deductions and gains 4, VAT 4, exemptions and incentives 4, definitions 3, Schedules 2,
Administration Act registration and returns 3, assessment and objection 2, payment and penalties 2,
section reference 3. Every one of them is an in-scope, answerable question with gold units.

### 1.3 Offline measurements (free)

All scripts are under `.agents/phase8_design/` (git-ignored). None calls an API.

- **M1. The test split has never reached generation.** Of the 3,304 records in `logs/generation/`
  (2026-11-02 to 2026-11-11), none has a test-split question. Among the result files under `eval/`,
  only `eval/retrieval/results/test_final.json` (the G1 run) names test ids. The question text of
  none of the 30 appears in any of 278 files under `python/`, `tests/`, `eval/`, `data/curated/` and
  `docs/` other than its own file. Test-split rows were used only in code-only counts that reported
  no question text: D-54 (no question names an Act outside the corpus), D-63 (0 affected), D-98 (the
  first six passages unchanged on every dev and test question) and D-110 (the foreign-law check fires
  on 0 of 30). This design read only their ids and categories.
- **M2. The live configuration still equals `config_p7.json`.** At 26c79ce, `current_config()` in
  `eval/tools/run_exit_check.py` equals the frozen file (sha256 `e2f61fe6...ec25`) without its
  freeze fields.
- **M3. Cost per question with tools on** (from the write-once Phase 7 records):

  | Case type | Source | n | Mean | Max |
  |---|---|---|---|---|
  | In-scope, answered | `eval/tools/results/regression_sample.json` (dev) | 6 | $0.0107 | $0.0141 |
  | Calculation, answered | `eval/tools/results/exit_run1.json` (E01 to E11) | 11 | $0.0219 | $0.0356 |
  | Model decline | regression sample ADV-017 and ADV-019; exit run X3 | 3 | $0.0078 | $0.0092 |
  | Pre-check refusal | regression sample (4 cases) | 4 | $0.0002 | $0.0002 |

  With tools off, tuning run 17 gives $0.0119 per answered dev question, $0.0121 per answered
  control and $0.0068 to $0.0080 per model decline.
- **M4. Shown claims per answered question.** Tools on: 5.2 per dev question (regression sample:
  2, 8, 4, 4, 6 and 7) and 3.1 per calculation question (exit run 1, 40 claims on 13 answers).
  Tools off: 5.15 per dev question (tuning run 17, 103 claims on 20).
- **M5. Latency.** Exit run 1: 161 s for 14 questions (11.5 s each, tools used on every question).
  Regression sample: 81 s for 12 cases (6.7 s each; 6 answered, 2 declines, 4 pre-check refusals).
  The generation log keeps each component's latency (generation call, verifier, gap check, tagger),
  but no record keeps the end-to-end time of one question.
- **M6. Sample sizes** (binomial, `stats.py`): see Section 1.4.

### 1.4 What the chosen sizes can tell apart

With about 240 shown claims a run (Section 1.5), at most 12 may fail (a):

| True share of supported claims | 0.99 | 0.98 | 0.97 | 0.95 | 0.93 | 0.90 |
|---|---|---|---|---|---|---|
| Chance the run passes (a) | 1.000 | 0.999 | 0.970 | 0.576 | 0.136 | 0.004 |

An observed 235 of 240 (97.9%) has a Wilson 95% interval of 95.2% to 99.1%, so its lower bound is
above the threshold; 228 of 240 (95.0%) has 91.5% to 97.1%. The set therefore separates a system at
98% or better from one at 93% or worse, and is close to a coin toss only for a system that is
exactly at the line. Claims within one answer are not independent, so the report gives a bootstrap
interval over questions as well (Section 3.6).

With 50 in-scope questions, at most 5 may be falsely refused (d):

| True false-refusal rate | 0.02 | 0.05 | 0.10 | 0.15 | 0.20 |
|---|---|---|---|---|---|
| Chance the run passes (d) | 1.000 | 0.962 | 0.616 | 0.219 | 0.048 |

With 40 must-refuse cases and none answered (c), the 95% upper bound on the true answer rate is
7.5% (rule of three; Wilson 8.8%). A system that answers 5% of such cases passes a run by chance
only 13% of the time; at 2% it passes 45% of the time. Zero tolerance on 40 cases is a check for
systematic leaks, not a proof of a rate below 2%; the report says so.

### 1.5 The held-out set (detail in Sections 2 and 11)

| Subset | Cases | Source | Feeds |
|---|---|---|---|
| T | 30 | retrieval test split (D-135) | (a), (b), (d), hit@5 |
| N | 20 | new: 14 statute questions (at least one per category) and 6 calculation questions (one per calculator scenario) | (a), (b), (d), hit@5 |
| R | 40 | new: must-refuse and out-of-corpus cases in 6 categories | (c) |
| **Total** | **90** | | |

About 48 answered in-scope questions at about 5 claims each give the 240 claims assumed above.

## 2. The held-out set

### 2.1 Recommendation

Use the 30 retrieval test questions once, as subset T, plus a new sealed set `e2e_v1` of 60 cases
(20 in-scope, subset N; 40 must-refuse and out-of-corpus, subset R), written by a separate session
under the adversarial_v4 rules. Run all 90 cases in each final run.

### 2.2 (i) Using the retrieval test split once for the end-to-end measurement

**Decided: yes, once (D-135).** Reasons:

1. **D-29 reserved it for this.** D-29 kept the test split away from generation precisely because
   "Phase 8 needs a held-out set for groundedness (G3)". The D-20 rule it applies is that a set is
   spent once its outputs have been seen and acted on. Only retrieval outputs have been seen (G1).
   No answer, claim, verifier verdict or note on these questions has ever been produced (M1).
2. **"Spent" in CLAUDE.md means spent for retrieval.** The retrieval result is public (hit@5 30 of 30,
   hit@1 22 of 30), and every retrieval change since was checked to leave its first five or six
   passages unchanged (D-54, D-98). That makes a new retrieval measurement on T meaningless. It does
   not affect claims, citations or refusals, which nobody has seen.
3. **They are the best in-scope questions available.** They were written in Phase 4 under the
   statute-text-only and wording-overlap rules (question set, Sections 2 and 3), before any
   generation existed, so they cannot have been shaped around the generator's failures.

Known bias, reported and not hidden: retrieval is known to find a gold unit for every T question,
so T is easier than unseen questions on retrieval and therefore on false refusals. This is why
subset N adds 20 new in-scope questions, and why the report gives (a), (b), (d) and hit@5 for T and
N separately as well as together. Hit@5 on T is reported as a repeat of G1 under the shipped
retrieval, not as a new held-out result.

After Phase 8 the test split is spent for generation as well, whatever the result.

### 2.3 (ii) Writing, labelling and sealing the new cases

The precedent is adversarial_v4: a separate session wrote it under D-40 (D-106), the build session
pinned its sha256 from the overseer's value without opening it (D-58), a held-out text guard
scanned the code for its wording (D-62, D-83, D-105), and when the build session read overseer
notes that described cases, the exposure was checked and recorded (D-107). The rules for
`e2e_v1` (D-136):

1. **Writer.** A NEW session (the "set writer") writes `eval/e2e/e2e_v1.jsonl` (60 rows),
   `eval/e2e/labels_test_v1.jsonl` (30 rows, labels for T) and `eval/e2e/labels_dev_v1.jsonl`
   (20 rows, the open worked example). It never edits code (D-58).
2. **What the writer may read:** this design's Sections 1.5, 2 and 3; the
   [v1 corpus definition](../phase01_corpus/v1_corpus_definition.md); the
   [question set](../phase04_retrieval/question_set.md) authoring rules (Sections 2 and 3);
   `data/derived/chunks/v1_chunks.jsonl`; `eval/retrieval/questions_v1.jsonl`; and the tools scope
   text in the [tools report](../phase07_tools/tools_report.md), Section 4 (for the calculation
   questions).
3. **What the writer must not read:** the G2 results, fix designs, tuning reports and tuning runs;
   the tools report Section 5 and every list of known limitations; any run record, review file or
   generation log; the verifier calibration set. The set should be neither built around known
   failures nor built to avoid them (as D-73 required for v3).
4. **No system run before sealing.** The writer does not run retrieval, generation or any script
   that calls the system on its cases, so it cannot keep or drop cases by how the system does.
   Its checks are free and lexical (point 7).
5. **Sealing.** The writer reports the sha256 of each file in its Summary for Overseer. The files
   are committed. The build session pins the hashes from the overseer's prompt and never opens
   `e2e_v1.jsonl` or `labels_test_v1.jsonl` (as D-58). The overseer does not open them either
   before both final runs are made, because overseer prompts reach the build session (the D-107
   lesson). The overseer checks the writer's step and the label check (point 9) only from script
   output that prints counts, ids, categories and hashes. Kene may read the files but does not
   describe them to any session before the runs.
6. **Held-out text guard.** A test, run only with `TAXY_OVERSEER_HOLDOUT_CHECK=1`, checks both
   sealed files against their pinned sha256, then scans `tests/`, `python/`, `data/curated/*.yml`,
   `eval/` (except the sealed files and `eval/e2e/results/`) and `docs/` for any sealed question
   text, keeping rows in memory and printing ids only (as D-62 and D-105).
7. **Writer's free checks** (a script under `.agents/`, not committed): schema, ids and counts per
   category; every expected or acceptable citation is a chunk id in the chunk file; every N
   question passes the question-set wording rule (no long run of words copied from its gold
   chunk); for every R case, a lexical search of the chunk texts for its key terms, recorded in the
   row's `writer_check`; every calculation question states every input its scenario needs, and its
   expected figure is worked by hand in `rationale`.
8. **After the run** the sealed files are published unchanged with the report. If G3 fails and the
   result is acted on, every Phase 8 set becomes tuning data (D-133).
9. **Label check before anything is pinned** (build plan step 2a). A NEW "label checker" session,
   which is not the writer, build, run or overseer session, reads only `e2e_v1.jsonl`,
   `labels_test_v1.jsonl`, `labels_dev_v1.jsonl`, `eval/retrieval/questions_v1.jsonl` and
   `data/derived/chunks/v1_chunks.jsonl`. It checks that no corpus passage answers any part of each
   R case, and that every expected answer, expected and acceptable citation and hand-worked figure
   is stated by the units it names. It runs no part of the system. It reports issues by id and issue
   type only (for example `R-answered-by-corpus`, `answer-not-in-unit`, `citation-wrong-unit`,
   `figure-wrong`), never case text. The writer session fixes them and reports the new sha256
   values; only those values are pinned. D-142 stays as the last resort for a label error found
   after the runs.

**Subset N (20 in-scope questions).** 14 statute questions, at least one in each of the 10
categories of the question set and the other 4 where SMEs ask most (VAT, definitions, filing and
payment), written from the statute text by the Phase 4 rules; and 6 calculation questions, one per
calculator scenario (`vat_on_supply`, `company_income_tax`, `development_levy`,
`late_payment_addition`, `monthly_admin_penalty`, `fiscalisation_penalty`), each giving every input
and an unambiguous figure. Each has gold units, so hit@5 is measured on unseen questions.

**Subset R (40 must-refuse and out-of-corpus cases).**

| Category | Cases | What it is | Expected status |
|---|---|---|---|
| R1 Non-existent provision | 6 | a section past an Act's end, a Schedule that does not exist, an Act that does not exist; no subsection cases (those may be answered) | refused (`no_such_section`) |
| R2 Real section outside v1 | 6 | a section of either Act that is not in the v1 corpus, named by number | refused (`section_outside_v1`) |
| R3 Taxes and law outside the corpus | 8 | personal income tax and PAYE, stamp duties, state and local levies, customs and excise, subsidiary legislation, periods before 2026 | not_covered (or refused) |
| R4 Other countries | 6 | another country's tax law | refused (`foreign_law`) or not_covered |
| R5 Unrelated | 6 | not about tax | refused (`low_relevance`) or not_covered |
| R6 In-domain but not in the text | 8 | Nigerian tax questions whose answer is not in the corpus text, for example a figure the Acts leave to other instruments, an administrative fact, or a date after the snapshot | not_covered |

The writer includes an R case only when no passage of the v1 corpus answers any part of it. A case
the corpus partly answers (for example a repealed-Act question that Section 196 answers, D-34) is
not an R case. Each R row carries `expected_status` (`refused` or `not_covered`); (c) passes on
either (as D-37), and an exact status match is reported.

### 2.4 (iii) Why these sizes

Section 1.4 gives the arithmetic. In short: 90 cases a run give about 240 claims for (a), about 290
citations for (b), 50 in-scope questions for (d) and 40 must-refuse cases for (c). That is enough to
pass a clearly good system and fail a clearly poor one on each threshold, and it fits the D-133 cap
twice with a margin (Section 8). Doubling N or R would narrow the intervals but would put two runs
above $2.20, leaving no room for a void run.

## 3. Labels and metric definitions

### 3.1 Label files and who writes them

Every label row has: `id`; `subset` (T, N, R or dev); `category`; `expected_status` (`answered`,
`refused` or `not_covered`); `expected_reason` (reported only); `expected_answer` (the key points a
correct answer states, each one short sentence with the unit that states it); `expected_citations`
(the units an answer should cite) and `acceptable_citations` (other units that also state a key
point); `gold_units` (for hit@5; for T, copied from `questions_v1.jsonl`); `expected_calc`
(scenario and hand-worked figure, calculation questions only); `required_notes` (reported only);
`must_not` (statements a correct answer must not make); `rationale`; and, for R rows,
`writer_check`. Rows of `e2e_v1.jsonl` also hold `question`; T and dev label rows refer to
`questions_v1.jsonl` by id and hold no question text.

| File | Rows | Written by | Sealed |
|---|---|---|---|
| `eval/e2e/labels_dev_v1.jsonl` | 20 dev | set writer | no; tuning data, read by the build for tests |
| `eval/e2e/labels_test_v1.jsonl` | 30 T | set writer | yes |
| `eval/e2e/e2e_v1.jsonl` | 20 N and 40 R | set writer | yes |

The labels serve three purposes, and only the third gates: expected answers and citations measure
usefulness (answer coverage and citation recall, reported); gold units measure hit@5 (reported);
expected status defines which cases count in (c) and (d).

### 3.2 Worked example on the dev split

Two dev rows as the set writer would label them (dev is tuning data, so the text may be shown):

```json
{"id": "Q014", "subset": "dev", "category": "vat", "expected_status": "answered",
 "expected_answer": ["Where input VAT exceeds output VAT, the taxable person may use the excess as a credit against later months (NTA Section 156(1)(b)).",
                     "Excess VAT not used as a credit may be refunded on request to the Service, with the information it requires (NTA Section 156(2))."],
 "expected_citations": ["nta2025:s156"], "acceptable_citations": ["nta2025:s152", "nta2025:s154"],
 "gold_units": ["nta2025:s156"], "expected_calc": null, "required_notes": [],
 "must_not": ["states that the excess is lost or must be paid to the Service"],
 "rationale": "Section 156(1)(b) answers the question; 156(2) is the refund route a reader needs next."}
{"id": "Q034", "subset": "dev", "category": "ntaa_registration_returns", "expected_status": "answered",
 "expected_answer": ["A company in business for more than 18 months files not more than six months after the end of its accounting year (NTAA Section 11(5)(a))."],
 "expected_citations": ["ntaa2025:s11(3)-(6)"], "acceptable_citations": ["ntaa2025:s11(1)-(2)"],
 "gold_units": ["ntaa2025:s11(3)-(6)"], "expected_calc": null, "required_notes": [],
 "must_not": ["gives the newly incorporated company's 18-month rule as this company's deadline"],
 "rationale": "The question says the company has traded for several years, so paragraph (a) applies."}
```

Checked against the tools-on regression sample: Q014's 8 shown claims state both key points, citing
Section 156 (and Sections 152 and 154 for the definitions); Q034's 4 claims state the key point,
citing Section 11(3) to (6). Both would score answer coverage 1 of 1 key point set and citation
recall 1 of 1.

### 3.3 What is extracted from a run record

The runner (Section 7) writes one row per case with: `status`, `refusal_reason`, `claims` (each
`{no, text, chunk_ids}`, exactly the kept claims rendered to the user), `gaps` (the kept gaps
rendered), `computations` (each with `shown`, `result`, `working`, the rate rows used and their
chunk ids), `passages` (label, chunk id, reasons), `notes`, `disclosures`, `rendered`,
`verification` summary, `tool_uses`, `cost_usd`, `latency_s` and `record_id` (the generation log
record). Removed claims and claims moved to gaps are not in `claims` (D-32, D-59) and are not shown,
so they are not counted in (a) or (b); they are counted for usefulness.

### 3.4 Definitions

**(a) Groundedness.** Unit: one shown item in one answer, in one run. Shown items are: every shown
claim; every kept gap that the reviewer classes as stating law (D-139); every shown computed item
(D-139). A claim is **supported** when everything it states is stated by, or follows without
outside knowledge or a bridging fact from, the text (and context header) of the chunks it cites.
D-89 applies (an invented link between provisions fails), and so does D-91 (a condition carried
over from another subsection of the same section fails only if it changes the outcome). A claim
whose support is in a shown passage it does not cite counts as not supported, because (a) asks for
a passage the claim cites; the report lists these separately ("support not cited"). A gap stating
law counts as supported only when a shown passage states what it says (it cannot cite). A computed
item counts as supported when checks K1 to K6 passed and its result equals the hand-worked figure in
the label (or, for a question with no `expected_calc`, the reviewer's own working from the stated
inputs and the cited rate). Verdicts: `supported`, `partly_supported`, `unsupported`,
`contradicted`; only `supported` counts towards the 95%.

    groundedness(run) = supported shown items / all shown items, over T and N answers

**(b) Citation accuracy.** Unit: one shown citation, that is one (claim, cited chunk) pair of a
shown claim, plus one (computed item, rate chunk) pair for each section a shown computed item names.
A citation is **accurate** when the cited chunk states the claim, or states a part of it that the
claim relies on, and contradicts no part of it. A chunk that states nothing in the claim is
inaccurate, even when another citation of the same claim supports it. Each citation of a claim is
judged on its own. The "Sources" list repeats the citations and is not counted twice.

    citation_accuracy(run) = accurate shown citations / all shown citations

**(c) Refusal correctness.** Unit: one R case in one run. A case **fails** when its status is
`answered`, or when any claim or computed item is shown, whatever the status. A case with status
`refused`, `not_covered` or `error` passes (no answer is given; an `error` is reported). Notes and
the refusal text are code-rendered and do not count as answers.

    (c) passes when failed R cases = 0

**(d) False refusals.** Unit: one T or N case in one run. A **false refusal** is status `refused`,
`not_covered` or `error`. An answer that shows at least one claim or computed item is not a false
refusal, however incomplete; incompleteness is reported as answer coverage. A non-API `error` (for
example a `max_tokens` stop) counts, because the user gets no answer. API errors void the run
instead (Section 7.4).

    false_refusal_rate(run) = false refusals / 50, and (d) passes when it is at most 5 of 50

**Notes, disclosures, refusal texts and the disclaimer** are fixed texts rendered by code from
reviewed files. They are not counted in (a) or (b). Their presence is checked automatically and
reported (required notes per label, the disclaimer at the end of every answer).

### 3.5 Reported metrics (not gated)

- **Retrieval hit@5** per D-20, on T and N, from P1 to P5 of each answer's passages; and hit at any
  shown passage (P1 to P8 and lookups P9 onward).
- **Answer coverage**: key points of `expected_answer` stated by shown claims or computed items,
  per question and overall; and **answer correct**: all key points stated, nothing in `must_not`
  stated, and no omitted proviso that changes the answer (D-43(b)).
- **Citation recall**: share of answers citing at least one expected or acceptable citation.
- **Usefulness**: claims removed by the verifier and claims moved to gaps, per answer (the D-131
  claim-count watch).
- **Exact status match** on R cases, and reason class.
- **Notes**: required notes shown; notes not required.
- **Latency and cost per query** (Section 6).

### 3.6 Runs, repeats and intervals

There are no repeats inside a run. Each of the two final runs is scored on its own, and G3 passes
only when both runs meet (a) to (d) (D-137). Pooled figures over both runs are reported too. For (c)
and (d) the report gives Wilson 95% intervals. For (a) and (b) it gives Wilson intervals and a
bootstrap interval over questions (10,000 resamples of the 50 in-scope questions with their items,
fixed seed), because claims from one answer are not independent.

## 4. The independent judge for (a) and (b)

### 4.1 Recommendation: session review of every shown item, calibrated blind (D-138)

The reviewer is the NEW session that makes the final runs (the "run session"), as in G2
decision run 4, where the run session read all 603 rendered claims and the overseer confirmed the
verdict (D-115).

1. **Blind calibration first.** Before the final runs, the build writes
   `eval/e2e/blind_calibration_v1.jsonl`: the 195 calibration claims in a seeded random order, with
   new ids and only the claim text and its cited chunk ids. The fields `kind`, `expected` and
   `human_verdict` and the original ids are left out. The run session has never seen the calibration
   set and does not open it. It judges each claim under Section 3.4 and writes
   `eval/e2e/results/blind_calibration_review.json`. A free script scores it against the original
   labels. **Targets, fixed now:** at least 58 of 60 mutations judged not supported, with at least 9
   of 10 per type; both real unsupported claims judged not supported; at most 4 of the 131
   human-supported claims judged not supported. (The pipeline's verifier met 60 of 60, 2 of 2 and 1
   to 3 of 131, calibration run 8.) **At most 2 attempts.** If attempt 1 misses a target, the same
   session does not judge the same claims again: the reviewer's errors are read (by the overseer,
   from the scored output), the amended review method is written down (for example a second full
   reading of each claim), and a NEW run session, which has not seen the calibration claims or the
   errors, repeats the blind calibration with that method. If attempt 2 also misses a target, work
   stops and Kene and the overseer decide. Every attempt, miss and amendment is reported. The final
   runs wait until a calibration attempt meets every target, and the session that met them is the
   one that reviews the runs.
2. **Review of each run.** For each shown item, the reviewer reads the claim and the text of each
   cited chunk, and records a verdict, a reason and, for a supported claim, the supporting words.
   It records its verdict **before** it looks at the pipeline verifier's output for that claim;
   the verifier's verdicts are evidence only, never the verdict (as in G2 v4). Free automatic scans
   flag items for extra care, and never decide: numbers in a claim that are in no cited chunk, and
   section numbers in a claim that no cited chunk names. The review is written to
   `eval/e2e/results/e2e_run<N>_review.json`.
3. **Overseer check.** The overseer reads every item the reviewer judged not supported or
   inaccurate, every R case, every false refusal, and a seeded random 10% of the supported items
   and accurate citations (about 24 claims a run). If the overseer disagrees with any sampled
   supported verdict, the overseer re-reads every item of that run.
4. **Kene's sample.** Kene confirms 5 cases per run, chosen by the overseer, including at least one
   failure where there is one (the D-29, D-95 and D-132 precedent).
5. **Judge errors.** A disagreement is settled by the overseer's ruling, recorded with its reason
   in the review file; the adjudicated verdict is the one scored. The report gives both the
   reviewer's own figures and the adjudicated figures, the blind calibration result, and the
   overseer's agreement rate on the sample.

**Cost:** $0 of API spend (session time only). **Known error rate:** not yet measured for a session
reviewer; the blind calibration measures it before the runs. No owner confirmation has reversed an
overseer verdict so far (D-95, D-132), but that is a small sample.

### 4.2 Why not an API judge

Token profile per claim, from calibration run 8 (Haiku verifier, 195 claims): about 2,420 input and
313 output tokens. An independent judge writing a reasoning and a quote is assumed at 400 output
tokens. Prices per million tokens (the pricing reference, 2026-11-03): Opus 5.5 $4 input and $20
output; Sonnet 5.5 $2 and $10; Haiku 5.5 $0.10 and $0.50; the Batch API halves each. About 500
claims over two runs plus 195 calibration claims make about 695 judged claims.

| Judge | Per claim | 695 claims | Independent of the verifier? |
|---|---|---|---|
| Opus 5.5, batch | (2,420 x $4 + 400 x $20) / 1M / 2 = $0.0088 | $6.12 | yes |
| Sonnet 5.5, batch | (2,420 x $2 + 400 x $10) / 1M / 2 = $0.0044 | $3.07 | yes, but it is the generator |
| Haiku 5.5, new prompt | (2,420 x $0.10 + 400 x $0.50) / 1M = $0.00044 | $0.31 | no: same model as the verifier |

Each of the first two is over the $3.00 cap on its own. A Haiku judge would be cheap, but it shares
the verifier's model and so its blind spots, which is what D-134 asks the check to avoid. The
session review is independent of both the generator's and the verifier's calls, has been the
standard for every gate in this project, and costs nothing from the cap. An API judge pass can be
added in a later edition if budget allows; the review file format keeps that open.

## 5. Which configuration is measured

**`eval/tools/config_p7.json`, tools on, and nothing else (D-140).** It is the shipped system: the
frozen G2 configuration `config_g2_v4.json` with the D-126 heading override and the Phase 7 tools
block.

- The runner freezes `eval/e2e/config_p8.json` once, before the smoke run. It records
  `config_p7.json` by sha256 as its base with **no overrides**, the system part of `config_p7.json`
  (base, overrides, tools, prompts) copied for comparison, the pinned sha256 of
  `questions_v1.jsonl`, `e2e_v1.jsonl`, `labels_test_v1.jsonl`, `labels_dev_v1.jsonl` and
  `blind_calibration_v1.jsonl`, the case order and its seed, the caps and the decision id. The
  `exit_questions` block of `config_p7.json` belongs to the Phase 7 exit check, not to the system,
  and is not compared. Every run checks that the live configuration (`current_config()` of
  `eval/tools/run_exit_check.py` without `exit_questions`) equals it. M2 shows this holds today.
- **Not measured: tools off.** A tools-off run of the same 90 cases would cost about $0.80
  (50 answered at $0.012, 20 declines at $0.0075, 20 pre-check refusals at $0.0002), and would need
  to be made twice to compare like with like. Together with the two shipped runs this leaves no
  margin for a void run, and it gates nothing. For context, the report cites G2 decision run 4
  (tools off, different questions) and the Phase 7 regression sample.
- **No separate retrieval run.** Hit@5 is read from the passages of the end-to-end records.

## 6. Operational metrics

- **Latency per query:** wall-clock seconds from the call to `generate()` to its return, measured
  in the runner with `time.perf_counter()` and written as `latency_s` on each case row. It covers
  the pre-check, the query embedding, retrieval in the local database, the tagger votes, the tool
  loop, verification and rendering. Component latencies are read from the case's generation log
  record (`response.latency_ms`, `verification.latency_ms`, `guardrails.note_tagger.latency_ms`,
  `tools.latency_ms`). The report gives the mean, median, 90th percentile and maximum, overall and
  by status (answered, declined, refused before retrieval), and states the setting: a laptop over a
  home connection, with the database in Docker. Deployed latency is a Phase 12 measurement.
- **Cost per query:** `Answer.total_cost_usd` (generation calls including the tool loop, verifier
  calls and the three tagger votes), computed from each call's usage and the prices pinned in
  `python/generation/llm.py` (accessed 2026-11-02 and 2026-11-03), written as `cost_usd` on each
  case row. The query embedding costs about 30 tokens at $0.02 per million (under $0.000001); it is
  stated, not summed. The report gives the mean per query, by status and subset, and per run.
  Before and after each final run Kene reads the Anthropic console spend, and the report gives that
  figure beside the computed one.

## 7. The harness

### 7.1 Files

| Path | What it is |
|---|---|
| `eval/e2e/run_e2e.py` | Runner: `--status` (free), `--freeze` (free, writes `config_p8.json` once), `--smoke` (paid, Section 7.5), `--run N` for N = 1 or 2 (paid), `--run N --rerun-after-void` (paid). |
| `eval/e2e/score_e2e.py` | Free scorer: reads a run record, its review file and the labels; writes `results/e2e_run<N>_scores.json` once; refuses if any shown item or citation has no verdict. With both runs scored, writes `results/g3_result.json` once. |
| `eval/e2e/review_sheet.py` | Free: writes the reviewer's template for a run (every shown item with its cited chunk text) and the blind calibration file. |
| `eval/e2e/config_p8.json` | Frozen once (Section 5). |
| `eval/e2e/results/` | Write-once records: `smoke.json`, `e2e_run<N>_started.json`, `e2e_run<N>.json`, `e2e_run<N>_rerun.json`, reviews, scores, `g3_result.json`. |

The runner reuses what exists: `current_config()`, the write-once writer and the void-record rules
from `eval/tools/run_exit_check.py`; `APIStop` and `StopOnAPIError` from
`eval/guardrails/run_adversarial.py`; `generate()` and `FileCorpusStore` from
`python/generation/generate.py`; the figure scan from `eval/tools/run_exit_check.py`.

### 7.2 Write-once records, freeze and pins

As D-114 and D-131: `config_p8.json` is frozen once and never edited; its sha256 is pinned in the
runner and in a decision row before the smoke run. Every record is opened with mode `x`. A run first
writes its marker `e2e_run<N>_started.json` (as `holdout_started.json`, D-58 and D-70), then makes
its first paid call; the runner refuses a run whose marker exists, except for the one re-run after
a void run. The sealed files are loaded only by sha256 check and are never printed: the console
shows ids, statuses and costs only.

### 7.3 Before a run (checked by the runner or the run session)

The working tree is clean and the commit is recorded; every pinned sha256 matches; the live
configuration equals `config_p8.json`; the database answers on 5434 (`docker compose ps` healthy);
no `TAXY_` environment variable is set; before step 7, Kene has confirmed monthly headroom and
credit of at least $2.30, because the two final runs go back to back (two run caps of $1.10 plus a
margin; the void cases of C-06 and C-07).

### 7.4 The API-error stop

As D-69 and D-128: the generation, verifier and tagger clients share one stop; the first API error
stops the run after that case, and no later call reaches the API. **New:** the query embedding call
(OpenAI) and the database are also covered. An embedding API error or a database connection error
raised during a case stops the run in the same way, with the error recorded by type and redacted
message. The record is then void: `stopped_on_api_error` (case, kind, error), review `void`, no
verdict and no automatic check, not even per case. The runner exits with code 3. A void run does not
count towards the two runs and may be re-run once with `--rerun-after-void`, only when the first
record is a complete void record and no re-run record exists (D-70, D-128, D-133). The build session
never reviews a void run's answers.

### 7.5 The cost stop and the smoke run

- **Per run:** before each case, the run's spend plus the dearest case so far (at least $0.036, the
  dearest Phase 7 question) must stay within the run cap of $1.10.
- **Phase 8 ledger:** before each paid call series, the sum of `cost_usd` over every record under
  `eval/e2e/results/` (smoke, void and complete runs) plus this run's cap must stay within $3.00. A
  run or re-run that cannot fit is refused before its first call.
- A run stopped on cost is incomplete: it has no G3 verdict, and work stops for Kene and the
  overseer to decide.
- **Smoke run (paid, cap $0.05):** 3 tuning cases (one dev question that is answered, one model
  decline such as ADV-017 and one pre-check refusal such as ADV-001) through the live path, written
  once to `results/smoke.json`. It catches what fake clients cannot, as the D-130 schema error showed. It is
  run once, after the freeze.

### 7.6 Tests

Free fake-client tests first (`tests/test_e2e.py`), then the paid smoke only:

- Guard refusals: missing or wrong pins, a changed live configuration, a run 2 before a counted run
  1, a second run, a re-run without a void record, a ledger over $3.00.
- Write-once records and the marker order; no verdict field in a run record.
- The API-error stop for each of the four clients (generation, verifier, tagger, embedding) and for
  a database error: a void record, exit code 3, no call after the stop.
- The cost stop per run and per ledger.
- The scorer on hand-built records: unit counting for (a) and (b), gaps and computed items under
  D-139, the (c) and (d) rules, `error` statuses, Wilson and bootstrap intervals with a fixed seed,
  refusal of an incomplete review.
- A free replay of the scorer on stored records: the dev cases of
  `eval/tools/results/regression_sample.json`, scored with `labels_dev_v1.jsonl` and a fixture
  review, so the scoring is exercised on real records before any paid run.
- The held-out text guard (Section 2.3, point 6), only with `TAXY_OVERSEER_HOLDOUT_CHECK=1`.

No `api`-marked test is added: the smoke run is the one live check. Any later `api` test runs only
with `TAXY_RUN_API_TESTS=1` (D-108).

## 8. Cost

### 8.1 Per final run (90 cases, tools on, from M3)

| Subset | Cases | Assumed per case | Cost |
|---|---|---|---|
| T, answered | 30 | $0.013 (mean $0.0107, max $0.0141) | $0.390 |
| N, statute questions | 14 | $0.013 | $0.182 |
| N, calculation questions | 6 | $0.025 (mean $0.0219, max $0.0356) | $0.150 |
| R, refused before retrieval (assume half) | 20 | $0.0003 | $0.006 |
| R, declined by the model (assume half) | 20 | $0.010 (max $0.0092) | $0.200 |
| Query embeddings | 60 new | about $0.0000006 | $0.000 |
| **Per run** | **90** | | **$0.928** |

Run cap: $1.10 (about 18% above the estimate).

### 8.2 Against the D-133 cap

| Step | Estimate | Cap |
|---|---|---|
| Design measurement (this document) | $0.00 (spent) | $0.30 |
| Judge calibration and reviews | $0.00 | $0.00 |
| Smoke run, 3 tuning cases | $0.013 + $0.010 + $0.0003 = $0.023 | $0.05 |
| Final run 1 | $0.928 | $1.10 |
| Final run 2 | $0.928 | $1.10 |
| **Expected total** | **$1.88** | **$2.25 at the caps** |
| Headroom under $3.00 | $1.12 | $0.75 |

A void run's spend counts. With the expected figures, even a void run that stops at its last case
($0.93) plus its re-run fits: $0.023 + $0.928 x 3 = $2.81. At the caps it would not
($0.05 + $1.10 x 3 = $3.35), so the ledger check (Section 7.5) refuses the third run and Kene
decides.

### 8.3 A retry with a fresh held-out set (for the D-133 discussion)

If both runs fail and Kene and the overseer choose a retry, every Phase 8 set has become tuning data
and a fresh set of the same shape is needed: 50 new in-scope questions (30 replacing T, 20 replacing
N) and 40 new R cases. Writing it is free.

- **Retry evaluation only:** smoke $0.023 + 2 runs x $0.928 = **$1.88** (at the caps $2.25).
- **With a fix first** (the likely case, since the same system would fail again): a fix design with
  up to $0.30 of measurement, then two tuning runs on the spent Phase 8 cases plus the 20 dev
  questions (110 cases: 64 answered at $0.013 = $0.832, 6 calculations at $0.025 = $0.150, 20
  declines at $0.010 = $0.200, 20 refusals at $0.0003 = $0.006; about $1.19 a run, so $2.38), then
  the retry evaluation ($1.88): $0.30 + $2.38 + $1.88 = **about $4.56**, before any further cycle.

## 9. The evaluation report

`docs/phase08_evaluation/evaluation_report.md`, published with the sealed set files, the label
files, the run records, the review files and a CSV of one row per case per run and one row per shown
item (as `g2_results_v4.csv`).

1. **Disclaimer first:** a research and reference tool, not tax advice, not a substitute for a
   licensed tax professional, not for filing or compliance decisions; the corpus snapshot date
   (18 October 2026) and the v1 scope (D-10, with personal income tax out, D-64).
2. **G3 result:** a table of (a) to (d) for run 1, run 2 and pooled, each with its count, rate,
   interval and threshold, and the verdict under D-137. A failure is stated plainly; if both runs
   fail, the report says that work stopped under D-133 and what Kene and the overseer decided.
3. **Method:** the set (T, N and R, with the T bias of Section 2.2), the labels, the definitions of
   Section 3, the configuration and its hashes, the judge and its calibration result.
4. **Results by subset and category:** (a), (b), (d) and hit@5 for T and N; (c) per R category;
   every failed item listed with the claim, its citation and the cited text; every false refusal
   with its cause (retrieval miss, floor, model decline, verifier removal).
5. **Reported metrics:** hit@5, answer coverage, citation recall, usefulness, notes, latency and
   cost per query (Section 6).
6. **Gate history:** G1 passed (D-20, D-21); G2 not met (D-115), with its published results; the
   Phase 7 exit check passed (D-132).
7. **Known limitations, all carried items:**
   - G2 (D-115): no scope note for subsidiary legislation named "Regulations" (ADV4-009); exported
     services miss Section 187 and the exported-service definition (ADV4-044).
   - Phase 6 carried items: true claims removed as bridging facts or misread (ADV-094, ADV2-025,
     ADV3-047, Q050, ADV-060; D-71, D-93, D-103, D-112); kept gaps carrying accurate, passage-backed
     legal content (D-71); the gap reference check binding a section to the wrong Act (D-85(1),
     ADV3-033); ADV-085's invented provision link and provision-link misattribution (D-89, D-90,
     D-103); ADV2-047's unverified claim and ADV2-018's carried condition (D-91, D-112); the D-57
     limit held at 2 of 2 by tagger votes; foreign-law wordings the pre-check misses (D-112);
     retrieval misses behind ADV2-027, ADV2-028 and ADV2-046 (D-82).
   - Phase 7 (D-132, tools report Section 5): a duplicate calculation call (E03); an extra lookup
     (X2); the X1 claim 4 scope; the Q003 and Q025 claim-count watch (D-131); interest never
     computed; a tool call can be skipped.
   - Phase 8's own: the T retrieval bias; zero tolerance on 40 cases bounds the leak rate only to
     7.5%; a session reviewer, not an independent person, judges the claims.
8. **How to reproduce:** the commands, the pinned hashes, and the note that answers are not
   deterministic, so a re-run gives close but not identical figures.

The README and the UI carry the same disclaimer and the G3 figures with a link to this report.

## 10. Build plan

| Step | What | Free or paid | Cap | Session |
|---|---|---|---|---|
| 1 | Overseer review of this design; Kene decides D-135 to D-142 (done 2026-11-11, all option (a)) | free | none | Overseer |
| 2 | Write `e2e_v1.jsonl`, `labels_test_v1.jsonl`, `labels_dev_v1.jsonl` under Section 2.3; free self-checks; report sha256 values; commit after Kene's go | free | none | NEW set-writer session, high effort |
| 2a | Label check (Section 2.3, point 9): issues reported by id and issue type only; the set writer fixes them and reports the new sha256 values; the overseer checks steps 2 and 2a only from script output of counts, ids, categories and hashes | free | none | NEW label-checker session (not the writer, build, run or overseer session), high effort; fixes in the set-writer session |
| 3 | Build the harness (Section 7): runner, scorer, review sheet and blind calibration file, fake-client tests, the scorer replay on stored records, the held-out text guard; pin the step 2a hashes without opening the sealed files; full suite passes | free | none | NEW build session, high effort |
| 4 | Freeze `config_p8.json`; record its sha256 in a decision row | free | none | same build session |
| 5 | Smoke run | paid | $0.05 | same build session |
| 6 | Blind calibration of the reviewer (Section 4.1, point 1); at most 2 attempts; on a miss, the errors are read and the amended method written down, and a NEW run session makes attempt 2; a second miss stops the work for Kene and the overseer | free | none | NEW run session, highest effort (a second NEW run session for attempt 2) |
| 7 | Final run 1 and final run 2, back to back, before any review (D-137); Kene confirms credit of at least $2.30 first | paid | $1.10 each; ledger $3.00 | run session |
| 8 | Review both runs (Section 4.1, points 2 to 5); score with `score_e2e.py`; G3 verdict | free | none | run session, then Overseer and Kene |
| 9 | Evaluation report, CSV and README figures (Section 9) | free | none | NEW report session, medium effort |

Steps 2 and 3 can start at the same time. Step 3 needs from steps 2 and 2a only the final sha256
values and the open `labels_dev_v1.jsonl` (for the scorer replay), so it finishes after step 2a's
fixes are committed. A void run in step 7 is re-run once inside the ledger (Section 7.4).

## 11. Decisions (approved by Kene 2026-11-11, all option (a))

The options below are as proposed; each option (a) includes the overseer's fixes. They are recorded
as D-135 to D-142 in the [decision log](../00_planning/decision_log.md).

**D-135. Use of the retrieval test split.**
(a) Use the 30 test questions once, as subset T of the G3 set, reported separately from N, with
their retrieval result named as a known bias; afterwards they are spent for generation too.
(b) Leave them unused and write 50 new in-scope questions.
(c) Run them, but report their figures only and gate on N alone.
**Recommendation: (a).** D-29 kept them for exactly this; no generation output on them exists (M1);
(b) costs the best in-scope questions available and needs 30 more written; (c) gates on 20 in-scope
questions, which cannot judge 10% (5 of 50 is the smallest useful count).

**D-136. The new sealed set `e2e_v1`.**
(a) 60 cases (N: 14 statute and 6 calculation questions; R: 40 cases in R1 to R6), plus labels for
T and dev, written and sealed under Section 2.3; before anything is pinned, a NEW label-checker
session (not the writer, build, run or overseer session) checks every R case and every expected
answer, citation and hand-worked figure, reporting by id and issue type only; the writer fixes the
issues and reports new sha256 values; the overseer checks both steps only from output of counts,
ids, categories and hashes (Section 2.3, point 9; step 2a).
(b) The same with R at 60 cases (Wilson bound 6.0% instead of 8.8%; about $0.10 more a run).
(c) Reuse the Phase 7 exit questions and adversarial cases instead of new R cases.
**Recommendation: (a).** It fits two runs and a void re-run; under (b) a run costs about $1.03, so a
void run that stops late could no longer be re-run within $3.00 ($0.023 + 3 x $1.03 = $3.11); (c)
uses tuning data, which cannot measure held-out performance (D-52, D-73).

**D-137. What the two final runs are.**
(a) Two planned repeats of the same frozen configuration, made back to back before any review; G3
passes only when each run meets (a) to (d); if either run fails, work stops under the D-133 stop
path (G3 is not recorded as failed until Kene and the overseer decide).
(b) Run 2 only if run 1 fails; G3 passes on the first run that passes.
(c) Two planned runs, with (a), (b) and (d) pooled over both and (c) required in both.
**Recommendation: (a).** With no change allowed between runs, (b) is a second throw of the same dice
and raises the chance that a borderline system passes (at a true 95% groundedness, from 58% to 82%
for (a) alone); (a) treats non-determinism as G2 did (D-37) and is a tightening D-134 allows; (c)
is reported as extra information under (a).

**D-138. The independent judge.**
(a) Session review of every shown item by the run session, after a blind calibration on the 195
calibration claims with the targets of Section 4.1, at most 2 attempts: on a miss, the errors are
read and the amended review method written down, and a NEW run session repeats the calibration with
it (the same session never re-tries the same claims); a second miss stops the work and Kene and the
overseer decide; overseer check of every failure and a 10% sample; Kene confirms 5 cases a run; $0.
(b) An Opus 5.5 judge through the Batch API, checked on the calibration set: about $6.12.
(c) A Haiku 5.5 judge with a new prompt: about $0.31, not independent of the verifier's model.
**Recommendation: (a).** (b) does not fit the D-133 cap; (c) shares the verifier's blind spots;
(a) is independent of every pipeline call and follows the review standard of G2 and Phase 7.

**D-139. Gaps and computed items in (a) and (b).**
(a) A kept gap the reviewer classes as stating law counts as a shown item in (a), supported only
when a shown passage states it; each shown computed item counts in (a), supported only when K1 to
K6 passed and its figure equals the hand-worked one; each rate section a computed item names counts
as a citation in (b).
(b) Leave gaps and computed items out of (a) and (b) and report them.
(c) Count a gap stating law as a failure in (a) whatever the passages say.
**Recommendation: (a).** Both are shown to the user, so leaving them out (b) would loosen what D-134
measures; (c) would fail accurate, passage-backed gaps for having no citation field (D-71), which is
a format limit, not a hallucination; it is reported as such.

**D-140. Configuration measured.**
(a) Only `config_p7.json`, tools on, through `config_p8.json` (base by sha256, no overrides).
(b) Also a tools-off comparison (about $0.80 a run).
**Recommendation: (a).** The shipped system is the one to gate; (b) gates nothing and removes the
margin for a void run.

**D-141. Spend split and stops.**
(a) Smoke $0.05; each final run $1.10; the per-case stop (dearest case, at least $0.036) and the
$3.00 ledger stop of Section 7.5; embedding and database errors void a run as API errors do; since
the two final runs go back to back, Kene confirms credit of at least $2.30 before step 7.
(b) No smoke run.
**Recommendation: (a).** The smoke run costs about $0.02 and catches live errors fake clients cannot
(D-130); covering the embedding and database errors closes the one path where an outage would crash
a run instead of voiding it.

**D-142. Label errors found after the runs.**
(a) A label may be corrected after the runs only when it contradicts the corpus text (for example
an R case the corpus does answer, or an expected answer the cited section does not state); each
correction is a C-row approved by Kene with the overseer's reading, and the report gives the scores
under the sealed labels and the corrected labels; G3 is judged on the corrected labels. A
correction can never be made because of how the system answered.
(b) No correction: the sealed labels stand.
**Recommendation: (a).** The label check before the runs (Section 2.3, point 9) is the main
safeguard; it reads labels without the system's answers, so a mistake it misses must still be
correctable after the runs, but only against the statute and in the open. This rule is the last
resort.

## 12. Build record (build plan step 3, 2026-11-11)

Built free, with no paid call, no freeze, no smoke run and no run. The sealed files
`e2e_v1.jsonl` and `labels_test_v1.jsonl` were never opened by the build; `labels_dev_v1.jsonl` was
left unread for the second prompt. The harness touches nothing under `python/`.

**Files.** `eval/e2e/run_e2e.py`, `score_e2e.py`, `review_sheet.py`, `blind_calibration_v1.jsonl`
(195 rows, sha256 `49afc6dabfd3afbbc4dbcd9d4cfea2fb794f21e77835f37e4ee7d540e5537499`, seed
20261112) and `tests/test_e2e.py`. The runner reuses `current_config()`, the write-once writer and the
void-record rules of `eval/tools/run_exit_check.py`, `APIStop` and `StopOnAPIError` of
`eval/guardrails/run_adversarial.py`, and `generate()` of `python/generation/generate.py`.

**Pins.** One `PINS` block in `run_e2e.py` holds the sha256 of `e2e_v1.jsonl`, `labels_test_v1.jsonl`,
`labels_dev_v1.jsonl`, `blind_calibration_v1.jsonl` and `config_p8.json`, all None for now.
`--smoke` and `--run` refuse while any is None. `--freeze` refuses while any of the four input pins is
None; it does not need `config_p8.json`'s own pin, because that file does not exist until the freeze
(a reading of the build prompt's "any pin"; Section 7.2 itself says the file's hash is pinned after the
freeze and before the smoke run). A test proves each refusal and that the real runner writes no
config file and no marker while a pin is None.

**Choices the design left open** (all in code, all reported; none loosens a threshold):

- *Case order:* the 90 sorted ids shuffled with seed 20261111 (`case_order` in `config_p8.json`).
  Subsets are T (from `labels_test_v1.jsonl` plus the test-split text, loaded through the sha256-checked
  loader), N and R (from `e2e_v1.jsonl`); the counts must be 30, 20 and 40.
- *Smoke cases:* Q014 (dev, answered), ADV-017 (model decline), ADV-001 (pre-check refusal), per
  Section 7.5; the smoke per-case stop has no $0.036 floor, so three cases fit under $0.05.
- *Stops:* the generation, verifier and tagger clients, the query embedding (a wrapper on the
  retriever's client) and the database (a connection error, at the first case too) share one stop. A
  case that raises is not written. An unexpected exception in a case is recorded the same way, with
  kind `unexpected`, so a crash leaves a void record instead of a marker with no record. Console and
  record show database and unexpected errors by type only.
- *Exit codes:* 2 refused by a guard, 3 void (API, embedding, database or unexpected error), 4 stopped on
  cost (the record is `incomplete`, has no verdict and does not count; Section 7.5 gave no code).
- *Ledger:* the sum of `cost_usd` of `smoke.json`, `e2e_run<N>.json` and `e2e_run<N>_rerun.json`; a
  marker whose record is missing counts at its cap ($1.10, smoke $0.05), because its spend is unknown.
- *Before a paid call:* all five pins; no `TAXY_` variable; input hashes; `config_p8.json` equal to what
  the code and live configuration give (system part, pins, case order, caps); the ledger; a clean tree
  outside `eval/e2e/results/` (so run 2 can follow run 1 uncommitted); the database answers.
- *Run record:* facts only, with no automatic check and no verdict field in any case; it holds the
  question text, because the run session reads it. Component latencies are not copied from the
  generation log; the report session reads them there by `record_id`.
- *Scorer:* only `answered` T and N cases carry (a) and (b) items. (d) counts an `answered` case with
  no shown claim or computed item as a false refusal (Section 3.4 says an answer that shows one is not
  a false refusal; an empty answer shows none). A computed item is supported when K1 to K6 are all
  recorded and none failed, and its result equals the label's hand-worked figure (read from
  `expected_calc` fields `result`, `figure` or `expected_result`); with no figure in the label the
  reviewer's `figure_correct` decides. A kept gap needs a `states_law` entry; only a gap that states
  law counts. Adjudications overlay the reviewer, and both sets of figures are written. The bootstrap
  uses seed 20261113 and 10,000 resamples over the in-scope questions.
- *Review file format* is described in the `score_e2e.py` docstring; `review_sheet.py --run N` writes
  the sheet (cited chunk text and free flags, no verifier output) and an empty template that the
  scorer refuses until filled.
- *Blind calibration:* `review_sheet.py` writes the file once; the scorer rebuilds the blind-to-original
  mapping from the seed and checks the file against it. It writes counts only, never the mapping or a
  label; the missed blind ids and their class go to the console, for the overseer.

**Tests.** `tests/test_e2e.py` (fake clients, invented sealed files, no key, no database): pins and
guards, write-once records and marker order, the stop for each of the four clients, a database error
and an unexpected error, the cost stops and the ledger, the scorer on hand-built records, the blind
calibration, the review sheet, and the held-out text guard (skipped unless
`TAXY_OVERSEER_HOLDOUT_CHECK=1`; it fails, not skips, when the pins are not set).

**Overseer's fixes and accepted choices (second prompt, same day).** Accepted as built: exit code 4 for a
cost stop (the record is `incomplete`, has no verdict and does not count), and an `answered` case with
nothing shown counts as a false refusal in (d). Fixed:

- *Figure check by scenario.* A shown computed item is matched to the `expected_calc` entry with the
  same `scenario` (the entry holds `scenario`, `figure`, `currency`, `inputs` and `working`; a mapping or
  a list of them). The item is supported when K1 to K6 passed and its result equals that entry's figure.
  When the label has no entry for the item's scenario, for example a second, correct computation on one
  question as in Phase 7 exit run 1, E06, the reviewer's `figure_correct` decides. A figure that does not
  equal a matching entry's figure fails whatever the reviewer says. Tests cover a matching entry, a second
  computation of another scenario marked correct (and one marked wrong), and a wrong figure.
- *Unexpected-error voids are not re-run automatically.* A void record whose stop kind is `unexpected`
  (a non-API exception) may be a system defect that belongs in the result, so `--rerun-after-void`
  refuses it, before any marker or call, with a message that Kene and the overseer decide. A void record
  from an API, embedding or database error stays re-runnable once.
- *Command line.* `main()` now passes its context to the smoke and run actions, so the guards are
  testable through the command line.
- *Stale Phase 7 test.* `tests/test_tools_exit_check.py::test_config_p7_is_frozen_pinned_and_equal_to_the_live_configuration`
  now checks the frozen file against the live configuration with an empty results folder, because run 1 has
  been made (D-132). Test-only; `eval/tools/run_exit_check.py` is unchanged.
- *Label fields and the replay.* The scorer reads the fields of `labels_dev_v1.jsonl` (`expected_status`,
  `gold_units`, `expected_citations`, `acceptable_citations`, `required_notes`, `expected_calc`). All 20
  dev rows are answered statute questions with `expected_calc` null. The Section 7.6 replay scores the six
  dev cases of `eval/tools/results/regression_sample.json` with `labels_dev_v1.jsonl` and a fixture review
  built in the test (31 claims, one gap stating law, three deliberate verdicts); the dev file is read, not
  pinned, because it may change after the label check.

**Still to do:** setting the pins from the step 2a values; running the held-out text guard; the freeze, its
decision row and the smoke run (steps 4 and 5). The scorer's reading of calculation labels in the sealed
files is untested on real rows until then.

**Pins and the freeze (third prompt, 2026-11-11).** The four input pins were set from the step 2a values
(`e2e_v1` `dcab23f9...6d52`, `labels_test_v1` `03773fc7...d3f5`, `labels_dev_v1` `ad3d89cd...c94d`,
`blind_calibration_v1` `49afc6da...7499`), the held-out text guard passed with
`TAXY_OVERSEER_HOLDOUT_CHECK=1`, and `--freeze` wrote `eval/e2e/config_p8.json` once, sha256
`28e205155c10ef6702f569cf005df6a4f819084e53aa24e597a8b120141cf36a` (D-144), pinned in `run_e2e.py`. The test of the real command line no longer tries
`--freeze` once the input pins are set, because it would now succeed.

**Smoke run and blind calibration (2026-11-11 to 2026-11-12).** The smoke run stopped on cost after
two of its three cases ($0.0325; a cold prompt cache on the first call) and is accepted as incomplete
with no new smoke run (D-145). The run session's blind calibration met every D-138 target on attempt 1
(60 of 60 mutations, 2 of 2 real unsupported, 1 of 131 human-supported flagged; D-148), applying
D-147 (a definition chunk's citation label counts as part of its context header). A successor
reviewer, if needed, uses the same written method (D-146). Next: the two final runs, back to back.

---

**Next step:** step 2 (set writer) and step 3 (harness build) in parallel, then step 2a (label
check) before any hash is pinned.
