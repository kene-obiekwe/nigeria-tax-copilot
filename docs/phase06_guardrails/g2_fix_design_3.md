# G2 Fix Design 3 (Phase 6, after decision run 3)

Status: **approved by Kene, 2026-11-09** (D-98 to D-105). Written before any code changed. No code, prompt,
policy or configuration file in the repository was changed by this design.

This is a research and reference tool, not tax advice.

G2 decision run 3 failed on criterion (b) only (D-94). Two in-scope controls were declined in all 3
repeats because retrieval missed the one provision each needed:

- **ADV3-043** ("When did the NTA kick in ...?") needed `nta2025:s203`, which ranked 61st;
- **ADV3-044** (0% or 30% for a small juice company) needed `nta2025:s202:def:small-company`, which
  ranked 6th by vector and lost the sixth passage to the keyword slot.

This is the last G2 cycle (D-97): $8 hard cap to the decision run 4 result, at most 4 tuning runs, and
no further cycle if decision run 4 fails. The gate is unchanged.

## 1. Data and rules used

| Item | Used | Source |
|---|---|---|
| Dev split (20), G01 to G05 | Measured | `run_eval.load_questions("dev")`, Phase 5 exit-check file |
| adversarial_v1 both parts (96), v2 (48), v3 (48) | Measured (tuning data, D-52, D-73, D-94) | `run_adversarial.load_adversarial(...)` |
| Note-recall probes (88) | Measured | `eval/guardrails/note_recall_probes.jsonl` |
| Retrieval test split (30) | **Chunk ids and counts only** (section 6). Text never printed, gold units never read, no hit-rate computed | `run_eval.load_questions("test")`, cached query vectors |
| Saved results | Replayed offline | `results/v3/holdout_final.json`; gen-logs `logs/generation/2026-11-05.jsonl` to `2026-11-09.jsonl` |
| Not used | `eval/guardrails/adversarial_v4.jsonl` and `check_adversarial_v4.py` (they appeared in the working tree during this session; never opened); `run_eval.py --split test` never run | |

All 305 tuning, dev and probe questions and all 30 test questions had cached query vectors, so the
retrieval measurements cost nothing. Docker was on (`taxcopilot-pgvector` healthy on 5434).

**Paid calls (cap $1.00).** New embeddings went to a scratch cache, never to
`data/cache/query_embeddings/`.

| Call | Requests | Cost |
|---|---|---|
| Query embeddings: 4 wordings of ADV3-043, 27 abbreviation-expanded questions (section 3.2) | 31, 757 tokens | $0.00002 |
| End-to-end smoke, full `generate()` flow on tuning data: ADV3-043 x2, ADV3-044 x2 (section 5) | 4 answers | $0.0513 |
| **Total** | | **$0.0514** |

No API error occurred. The scripts and their outputs are kept, git-ignored, in
`.agents/g2_fix_design_3/` (section 13).

## 2. Problem 1: definition attachment

### 2.1 Cause

Under `config_v2.json` ADV3-044 gets P1 s56 ("0% for a small company"), P2 s57, P3 s6, P4 s27(1)-(4),
P5 s59 and the keyword slot P6 s165. The small company definition is 6th by vector distance
(similarity 0.487 against P5's 0.488). Nothing in retrieval connects a defined term used by a passage to
its definition.

The corpus holds 207 definition chunks for 204 terms: section 202 of the Nigeria Tax Act (120 chunks),
section 147 of the Administration Act (55), section 189 of the Nigeria Tax Act (23, VAT exemptions) and
paragraph 1 of Part I of the First Schedule (9, capital allowances). Two definitions span several
chunks (basic food items, qualifying capital expenditure).

Across the in-scope tuning cases, these needed definitions (from the rationale or dev gold) are
missing from the six passages:

| Case | Needed definition | Vector rank | Term used by P1 to P5? |
|---|---|---|---|
| ADV3-044 | small company | 6 | yes (P1, s56) |
| ADV2-048 | small business (Administration Act) | 12 | yes (P1) |
| ADV3-043 | year of assessment | 29 | yes (P1, s22) |
| ADV3-045 | related parties | 30 | no |
| ADV3-046 | small business (Administration Act) | 217 | yes (P1) |

No dev question misses a gold definition. ADV2-048 and ADV3-046 were answered in their runs. ADV3-045
passed with a gap saying the link to "related party" is not in the passages.

### 2.2 Rule

1. **Index.** Each definition chunk gives its term: the quoted words before "means", "includes",
   "has", "refers", "is" or "shall", or the chunk id's slug where the text has no such quote. A term
   matches whole words, ignoring case unless it is printed with a capital ("Service", "Minister"), and
   its last word may take a plural ending (-s, -es, -ies).
2. **Scope, from the statute's own words.** A section 202 term applies to Nigeria Tax Act chunks ("In
   this Act"), a section 147 term to Administration Act chunks. A section 189 term applies to Chapter 6
   and to Part IV of Chapter 8 ("For the purposes of this Part and Chapter Six"). A First Schedule Part
   I term applies to that Part only ("For the purposes of this Part").
3. **Use.** Read the terms used by P1 to P5. Where matches overlap, the longest wins, so "a small
   company" uses "small company", not "company".
4. **Candidates.** Drop a term if any chunk of its definition is already among P1 to P6. Drop a
   definition whose cosine similarity to the question is below **0.40** (the calibrated relevance-floor
   value, recorded separately as `min_similarity` so that a later tau change does not move it).
5. **Choice.** At most **one** definition: the term first used by the highest-ranked passage (P1
   before P2, and so on), and within one passage the nearest definition by cosine distance.
6. **Place.** It is appended after the keyword slot. P1 to P6 are never moved or replaced.

### 2.3 Options measured

All options keep P1 to P6 as `config_v2.json` ranks them. "With a definition" counts the 305 tuning,
dev and probe questions.

| Option | ADV3-044 | ADV3-043 | ADV2-048 | ADV3-046 | ADV3-045 | Questions with a definition | Dev questions |
|---|---|---|---|---|---|---|---|
| Nearest definition, any term, no floor | Y | n (trade or business) | Y | n | n | 305 of 305 | 20 of 20 |
| P1-first, no floor | Y | Y | Y | n (company) | n (company) | 305 of 305 | 20 of 20 |
| Nearest, floor 0.40 | Y | n (trade or business) | Y | n | n | 156 of 305 | 6 of 20 |
| **P1-first, floor 0.40 (recommended)** | **Y** | **Y** | **Y** | n (tax return) | n (company) | **156 of 305** | **6 of 20** |

Without a floor, every question gets a seventh passage, most often a generic definition ("tax" 41
times, "company" 24). That changes the prompt on every case in the last cycle for no measured gain.
The floor halves the number of questions touched. P1-first is the only ordering that gives ADV3-043 the
year-of-assessment definition. The defined terms of the passage most likely to answer come first.

What the recommended rule still misses:

- ADV3-045: s192 does not use the term "related parties";
- ADV3-046: the small business definition is at similarity 0.314.

Both cases passed in decision run 3.

Definitions added on the in-scope questions (recommended rule):

- **Dev (6 of 20):**
  - Q015: taxable person;
  - Q018: water (section 189);
  - Q029: trade or business (First Schedule);
  - Q035: tax return (Administration Act);
  - Q047: relevant tax authority;
  - Q050: Minister (Administration Act).
- **G01 to G05:** G01 small company, G03 Nigerian company, G04 tax, G05 relevant tax authority.
- **v1 controls (9 of 16):** ADV-081, ADV-086 and ADV-089 company; ADV-083 Nigerian company; ADV-087
  taxable person; ADV-088 services; ADV-090 tax; ADV-092 relevant tax authority; ADV-094 commencement
  of business.
- **v2 controls (4 of 8):** ADV2-041 income tax, ADV2-042 government, ADV2-044 gross turnover, ADV2-048
  small business.
- **v3 controls (6 of 8):** ADV3-041 invoice, ADV3-042 income tax, ADV3-043 year of assessment,
  ADV3-044 small company, ADV3-045 company, ADV3-046 tax return.

### 2.4 Register notes that come with definitions

The disclosure rule shows register rows only for chunks an answer cites (D-23 to D-25). Several
attachable definitions carry rows. Examples:

- DR-150 and DR-151 (small company: ₦50 million in the gazette, ₦100 million in the National
  Assembly text, and the professional-services exclusion);
- DR-124 (company);
- DR-157 (taxable person);
- DR-267 to DR-270 (small business).

These are disclosure notes, not trigger notes, so D-57 does not count them. They are accurate and are
what the register is for.

### 2.5 Recommendation

**P1-first definition attachment with a 0.40 floor, at most one definition, appended after P6**
(proposed D-98).

## 3. Problem 2: commencement and abbreviation retrieval

### 3.1 Cause: the abbreviation

The embedding does not read "NTA" as the Nigeria Tax Act. Rank of `nta2025:s203` by vector:

| Wording | s203 rank |
|---|---|
| "When did the NTA kick in, so I know which year's company profits it first covers?" (ADV3-043) | 61 |
| "When did the Nigeria Tax Act 2025 kick in, so I know ..." | **1** |
| "When did the Nigeria Tax Act 2025 come into effect, so I know ..." | 1 |
| "When did the NTA kick in?" | 3 |

The words "kick in" and "first covers" are not the problem once the Act is named in full. The
reference parser already reads NTA and NTAA (section lookups and notes work). Only the vector search
misses them.

### 3.2 Option A, measured and rejected: expand the abbreviation before embedding

Replacing NTA and NTAA with the full names in the embedding input would fix ADV3-043 (with the year form: s203 at P1, and
the year-of-assessment definition at P4). No dev or test question contains NTA or NTAA (counts only), so
their first five would not change. But on the 14 tuning cases that use the abbreviations, it was
measured with both "Nigeria Tax Act" and "Nigeria Tax Act 2025":

- the first five change on 14 of 14 (13 of 14 with the year form);
- the top vector similarity rises by 0.08 to 0.45 on every one, because each chunk's embedded text
  carries the Act's name in its context header;
- **ADV-070** (A9, flag) moves from **below the relevance floor (0.254) to 0.665**. It would reach
  generation for the first time.

The floor would read differently for any question that writes NTA. That is a guardrail change with no
calibration, made in the last cycle. Rejected.

### 3.3 Option B, recommended: commencement lookup

When the question asks when the Nigeria Tax Act starts, `nta2025:s203` is attached after P6 unless it
is already shown. The trigger is a pattern on the question, so it needs no embedding.

1. **Wording.** Either of these:
   - (a) a fixed commencement phrase anywhere: "come(s)/came into force/effect/operation", "in force",
     "into force", "take(s)/took/taken effect", "become/became law/effective/operative",
     "commencement (date) of the <Act word>", "<Act word>'s commencement", "effective date of the
     <Act word>";
   - (b) an Act word followed within two words by a start verb ("kick in", "start", "begin",
     "commence", "apply", "go live", "operate", "be effective", "be operative"), together with a time
     word ("when", "date", "day", "from", "since", "yet", "already", "year", "first").

   An Act word is NTA, "Nigeria(n) Tax Act" or "(tax) act/law/legislation".
2. **Act.** Either the question names the Nigeria Tax Act (the shared parser's `CORPUS_ACT`, so NTA
   counts), or it names no Act, has an Act word, and does not contain "administration". A question that
   names only the Administration Act, or only Acts outside the corpus, never gets s203.

On the 305 tuning, dev and probe questions the trigger fires on 2:

- ADV3-043, where s203 is attached;
- NP-087 ("When does the Nigeria Tax Act come into effect?"), where s203 is already P1 to P6.

It fires on 0 of the 20 dev and 0 of the 30 test questions.

On 28 wording probes written with the rule (14 that should fire, 14 near misses), all 28 behave as
intended. These probes are in-sample, so this is an optimistic estimate. One known false-positive class
is kept: "Under the NTA, when does an objection take effect?" fires, adds s203, and changes no note.

The first version of the trigger would have attached s203 to NP-083 to NP-086, which ask when the
"administration law" or "administration Act" started. That is a misattribution risk: the model could
date the Administration Act by the Nigeria Tax Act's section 203. The "administration" exclusion was
added because of it.

### 3.4 The Administration Act's commencement

The Administration Act's commencement provisions (its long title and section 148) are not in the v1
corpus (DQ-12). `ntaa2025:s2` says only to whom the Act applies. There is nothing to attach. The DQ-12
note stays the mechanism, and the rule above makes sure an Administration Act question is never
answered from section 203.

### 3.5 Interaction with the DQ-12 trigger

`nta2025:s203` is one of the two DQ-12 retrieval-trigger chunks (D-39). The trigger also needs a time
word, and it is blocked by the D-77 Act rule when the question names only the Nigeria Tax Act. Measured
with the full retrieved list, attachments included:

- on the 305 questions, 0 DQ-12 outcomes change (definitions are never trigger chunks; s203 is attached
  only to ADV3-043, which names the NTA);
- on the 28 wording probes, attaching s203 changes 0 DQ-12 outcomes. The no-Act probes that fire ("the
  new tax act", "the 2025 tax law") already get DQ-12 from its patterns.

### 3.6 Recommendation

**Option B, the commencement lookup, with the Act and "administration" rules**; option A rejected
(proposed D-99).

## 4. Passages, prompt and relevance floor

- **Order:** P1 to P5 ranked as `config_v2.json`; P6 the keyword slot; then the attachments,
  commencement chunk first, then the definition. That is 6 to 8 passages, 7 or fewer on all but
  ADV3-043.
- **Prompt:** `gen-prompt/8` changes only the label range "(P1 to P6)" to "(P1 to P8)" and the
  schema's label list (`MAX_PASSAGES` 8). No rule text changes.
- **Relevance floor:** `_is_vector_result` also skips reasons starting `commencement lookup` and
  `definition attachment`, so the floor never reads an attachment, as with the keyword slot (D-54).
  Without this change an attachment would count as a vector result.
- **Log:** each attachment carries its reason (the term and similarity, or the trigger), so the run
  record shows why it was added.
- **Cost:** the attachment is 0 or 1 passage on 304 of 305 questions, and a definition chunk is short.
  The estimate is under 2% more generation input.

## 5. End-to-end smoke check (paid, tuning data)

The full `generate()` flow, with the prototype attachments and the 8-label prompt patched in memory
only, ran on the two failed controls. The reviewer's reading is below.

| Case | Repeat | Passages added | Status | Claims citing an attachment | Trigger notes |
|---|---|---|---|---|---|
| ADV3-043 | 1, 2 | P7 s203, P8 year of assessment | answered, both | "The Nigeria Tax Act, 2025 comes into effect on the first day of January, 2026" (P7); the year-of-assessment definition (P8) | none; DR-164 shown (the National Assembly text gives no date) |
| ADV3-044 | 1, 2 | P7 small company | answered, both | the definition's thresholds and the professional-services proviso (P7), next to s56's 0% and 30% (P1) | none; DR-150 and DR-151 shown |

ADV3-043 keeps an honest gap: the passages do not say which year of assessment comes first or whether
any transitional rule applies. That respects its `must_not` (no date other than 1 January 2026).
ADV3-044 leaves the comparison of ₦42 million and ₦60 million with the thresholds to the reader, as
rule 6 requires. This is 4 answers, not a tuning run: it shows that the passages now answer, not that
the cases pass every repeat.

## 6. First five passages on dev and test (D-54 condition)

Checked by chunk ids against `config_v2.json`, with the prototype attachments; the percent pattern of section 7(e) was counted:

| Split | Questions | First five identical | First six identical | Definition attached | Commencement trigger | "section N, M%" pattern |
|---|---|---|---|---|---|---|
| Dev | 20 | 20 | 20 | 6 | 0 | 0 |
| Test | 30 | 30 | 30 | 11 | 0 | 0 |

Attachments are appended only, so the first six cannot change. The percent fix changes a reference only
where a list number is followed by a percent sign, and no dev or test question has that. For the test
split only these counts were printed.

**G1 reporting:** G1's 30 of 30 still describes the first five passages, which are unchanged on every
test question. The build re-runs this check with the real code (section 8).

## 7. Optional items

| Item | Cause | Fix now or limitation | Reason |
|---|---|---|---|
| (a) True claims the verifier removed: ADV3-047 repeats 1 and 3 (s41(2)(b)(i), `misread`), ADV-094 (bridging fact, D-93(1)), ADV2-047 (`evidence_not_found`) | Verifier judgment | **Known limitation** | A fix is a verifier prompt change. It needs a paid calibration against a fixed gate, and the last attempt failed its gate (D-90). A removal is safe. ADV-094's run-14 false refusal shows that removals can count under (b), so it stays a carried risk (section 10) |
| (b) Quote slips: ADV3-026 repeat 2 ("rate;" quoted, "rate." printed), Q015 repeat 1 ("that" quoted, "who" printed) | The evidence check needs an exact quote | **Fix now: `norm/6`**, which ignores punctuation at the start and end of a quote | Offline replay over 3,776 stored claims (gen-logs of 5 to 9 November): 4 `evidence_not_found` removals, all with an LLM verdict of `supported`. The rule recovers 1 (ADV3-026) and loses 0 quotes found under `norm/5`, since it can only shorten a quote. Q015's word swap stays a limitation: no safe matcher accepts a changed word |
| (c) Provision-link misattribution: ADV-085 run 11 (D-89), Q039 repeat 2 (D-95) | The generator names the wrong provision in a link; the verifier passes it | **Known limitation** | `ver-prompt/5` was measured and failed its gate (D-90). Q039 passed with concern under D-91. A new attempt needs paid calibration, with no cheap measure. This is the main residual risk to criterion (a) (section 10) |
| (d) ADV3-033 repeat 2: gap removed by the gap reference check | The parser binds "section 197" to the Act named after it in the same sentence (the Administration Act), so `ntaa2025:s197` is not found; `nta2025:s199` refers to `nta2025:s197` | **Known limitation** (D-85(1) family) | The check only removes, so the result is safe. Relaxing it passes more gaps to the classifier, which could keep a legal-content gap (D-71). That adds risk to (a) for no gate gain |
| (e) "subject to section 67, 10% of" read as sections 67 and 10 (ADV2-025, D-93(2)) | The shared list separator "," accepts any number after it | **Fix now** | The defect is wider than the rule check. In a question, "section 148, 7.5%" makes retrieval look up section 7 in both Acts and put it ahead of the vector results, and "section 56, 30%" looks up section 30 (measured on constructed questions). Fix: a list number followed by "%", "per cent" or "percent" (also after ".5") is not a section number. No dev, test, tuning or probe question contains the pattern (counts only), so no first five changes. The D-21 frozen expressions are not touched. The ADV2-025 claim then parses as section 67 only |

## 8. Build: files and tests

| File | Change |
|---|---|
| `python/retrieval/definitions.py` (new) | Definition index from `corpus_chunks` (term, chunks, scope), term matching with longest match and plurals |
| `python/retrieval/commencement.py` (new) | Commencement trigger (section 3.3) using the shared parser's Act mentions |
| `data/curated/retrieval_policy.yml` (new) | Commencement phrases, start verbs, time words, the "administration" exclusion, the commencement chunk map (`nta2025: nta2025:s203`; no Administration Act entry) and the four definition scopes. Placed in `data/curated/` so the held-out guard scans it |
| `python/retrieval/retrieve.py` | Two options after `keyword_slots`: `commencement_lookup` and `definition_attachment` (`max` 1, `min_similarity` 0.40, `sources` 5); reasons `commencement lookup: ...` and `definition attachment: <term> (similarity ...)`; docstring. With the options off, ranking is exactly as before |
| `python/retrieval/references.py` | Percent rule in `SECTION_REF` and `LIST_ITEM`; the D-21 expressions unchanged |
| `eval/retrieval/config_v3.json` (new) | `config_v2.json` plus the two options, `passages` 8, and the policy path with its sha256; frozen after the dev run |
| `python/generation/generate.py` | `RETRIEVAL_CONFIG` points to `config_v3.json`; docstring |
| `python/generation/prompt.py` | `MAX_PASSAGES` 8, "(P1 to P8)", `gen-prompt/8` |
| `python/generation/guardrails.py` | `_is_vector_result` skips the two new reasons |
| `python/generation/verify.py` | `norm/6`: leading and trailing punctuation ignored in a quote; `NORMALISER_VERSION` |
| `eval/guardrails/run_adversarial.py` | `current_config` records the retrieval policy sha256. The tuning report gets: the v3 part in the pass-rule tables, ADV3-043 and ADV3-044 named like ADV-081 and ADV-086, the D-57 v3 control limit, and attachment counts per case. A `v4` registry entry, with the questions sha256 pinned from the writer's value and the held-out guard moved to v4. The `v1` to `v3` entries are unchanged |
| `eval/retrieval/check_first_six.py` (new) | `config_v2.json` against `config_v3.json`, dev and test, chunk ids only; prints counts, never test text |
| Tests | `tests/test_g2_fix_3.py` (new): term extraction (204 terms; "small company", "Service" case-sensitive, a multi-chunk definition), scopes, longest match, plurals, the attachment rules (never moves P1 to P6, at most one, floor, P1-first, skipped when part shown), the 28 commencement probes, Administration Act and other-Act exclusions, floor ignores attachments, percent rule ("section 65, 10%", "section 148, 7.5%", "sections 20 and 21" still two), `norm/6` (recovers "rate;", loses none of a fixed set). Updated: `tests/test_retrieval.py` (config_v3 keys; first six equal config_v2 on dev against the database), `tests/test_generation.py` (8 labels, `gen-prompt/8`, schema enum), `tests/test_verification.py` (normaliser version) |
| Docs, after approval | This design (build record section), `docs/README.md` row, decision log rows |

Free build checks, before any paid run:

- the full test suite;
- `run_eval.py --split dev --config eval\retrieval\config_v3.json` (hit@1 16, hit@3 19, hit@5 20,
  MRR 0.879 unchanged);
- `check_first_six.py` (20 of 20 and 30 of 30);
- the evidence replay (1 recovered, 0 lost).

## 9. Tuning and decision run 4

### 9.1 Case set (217 per run)

| Part | Cases |
|---|---|
| adversarial_v1, both parts | 96 |
| adversarial_v2, all | 48 |
| adversarial_v3, all (D-94) | 48 |
| Dev questions | 20 |
| G01 to G05 | 5 |

The runner already takes `--set v1 --parts tune,holdout --add v2:holdout --add v3:holdout`. The
in-scope set has 54 questions: 20 dev, G03, G05, and the 16 v1, 8 v2 and 8 v3 controls.

### 9.2 Pass rule (D-84, extended to v3, not otherwise changed)

A run passes when every case passes design Section 6.2, D-43(b) and D-51, and:

1. false refusals: at most 1 of the 54 in one run, and none in both passing runs. ADV-081, ADV-086,
   **ADV3-043 and ADV3-044** are answered with every claim supported;
2. D-57: 0 of 20 dev; at most 2 of 16 v1 controls, at most 1 of 8 v2 controls and at most 1 of 8 v3
   controls with a note not required; none judged false;
3. reported, not failed: tagger failures, gap-reference removals, attachments (each claim citing an
   attachment is read), and over-refusals.

Tuning ends after two passing runs in a row. Any change after a failed run restarts the count. Then
`--freeze-config --set v4` writes `config_g2_v4.json`.

No change to the rule itself is argued: two runs in a row is the only control on run-to-run variance
before a held-out set.

### 9.3 Cost plan within D-97's $8

| Item | Estimate |
|---|---|
| This design | $0.0514 (spent) |
| Build checks (tests marked `api`, replays, dev run) | under $0.02 |
| One tuning run, 217 cases | about $1.58: 169 cases at run 14's $0.00690 each ($1.17), plus 48 v3 cases at decision run 3's $0.00794 per case-repeat ($0.38), plus about 2% for the attachments |
| Decision run 4 (70 cases x 3 repeats, as run 3's $1.67) | about $1.72 |

That leaves about $7.93 after this design and the build checks. Reserving $1.72 for the decision run
leaves about $6.21 for tuning. **The budget allows 3 tuning runs (about $4.74), not the 4 that D-97
permits.** A fourth would pass the cap by about $0.11.

Proposed stop rule, before every paid run: spent so far, plus $1.60 for that run, plus $1.75 for the
decision run, must be at most $8.00. A void run (C-07, C-08) counts towards the money, not towards the
run count.

The 3 runs allow two paths:

- pass, pass;
- fail, then one fix, then pass, pass.

If tuning has not given two passing runs in a row when the stop rule stops it, the proposal is that no
decision run is made. G2 is then recorded as not met, as D-97 says. The reason: a decision run on a
configuration that failed tuning is unlikely to pass, and it would spend $1.72 to show that.

**Monthly limit.** About $2.51 of November's $20 is left (the overseer brief's $2.56 before this
design). That covers one tuning run in November at most. The rest waits for the 1 December reset
unless Kene raises the limit. Kene should confirm the figure in the Console before the first paid run
(D-69).

## 10. Carried risks

| Risk | Measured | Handling |
|---|---|---|
| An invented provision link passes the verifier (ADV-085, D-89) | 1 failure in tuning run 11; 0 in decision run 3 | Known limitation (section 7(c)). The reviewer applies D-89 and D-91 as before |
| Verifier removals cascade to a false refusal (ADV-094 run 14) | 1 in tuning run 14 | Within the D-84 limit of 1; section 7(a) |
| An attached definition gives a model more to cite | 156 of 305 questions gain a definition passage; ADV3-043 and ADV3-044 answered 4 of 4 in the smoke check | Tuning runs measure it on 217 cases; every claim citing an attachment is read |
| The commencement trigger on unseen wording | In-sample probes 28 of 28; fires on 2 of 305 questions | It adds at most one passage and changes no note; misses fall back to today's behaviour |
| "Nigerian Tax Act" is not read as the Nigeria Tax Act by `CORPUS_ACT` (existing) | Found in the probes: such a question counts as naming no Act, so the DQ-12 patterns can fire | No change in this cycle; Phase 8 |
| The definition index carries one odd term (a definition whose quoted words are a long phrase) | 1 of 204 | Harmless (it matches nothing); the build lists the 204 terms in its test |

## 11. Decisions proposed (for Kene, after overseer review)

| Id | Decision | Options | Recommendation |
|---|---|---|---|
| D-98 | Definition attachment | (a) P1-first, similarity at least 0.40, at most one definition, after P6; (b) the same without the floor; (c) none | **(a):** gives ADV3-044, ADV3-043 and ADV2-048 their definitions; touches 156 of 305 questions against 305 for (b); the first six are unchanged on every dev and test question |
| D-99 | Commencement and abbreviation retrieval | (a) commencement lookup for the Nigeria Tax Act (section 3.3), with no Administration Act entry; (b) expand NTA and NTAA before embedding; (c) none | **(a):** fixes ADV3-043, fires on 2 of 305 questions, changes no DQ-12 outcome; (b) changes the first five on 14 tuning cases and lifts ADV-070 over the relevance floor |
| D-100 | Passages and prompt | (a) up to 8 passages (P7, P8 for attachments), `gen-prompt/8` (label range only), the floor ignores attachments, `config_v3.json` frozen after the dev run | **(a):** needed by D-98 and D-99 |
| D-101 | Percent after a section number | (a) a list number followed by "%" or "per cent" is not a section; (b) leave | **(a):** removes a wrong section lookup and a wrong rule-check removal; no measured question changes |
| D-102 | Evidence normaliser `norm/6` | (a) ignore punctuation at a quote's ends; (b) leave | **(a):** recovers 1 of 4 quote-slip removals, loses 0 of the stored quotes, free to measure |
| D-103 | Known limitations kept | Sections 7(a), (c) and (d), and Q015's word swap | **Keep:** each removal is safe; each fix needs paid calibration or adds risk to criterion (a) |
| D-104 | Tuning and decision run 4 plan | (a) 217 cases per run, the section 9.2 rule, at most 3 tuning runs under the section 9.3 stop rule, no decision run unless two passing runs in a row; (b) as (a), but always make the decision run | **(a):** keeps the run inside $8 with about $1.47 of margin |
| D-105 | Decision run 4 setup | (a) `v4` registry entry with the writer's pinned sha256; guard moved to v4, scanning `prompt.py` and `data/curated/*.yml` (now including the retrieval policy); D-57 limit at most 1 of 8 controls, none false; tagger failures reported; gate unchanged | **(a)** |

## 12. Effect on earlier decisions

| Decision | Effect |
|---|---|
| D-21, G1 | Unchanged. `config_final.json` and the D-21 parser are untouched; the first five are identical on all 30 test questions |
| D-54 | Extended: `config_v3.json` keeps every `config_v2.json` setting and appends after P6 |
| D-39, D-77 | Unchanged; measured with the attachments, 0 DQ-12 outcomes change |
| D-79, D-85(1) | Unchanged (section 7(d)) |
| D-90, D-93 | D-93(2) fixed by D-101; D-93(1) and D-90 stay |
| D-84 | Extended to v3 by D-104; the rule itself is unchanged |
| D-97 | Applied: the plan fits $8 with 3 tuning runs |

## 13. Commands run (diagnostics)

Run with `.venv\Scripts\python` and `PYTHONIOENCODING=utf-8` from a scratch folder. Copies of the
scripts and of the main output are in `.agents/g2_fix_design_3/` (git-ignored). Nothing in the
repository was changed by them.

| Script | What it measured | Section |
|---|---|---|
| `harness.py` | Question sets (dev, test ids-only use, G01 to G05, v1 to v3, probes); refuses to embed uncached questions | all |
| `diag.py` | Passages and vector ranks for ADV3-042 to ADV3-044 | 2.1, 3.1 |
| `needed.py`, `defneed.py`, `cands.py` | Needed definitions missing; candidate terms with similarity, passage and frequency | 2.1, 2.3 |
| `proto.py`, `measure.py`, `dist.py` | Prototype rules over 305 questions; attachment counts, DQ-12 before and after | 2.3, 3.3, 3.5 |
| `comm.py` | Commencement trigger and its 28 probes | 3.3, 3.5 |
| `expand.py`, `expand_measure.py`, `abbr_count.py` | Abbreviation wording ranks; expansion on the 14 cases; abbreviation counts per set | 3.1, 3.2 |
| `gen_smoke.py` | Full-flow smoke on ADV3-043 and ADV3-044 (paid) | 5 |
| `first_six.py` | First five and six on dev and test, ids and counts only | 6 |
| `pct.py` | Percent misparse in references, pre-check and lookup | 7(e) |
| `replay_evidence.py` | `norm/6` replay over stored quotes | 7(b) |
