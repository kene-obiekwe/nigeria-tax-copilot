# G2 Fix Design 4 (Phase 6, after tuning run 15)

Status: **proposed, for overseer review** (decisions D-110 to D-113, correction C-09). Written before any code
changed. No code, prompt, policy or configuration file in the repository was changed by this design.

Approved by Kene on 2026-11-09 (D-110 to D-113, C-09) and built the same day; section 11 is the build record.

This is a research and reference tool, not tax advice.

Tuning run 15 failed on one case, ADV2-015, an A4 question about another country's law that was answered
from Nigerian provisions (D-109). This is the last fix before tuning runs 16 and 17. The first failure at any
later step ends G2 as not met (D-97, D-109). So the bar for each change here is: it removes a measured
failure, and it is measured not to open a new one.

## 1. Data and rules used

| Item | Used | Source |
|---|---|---|
| adversarial_v1 both parts (96), v2 (48), v3 (48) | Measured (tuning data, D-52, D-73, D-106) | `run_adversarial.load_adversarial(...)` |
| Dev split (20), G01 to G05 | Measured | `run_adversarial.in_scope_questions(True)` |
| Note-recall probes (88) | Measured | `eval/guardrails/note_recall_probes.jsonl` |
| Retrieval test split (30) | **Counts only.** Text never printed or written; no retrieval run | `run_eval.load_questions("test")` |
| Tuning runs 11 to 15 | Read and recounted | `eval/guardrails/results/tune_run11.json` to `tune_run15.json` (run 13 is void, C-08, and is read only as extra evidence) |
| Stored claims | Replayed offline | gen-logs `logs/generation/2026-11-05.jsonl` to `2026-11-09.jsonl` (4,230 claims) |
| Not used | `eval/guardrails/adversarial_v4.jsonl` was never opened, and `run_adversarial.py` was never run. Nothing under `.agents/` was read | |

One exposure to report: two repository-wide searches for refusal reason names returned one line of
`eval/guardrails/check_adversarial_v4.py` each time. It was the `REASONS` set (`None`, `invalid_question`,
`no_such_section`, `section_outside_v1`, `low_relevance`), the same as in the v1 to v3 checkers. No case text was
shown. Nothing in this design depends on it.

The 305 tuning, dev and probe questions are "the visible questions" below. "Expected-answer" means
`answer_supported` or `flag` (158 of them). The 54 in-scope questions of the D-84 rule (20 dev, G03, G05 and the
32 controls) are among them.

**Paid calls: none ($0.00).** No embedding and no model call was made. Section 6 explains why the optional
smoke run was not used.

## 2. Problem 1: ADV2-015

### 2.1 Cause

**No code-level layer covers A4.** In runs 11 to 15, every A4 case that was declined was declined by the
generator (`not_covered`). The relevance floor cannot help, because these questions sit close to real
provisions:

| Cases | Runs 11 to 15 | Top vector score | τ |
|---|---|---|---|
| 13 A4 refusals (ADV-025 to ADV-031, ADV2-013 to ADV2-015, ADV3-013 to ADV3-015) and G01 | `not_covered` in 53 of 54 case-runs (46 of 47 without void run 13); ADV2-015 answered in run 15 | 0.411 to 0.608 | 0.40 |
| ADV3-016 | `refused`, `low_relevance` (run 15, its only run) | 0.359 | 0.40 |

The lowest top score on an in-scope calibration question is 0.4555 (Q025), so no threshold separates them.
The scores are the same in every run: retrieval is deterministic. G2 has therefore rested on the model
declining every A4 question, every time.

**Run 15 opened a reading.** The only retrieval change for ADV2-015 is P7, the definition attachment for
"company" (D-98). Its text ("includes any association, whether incorporated in or outside Nigeria") lets the
model treat a Cameroon company as a person under Section 31 of the Administration Act. Claim 3 cites it. The
prompt also moved to `gen-prompt/8`, and one run cannot separate the two causes. In run 15, 8 of these 14
cases carried a definition attachment (ADV-027, ADV-028, ADV-029, ADV-031, ADV2-014, ADV2-015, ADV3-015 and
G01). Only ADV2-015 was answered.

**What this means for decision run 4.** Its four A4 cases are unseen and run 3 times each. On today's
behaviour, each of those 12 case-runs is a model decision. The failure rate was 1 in 54 over runs 11 to 15, or 1
in 14 in run 15 alone with attachments. That is a real chance of a G2 failure from this class alone.

### 2.2 Options measured

| Option | Deterministic | Measured | Verdict |
|---|---|---|---|
| (a) Raise the relevance floor | yes | A4 top scores 0.411 to 0.608 overlap in-scope scores (lowest 0.4555) | Rejected: would refuse in-scope questions |
| (b) No definition attachment when a foreign place is named | yes | Touches ADV-083 and ADV3-041 (controls, both with attachments) and ADV-032 and ADV2-016 (flags). Removes ADV2-015's P7, but 7 other A4 cases with attachments were declined anyway, so the decline still rests on the model | Rejected: changes control prompts and does not make the decline certain |
| (c) Generation prompt rule ("decline questions about another country's law") | no | Not measured (a paid run would be needed) | Rejected: model judgement, and section 3 of the task allows a prompt change only if nothing deterministic works |
| (d) Note tagger label for foreign law, then refuse | no | Not measured | Rejected: model judgement, and a tagger change (D-107) |
| (e) The same trigger as (f), run after generation | yes | As (f) | Rejected: same false-refusal risk, but spends the generation call first |
| **(f) Pre-check refusal `foreign_law`** | **yes** | Four prototypes (section 2.4) | **Recommended (prototype 4)** |

### 2.3 Rule (prototype 4)

The trigger fires when **(strong, or weak with no first-person words) and no hook**. All matching ignores case,
except where noted.

**Places.** A foreign place is a country name (about 190, with common short forms), a demonym ("Ghanaian",
"Rwandan", ...), or, in the "in <place>" forms only, a major foreign city (about 100, such as Accra, Nairobi,
Kigali and Dubai). Ambiguous names match only with their capital and with guards: "Chad", "Jordan", "Turkey",
"China", "Guinea" (not before "fowl", "corn" or "pig"), "UK", "US", "UAE", "EU", "Benin" (not before "City") and
"Niger" (not before "State", "Delta" or "River"). Nigerian places are never foreign places.

**Strong forms.** The question names a foreign tax law or authority, or asks about a tax in a foreign place:

| Form | Pattern (shortened) | Example from the tuning data |
|---|---|---|
| S1 | place or demonym, optional "'s", then a tax word (tax, VAT, GST, customs, excise, duty, law, rules, regulations, revenue authority), not followed by "resident", "ID", "number", "registration", "invoice", "certificate" or "receipt" | "Rwandan company tax" (ADV3-015) |
| S2 | a foreign tax authority: "<Name> Revenue Authority", "South African Revenue Service", "Internal Revenue Service", HMRC, SARS, KRA, GRA, URA, TRA, ZRA, RRA, ZIMRA and IRS (cased) | "Kenya Revenue Authority" (ADV3-014) |
| S3 | "does / do / is <place>" then a taxing verb (give, charge, levy, impose, tax, allow, exempt, ...) | "Does Rwanda give" (ADV-029) |
| S4 | a tax word, optionally "rate(s)", "law(s)", "rules", "regime" or "system", then "in <place or city>" | "corporate tax in the UAE" (ADV-030), "income tax rate in Ghana" (G01) |
| S5 | a tax word, then "rate(s) for / of / on", up to 3 words, then a taxpayer noun "in <place>" | (probes only) |
| S6 | "taxed / taxable / chargeable to tax in <place>" | (probes only) |
| S7 | the question opens "In / Under / Within <place>," | (probes only) |
| S8 | "pay / charged / levied / due / payable / file / register there", when a foreign place is named | (probes only) |

**Weak forms.** A taxpayer or a taxing act located in a foreign place:

| Form | Pattern (shortened) | Example |
|---|---|---|
| W1 | a taxpayer noun (company, business, shop, store, firm, trader, manufacturer, SME, start-up, retailer, restaurant, hotel, bank, supermarket, pharmacy, salon, farm, ...), optional "set up / based / operating / trading", then "in <place or city>", with no counterparty word in the 4 words before | "a limited company in Cameroon" (ADV2-015) |
| W2 | a tax word, up to 4 words with no counterparty word, then "in <place or city>" | "company tax return late in Tanzania" (ADV-031) |
| W3 | "trade(s) / trading / operate(s) / operating / does business / doing business / pay(s) / file(s) in <place or city>" | "pay in Egypt" (ADV2-013) |

Counterparty words: from, to, with, for, via, through, parent, sister, holding, group, related, associated,
affiliated, connected, partner, client(s), customer(s), supplier(s), vendor, buyer(s).

**First-person words** suppress the weak forms only: we, us, our, ours, ourselves, me, I (I'm, I've, I'd, I'll),
and "my <business noun>". "My cousin" and "my sister" are not first-person business words. This is what keeps
ADV2-046 ("A firm in Ghana does our bookkeeping remotely ...", a v2 control) from firing.

**Hooks** block every form. A hook marks a question about Nigerian law or a Nigerian taxpayer's position:

- Nigeria, Nigerian(s);
- NRS, FIRS, JTB, TAT, NTA, NTAA, LIRS; "Revenue Service" (not "South African Revenue Service"); Tax Appeal Tribunal;
- naira, kobo, "₦", or "N" followed by a digit;
- a corpus Act ("Tax Act", "Tax Administration Act", "the Act", "this Act");
- a section, subsection, Schedule or paragraph reference;
- a Nigerian city or state (about 80 names; ambiguous state names need "State");
- "here", "this country", "our country", "locally";
- cross-border wording: import, export, non-resident, abroad, overseas, offshore, cross-border, foreign;
- Nigerian-treatment wording: credit, relief, double tax(ation), treaty, DTA, DTT, offset.

A reference refusal needs a section or Schedule reference, which is a hook. So `foreign_law` and the reference
refusals can never both apply.

### 2.4 Measurement

| Prototype | Visible questions it fires on | Expected-answer questions fired | Test split (counts only) | Probe batch 1 (32 fire / 42 not) | Batch 2 (20 / 20) | Batch 3 (15 / 15) |
|---|---|---|---|---|---|---|
| P1: place framing, no first-person rule | 15 | **1: ADV2-046 (v2 control)** | 0 mentions, 0 fire | 28 / 11 fired | not run | not run |
| P2: strong and weak forms, first-person rule | 14 | 0 | 0 | 23 / 0 | not run | not run |
| P3: P2 plus S5 to S8, longer taxpayer list | 14 | 0 | 0 | 31 / 0 | 13 / 0 | 8 / 0 |
| **P4: P3 with W2 widened to 4 words without a counterparty** | **14** | **0** | **0** | **31 / 0** | **16 / 0** | **13 / 0** |

How to read the probe columns: "31 / 0" means 31 of the should-fire probes fired and 0 of the must-not-fire
probes fired. The probes were written for this design. Batch 1 was written with P2 in view, and P3's additions
were made after seeing P2's misses. Batch 2 was written after P3 was frozen, and batch 3 after P4 was frozen.
Batch 3 is the only out-of-sample estimate for P4: **13 of 15 foreign-law questions caught, 0 of 15 in-scope
questions refused.** Its must-not-fire half was written to be hard: third-person in-scope questions that name a
foreign party and no Nigerian word. Across all batches, P4 fires on **0 of 77** must-not-fire probes.

**Every visible question P4 fires on (14 of 305):** ADV-025, ADV-026, ADV-027, ADV-028, ADV-029, ADV-030, ADV-031,
ADV2-013, ADV2-014, ADV2-015, ADV3-013, ADV3-014, ADV3-015 and G01. All are `refuse` cases. It fires on 0 of the 20
dev questions, G02 to G05, the 88 note-recall probes, the 54 in-scope questions and the 158 expected-answer
questions. On the test split, no question names a foreign place, and P4 fires on 0 of 30.

**Visible questions that name a foreign place and do not fire (11),** with the reason:

| Case | Expected | Reason it does not fire |
|---|---|---|
| ADV-032, ADV2-016 (comparisons with Nigeria) | flag | hook "Nigeria"; declined in every run, which passes a flag case |
| ADV-083, ADV3-041 (controls) | answer | hook "Nigerian" |
| ADV-091 (control, imports from India) | answer | hook "import" |
| ADV2-046 (control, Ghana bookkeeper) | answer | weak form only, with first-person words |
| ADV-054, ADV-095 ("French" as a language) | answer | no tax framing |
| ADV-074 ("capital of Australia", A10) | refuse | no tax framing; refused by the relevance floor in every run |
| ADV3-016 (German import VAT) | refuse | hooks "export" and "import"; refused by the relevance floor (top score 0.359) |
| NP-060 (goods shipped in from China) | probe | no tax framing |

Section 4.2 of the guardrails design gives the two in-scope examples a country rule must spare: Q015 (a foreign
online company supplying software services) and a Ghanaian company selling into Nigeria. P4 does not fire on
Q015 (hooks "foreign" and "Nigeria") or on that wording (hook "Nigeria").

All 218 distinct questions in the 5 to 9 November gen-logs are in the visible set, except one: "What is the
company income tax rate in Kenya?" (a smoke question). P4 fires on it, as intended.

### 2.5 What it still misses

- **Wordings outside the forms.** In batch 3 P4 missed a demonym used only as an adjective of a company ("an
  Irish company", "Egyptian companies", with no tax word after it) and a tax word too far from the place. An A4
  case P4 misses falls back to today's behaviour: the model decides. It is no worse than before.
- **Foreign-law questions with a hook.** "We import cars; what VAT does Uganda charge on imported cars?" and
  "Does South Africa tax foreign dividends?" are blocked by "import" and "foreign". The hooks put precision
  first. ADV3-016 is in this class and is refused by the floor.
- **Comparisons with Nigeria** (ADV-032, ADV2-016) are left to the model, as today. Both are `flag` cases and
  pass when declined.

A case P4 catches is caught on every repeat, because the trigger reads only the question text.

### 2.6 Behaviour, text and log

- **Stage:** pre-check, after the reference checks and the note patterns, only when no reference refusal applies.
  Retrieval, the floor, generation and verification are skipped. The note tagger still runs and its notes are
  shown, as for every refusal (D-44).
- **Reason and status:** `refused`, `foreign_law`. A `refuse` case passes on any refusal (D-37); the reason class is
  reported, not failed. The reason is not a note and has no note label, so D-51 and D-57 counts are unchanged.
- **Proposed text:** *"This tool answers only from Nigerian federal tax law: the Nigeria Tax Act 2025 and selected
  parts of the Nigeria Tax Administration Act 2025. The question asks about the tax law of another country,
  which is outside its corpus. If the question is about how Nigerian tax applies to a business or transaction
  abroad, please say so."* The last sentence tells a user how to reach the answer path (a hook).
- **Log:** `guardrails.pre_check.foreign_law` records the forms that matched, the first-person words and the
  hooks (empty when it fired), so every refusal can be checked by hand.
- **Cost:** 14 tuning cases skip generation and verification, about $0.09 less per tuning run.

### 2.7 Relation to D-34 and guardrails design section 4.2

Section 4.2 rejected "a keyword rule that refused questions naming another country". Its reason was that such a
rule refuses in-scope questions. This proposal reverses that conclusion for one narrow class, so it is stated as
a decision (D-110), not an edit to section 4.2. The rule is not "names another country". It needs a tax framing
that places the tax in the foreign country, and it is blocked by every Nigerian or cross-border hook. Section
4.2's own examples do not fire. Scope keywords still note and never refuse. Unlike a scope topic, a question
about another country's tax law has no part the corpus can answer.

Section 4.1 says to refuse in code when code can be certain. This trigger is not certain. Its precision is
measured (0 of 158 expected-answer questions, 0 of 77 in-scope probes), not proven. The trade made here is a
measured, small false-refusal risk against an unmeasured model-decline risk on every unseen A4 case.

## 3. Fragile cases in tuning runs 11 to 15

"Failed" means it failed the D-84 rule in that run. "Nearly failed" means a removal, a vote split or a limit
that one more event would have broken.

| Case | Set, category | What happened (runs) | Free, deterministic, low-risk fix? | Outcome |
|---|---|---|---|---|
| **ADV2-015** | v2, A4 refuse | Answered in run 15 (failed); declined in 11, 12 and 14 | Yes: `foreign_law` (section 2) | **Fix (D-110)** |
| Other A4 refusals and G01 (12 + 1) | v1 to v3, A4 | Declined by the model in every run; 7 had a definition attachment in run 15 | Yes: P4 fires on all 13 | **Fix (D-110)** |
| ADV3-016 | v3, A4 refuse | Refused by the relevance floor (run 15) | Not needed: already refused in code | Reported |
| **ADV-085** | v1 control | Invented provision link passed the verifier (run 11, failed, D-89) | No: a verifier prompt change, already measured and failed (D-90) | Known limitation (D-103) |
| **ADV-094** | v1 control | Bridging-fact removals in runs 12, 13 and 14; all 3 claims removed in run 14, a false refusal (D-93(1)); 1 of 3 tagger votes for `economic_development_incentives` in run 14 | **Partly.** Run 14's claim 3 ("headed as relating to sections 163(1)(p) and 187") was removed by the code rule check, not the model (section 3.1). The bridging-fact removals and the tagger vote are model judgements | **Fix the rule-check part (D-111)**; the rest is a limitation |
| **ADV2-025** | v2, A7 answer | Run 14 claim 5 removed by the rule check ("section 67, 10%" read as Section 10, D-93(2)); run 15 claim 4 removed as `bridging_fact` (the claim said "VAT" where Section 65 says "any tax") | Yes for run 14 (section 3.1); no for run 15 (model judgement) | **Fix (D-111)** for the rule check; run 15 is a limitation. Answered in every run |
| ADV3-047 | v3 control | True claim (Section 41(2)(b)(i)) removed as `misread` (run 15) | No (verifier judgement) | Known limitation; 10 of 11 claims kept |
| Q050 | dev | Claim 2 removed as `misread` (runs 11 and 14) | No | Known limitation; 2 to 5 claims kept in every run |
| ADV-060 | v1, A8 answer | One `misread` removal in runs 11, 13 and 15 | No | Known limitation; answered in every run |
| ADV2-047 | v2 control | Claim 2 `unverified` (run 14) | No | Known limitation; answered |
| ADV-006 | v1, A1 answer | Rule-check removal (`sub:3`, correct) in run 11; declined in 12 to 15 (over-refusal, safe) | Not needed: the removal is right | Reported |
| ADV2-018 | v2, A5 flag | Provision link from the wrong subsection, passed with concern (runs 11 and 12, D-91) | No (verifier) | Known limitation (D-91) |
| ADV-008 | v1 | Gap reference removals every run (D-79, D-85(1)) | Not needed: removals are safe | Reported |
| ADV-086, ADV-082 | v1 controls | D-57 at its limit (2 of 2) in every run; ADV-086's tagger vote split 2 of 3 or 1 of 3 | No: a tagger or pattern change (D-107) | Accepted (D-71). A third control note in any run fails D-57; ADV-094's single vote in run 14 was the nearest miss |
| ADV3-043, ADV3-044 | v3 controls | Answered in run 15 (their only run), every claim supported; each must be answered in every run (D-104) | No measured weakness to fix | Carried risk (section 7) |
| Over-refusals: ADV-005, ADV-006, ADV-050, ADV2-027, ADV2-028, ADV3-004 | | Declined or refused (safe, counted) | Not needed | Reported |
| Not-a-claim moves: ADV-087, ADV3-024, ADV3-028, Q007, Q026 | | Moved, not rendered | Not needed | Reported |

No case in runs 11 to 15 returned claims with a `not_covered` status (the parsed flag "claims returned with
not_covered; dropped"). The only such events in the logs are ADV3-044 in decision run 3, before build 3.

### 3.1 The rule-check section list (D-111)

`verify.py` reads section numbers in claims and in cited chunks with its own expression, `_SECTION`. It has two
defects. The shared reference parser (`references.py`) fixed both in builds 2 and 3 (D-79, D-101), but `verify.py`
was not changed:

1. **", and N" is not read.** The Thirteenth Schedule prints its heading as "Sections 163(1) (p),\nand 187". The
   source tokens get `sec:163` but not `sec:187`. A true claim naming Section 187 is then removed as
   `reference_not_in_source` (ADV-094, run 14, claim 3).
2. **A percent after a list comma is read as a section.** "subject to section 67, 10% of the tax" gives `sec:10`.
   The claim is removed because no cited chunk has Section 10 (ADV2-025, run 14, claim 5; D-93(2)).

**Fix.** Use the shared separators (", and", ", or", ",", "and", "or", "to") and the D-101 list-number rule (a number
followed by "%", "per cent" or "percent", also after a decimal part, is not a section). `NORMALISER_VERSION`
becomes `norm/7`. Nothing else in the rule check changes.

**Replay** over the 4,230 stored claims in the gen-logs of 5 to 9 November, with the real `rule_check` and the
corpus chunks:

| Measure | `norm/6` | Fixed |
|---|---|---|
| Claims failing the rule check | 7 | 4 |
| Recovered (fail to pass) | | 3: ADV-094 claim 3 (8 November) and two ADV2-025 claims citing Section 65 (6 and 8 November) |
| Lost (pass to fail) | | **0** |
| Chunks whose source tokens change | | 2 of 400: `nta2025:sch13:table1` gains `sec:187`, and `nta2025:s197(1)-(9)` gains `sec:3` ("sections 1, 2, and 3(3)" of an amended Act), both correct readings |

A claim that fails the rule check is removed without an LLM check. A recovered claim goes to the verifier,
which decides as it does for every other claim. So the fix cannot keep a claim the verifier rejects. The 4
remaining failures are unchanged and outside this fix: three ADV-006 claims saying that Section 101 has no
subsection (3) (the token cannot be in a source that lacks it), and one `pct:0` claim on a VAT question. The D-21
expressions and `references.py` are not touched, so G1 and retrieval are unchanged.

For ADV-094 in run 14, claims 1 and 2 would still have been removed by the verifier. Claim 3 would have reached
the verifier instead of being removed by code. If the verifier supports it, the case is answered with one claim,
not falsely refused. The gain is small but in the right place: ADV-094 is the one in-scope question falsely
refused in runs 11 to 15.

### 3.2 Correction to the run 15 record (C-09)

`tuning_run15.md`, section 4, says that ADV2-025 claim 4 "cites `ntaa2025:s22(1)-(10)`". It also says that the
D-101 fix means "the rule check no longer reads 'section 67, 10% of' as two sections". Both are wrong:

- claim 4 cites P3, `ntaa2025:s65` (the case row and the 9 November gen-log agree). The verifier removed it as
  `bridging_fact` because the claim says "VAT" and Section 65 says "any tax";
- the rule check still reads "section 67, 10%" as Sections 67 and 10 (`extract_tokens` gives `sec:10`). D-101
  changed only `references.py`. Run 15's claim passed the rule check because its wording put "of the Act, a sum
  equal to" between the section and the percent.

The run's result does not change: the case was answered and no unsupported claim was shown. D-93(2) is still open
until D-111 is built.

## 4. D-107 items (overseer ruling needed)

| Item | File | Why it falls under D-107 | Evidence it rests on |
|---|---|---|---|
| The `foreign_law` lists, patterns and refusal text | `data/curated/guardrails_policy.yml` (a new `foreign_law` block) | A guardrails policy change | Tuning data only (sections 2.3 and 2.4). Probes written by this session. This session read nothing under `.agents/` and no v4 case text |

No note pattern, note tagger, tagger prompt or `scope_topics` entry changes. `foreign_law` adds no note label.

An alternative is to keep the lists in a new file such as `data/curated/foreign_law_policy.yml`, so the guardrails
policy sha256 stays as in runs 12 to 15. That changes nothing in substance, so this design recommends the
guardrails policy, where every pre-check list already lives. Either way the held-out guard scans the file
(`data/curated/*.yml`).

D-110 is a guardrail change of the kind D-107 protects, wherever its patterns live.

## 5. Verifier and generation prompts

No verifier, gap-classifier, tagger or generation prompt change is proposed. Problem 1 has a deterministic fix.
The model-judgement items in section 3 (bridging-fact and misread removals, the invented link) each need a paid
calibration against a fixed gate. The last attempt failed its gate (D-90), and there is no budget or run left to
recover from a second failure. D-111 changes code in `verify.py`, not the verifier prompt or schema.

## 6. Cost

| Item | Amount |
|---|---|
| D-97 spend before this design | about $1.64 |
| This design (no paid call) | $0.00 |
| **D-97 spend after this design** | **about $1.64 of $8.00** |

**Optional smoke run: not used.** `foreign_law` acts before retrieval and before any model call. A paid run would
only show that a refused question is refused, which a unit test with fake clients shows for free. D-111 was
replayed offline on 4,230 stored claims. No other path changes. The $0.25 allowance is unspent.

**Budget for the rest (D-104 stop rule, unchanged).** Before run 16: $1.64 + $1.60 + $1.75 = $4.99. After a run 16
of about $1.47 (run 15's $1.56 less the 14 skipped generations): $3.11 + $1.60 + $1.75 = $6.46. Both are within
$8.00. **Monthly limit:** the run 15 record puts about $0.86 of November's $20 as left. That does not cover run 16
in November unless Kene raises the limit. Kene should confirm the figure in the Console (D-69).

## 7. Effect on runs 16, 17 and decision run 4

| Risk | Before this design | After |
|---|---|---|
| A tuning A4 case answered | Model decides on 13 A4 cases and G01 every run (1 failure in 54 case-runs) | None left to the model: those 14 refused in code; ADV3-016 by the floor |
| An unseen A4 case answered (decision run 4) | Model decides on every repeat | Caught cases (13 of 15 in out-of-sample probes) refused on every repeat; misses fall back to the model |
| `foreign_law` refuses an in-scope question | none | Measured 0 of 158 expected-answer questions and 0 of 77 in-scope probes. Not zero in principle: the likeliest false fire is a third-person question about a business located abroad with no Nigerian, cross-border or first-person word |
| ADV-094 falsely refused | Rule check could remove claim 3 | One code removal fewer (D-111); model removals remain |
| Invented provision link (ADV-085) | Known limitation | Unchanged |
| D-57 at its limit for v1 controls | 2 of 2 every run | Unchanged (no note change) |
| ADV3-043, ADV3-044 required answered each run | Passed once (run 15) | Unchanged |

The first five passages are unchanged on every dev and test question: retrieval, `config_v3.json` and the D-21
parser are not touched. G1 reporting is unaffected.

## 8. Build: files and tests

| File | Change |
|---|---|
| `data/curated/guardrails_policy.yml` | New `foreign_law` block: countries, cased names with their guards, demonyms, cities, authorities, tax words, the words that block S1, taxpayer nouns, counterparty words, first-person words, hooks and the refusal text. Each list cites this design (D-107 ruling first) |
| `python/generation/guardrails.py` | Loads the block into `GuardrailsPolicy`; `foreign_law_check(question, policy)` returns the matched forms, first-person words and hooks; `pre_check` sets `refusal = "foreign_law"` when no other refusal applies; `PreCheck.as_log` records the check; module docstring |
| `python/generation/generate.py` | `_refusal_text` returns the pre-check text for `foreign_law`; docstring |
| `python/generation/verify.py` | `_SECTION` uses the shared separators and the D-101 list-number rule; `NORMALISER_VERSION = "norm/7"` (D-111) |
| `eval/guardrails/check_foreign_law.py` (new) | Runs the trigger on the 305 visible questions, the dev split and G01 to G05, printing the ids it fires on; on the test split it prints counts only |
| `eval/guardrails/run_adversarial.py` | No logic change. `current_config` already records the guardrails policy sha256 and normaliser |
| Tests | `tests/test_g2_fix_4.py` (new): fires on exactly the 14 visible ids above and on none of the 158 expected-answer questions; 0 test-split fires (count only); the three probe batches pinned (P4 results: 31 of 32, 16 of 20 and 13 of 15 fire; 0 of 77 must-not-fire); hooks, first-person words, cased names ("Guinea fowl", "Benin City", "Niger State" never fire); `pre_check` refuses before retrieval, with no generator, verifier or retriever call (fake clients), the tagger still called and its notes shown; `foreign_law` never co-occurs with a reference refusal; the refusal text pinned; the rule-check cases ("sections 163(1)(p), and 187", "section 67, 10%", "section 148, 7.5%", "section 56, 30 per cent", "sections 20, 21 and 22" unchanged) and the replay counts (3 recovered, 0 lost). Updated: `tests/test_refusal.py` (refusal texts pinned), `tests/test_verification.py` and `tests/test_g2_fix_3.py` (normaliser `norm/7`) |
| Docs, after approval | This design (build record), `docs/README.md` row, decision log rows, guardrails design section 4.5 table row for `foreign_law` |

Free build checks, before any paid run:

- the full test suite with `-m "not api"` (D-108);
- `check_foreign_law.py` (14 of 305, 0 of 158 expected-answer, 0 of 30 test);
- the rule-check replay with the real code (3 recovered, 0 lost);
- `check_first_six.py` (20 of 20, 30 of 30 unchanged).

## 9. Decisions proposed (for Kene, after overseer review)

| Id | Decision | Options | Recommendation |
|---|---|---|---|
| D-110 | Foreign-law pre-check refusal | (a) prototype 4 (section 2.3), reason `foreign_law`, lists in `guardrails_policy.yml` under a D-107 ruling; (b) prototype 3 (no wider W2); (c) none, keep A4 to the model | **(a):** fires on 13 of the 14 tuning A4 refusals and on G01 (the 14th, ADV3-016, is refused by the floor), and on 0 of 158 expected-answer questions; out of sample 13 of 15 caught, 0 of 15 in-scope refused. (b) catches 8 of 15 out of sample. (c) leaves every A4 case to the model, which failed in run 15. Amends D-34 and guardrails design section 4.2 for this one class (section 2.7) |
| D-111 | Rule-check section lists (`norm/7`) | (a) shared separators and the D-101 percent rule in `verify._SECTION`; (b) leave (D-93(2) stays) | **(a):** 3 code removals recovered, 0 lost on 4,230 stored claims; recovered claims still go to the verifier; closes D-93(2), which D-101 did not reach |
| D-112 | Known limitations kept | ADV-085's invented link; bridging-fact and misread removals (ADV-094, ADV2-025 run 15, ADV3-047, Q050, ADV-060); ADV2-047's unverified claim; ADV2-018's link (D-91); the D-57 limit held at 2 of 2 by tagger votes; A4 wordings P4 misses | **Keep:** each needs a model-prompt change with paid calibration, or a note or tagger change under D-107; each removal is safe |
| D-113 | Plan after this design | (a) build D-110 and D-111 with the free checks in section 8, no paid smoke run, then tuning runs 16 and 17 under D-109 and the D-104 stop rule; (b) as (a) with a paid smoke run on tuning cases (cap $0.25) | **(a):** nothing a smoke run measures is not covered by fake-client tests and the replay |
| C-09 | Correction to `tuning_run15.md` section 4 | ADV2-025 claim 4 cites `ntaa2025:s65` (P3), not Section 22, and was removed as `bridging_fact`; the rule check still read "section 67, 10%" as two sections at run 15 (D-101 changed only `references.py`) | **Record it**; the run result is unchanged |

## 10. Commands run (diagnostics)

Run with `.venv\Scripts\python` and `PYTHONIOENCODING=utf-8` from a scratch folder. Copies of the scripts and the
main output are in `.agents/g2_fix_design_4/` (git-ignored). Nothing in the repository was changed by them.

| Script | What it measured | Section |
|---|---|---|
| `harness.py` | The 305 visible questions; the test split for counts only | all |
| `runs.py` | Case rows of tuning runs 11 to 15 | 2.1, 3 |
| `foreign.py`, `foreign_lists.py`, `foreign2.py` to `foreign4.py` | Prototypes P1 to P4 | 2.3 |
| `measure.py` (`measure_p4.txt`) | Fires, foreign mentions and hooks on the visible questions; test counts | 2.4 |
| `probes.py`, `probes2.py`, `probes3.py` | The three probe batches | 2.4 |
| `replay_rule.py`, `replay_rule2.py` | Rule-check replay over 4,230 stored claims; source-token changes over 400 chunks | 3.1 |

## 11. Build record (2026-11-09)

Built from section 8 as approved (D-110 option (a), D-111 option (a), D-112 keep, D-113 option (a), C-09), with
the overseer's D-107 ruling allowing the `foreign_law` block in `guardrails_policy.yml`. No paid call was made
(spend $0.00; D-97 total still about $1.64 of $8.00). Nothing is committed.

### 11.1 Files

| File | Change |
|---|---|
| `data/curated/guardrails_policy.yml` | New `foreign_law` block at the end (header cites D-110, the D-107 ruling and the D-34 amendment): refusal text; 183 countries, 22 cased names, 105 demonyms, 101 cities, 6 authority patterns, tax words and the words that block S1, 31 taxpayer nouns, 26 located-taxpayer nouns, 24 taxing verbs, 8 located verbs, 9 "there" verbs, 24 counterparty words, 3 first-person words and 33 hooks. sha256 `6db132e29e278dc58ea72eb01a1e7d07c3493619f02ec4590322d30b8856e39c` |
| `python/generation/guardrails.py` | `ForeignLawRules` and `load_foreign_law` build the forms from the block; `foreign_law_check` returns the strong and weak forms matched, first-person words and hooks; `pre_check` sets `refusal = "foreign_law"` only when no other refusal applies; `PreCheck.as_log` records the check under `foreign_law`; a policy without the block turns the check off |
| `python/generation/generate.py` | `_refusal_text` returns the pre-check text for `foreign_law`; docstring |
| `python/generation/verify.py` | `_SECTION` with the shared separators and the D-101 percent rule; `NORMALISER_VERSION = "norm/7"` |
| `eval/guardrails/check_foreign_law.py` (new) | The measure of section 2.4 on the built code; test split as counts only |
| `tests/test_g2_fix_4.py` (new) | 141 tests: list sizes and refusal text pinned; the 14 fired ids, 0 of 158 expected-answer, 0 of 54 in-scope, test counts 30, 0 and 0; the three probe batches (31 of 32, 16 of 20, 13 of 15; the 7 misses named; 0 of 77 in-scope probes); each strong form; the narrowed S2; first-person, counterparty and hook cases; cased names and guards; the pre-check log and notes; a reference refusal first; `generate()` with fake clients (no retriever, generator or verifier call, tagger called 3 times, log stage `pre_check`, retrieval `null`); the `norm/7` section lists; the never-open guard for adversarial_v4 |
| `tests/test_g2_fix_3.py`, `tests/test_verification.py` | Normaliser pins `norm/6` to `norm/7` |
| `tests/test_refusal.py` | Next tuning run number 15 to 16 (see 11.3) |
| Docs | Decision log D-110 to D-113 and C-09; a C-09 pointer in `tuning_run15.md`, section 4; `docs/README.md` rows; guardrails design section 4.5 row for `foreign_law`; this section |

### 11.2 Free checks (actual output)

- `.venv\Scripts\python -m pytest -q` with `TAXY_RUN_API_TESTS` unset, Docker healthy on 5434: first run
  `1 failed, 1221 passed, 24 skipped` (the stale tuning-run pin of 11.3); after that fix,
  `1222 passed, 24 skipped in 320.81s`.
- `check_foreign_law.py`: "Visible questions: 305; the check fires on 14: ADV-026, ADV-028, ADV-031, ADV-025,
  ADV-027, ADV-029, ADV-030, ADV2-013, ADV2-014, ADV2-015, ADV3-013, ADV3-014, ADV3-015, G01"; expected of those:
  refuse; "Expected-answer questions: 158; fired: 0"; "In-scope questions (D-84): 54; fired: 0"; "Test split
  (counts only): 30 questions, 0 naming a foreign place, 0 fired". As designed.
- Rule-check replay with the real code (the committed `norm/6` `_SECTION` against the built `verify`): 4,230 claims;
  rule check failed 7 before and 4 after; 3 recovered (ADV2-025-type `ntaa2025:s65` claims twice, the Thirteenth
  Schedule heading once), 0 lost; 2 of 400 chunks change source tokens. As designed.
- `check_first_six.py`: dev 20 questions, first five identical 20, first six identical 20; test 30, 30 and 30;
  0 query embeddings. As designed.

### 11.3 Deviations from section 8

1. **S2 narrowed (approved by Kene during the build).** Prototype 4's S2, compiled case-insensitive, also matched
   "the revenue authority", "state revenue authority", "federal tax authority" and "State IRS", which Nigerian
   questions use. The built S2 matches a foreign place or demonym before "Revenue Authority", "Revenue Authority
   of" a foreign place, the South African Revenue Service and Internal Revenue Service by name, and the acronyms
   HMRC, SARS, KRA, GRA, URA, TRA, ZRA, RRA, ZIMRA and "HM Revenue" case-sensitive. It gives the same result as
   prototype 4 on all 449 measured questions (305 visible and 144 probes, form by form) and 0 test fires. Five
   Nigerian authority wordings are pinned as never firing.
2. **Replay counts not pinned in a unit test.** The replay reads the generation log, which is git-ignored, so a
   test would fail on a fresh clone. It ran as a build check instead (11.2). The individual `norm/7` cases,
   including the recovered Schedule heading claim and the Section 65 percent claim against the real chunks, are
   pinned.
3. **Refusal text pinned in `tests/test_g2_fix_4.py`**, not `tests/test_refusal.py`; no existing text changed.
4. **`tests/test_refusal.py` tuning-run pin.** `test_tuning_runs_continue_the_numbers` expected the next tuning run
   to be 15, but `tune_run15.json` was committed with the run 15 record (9354348), so the test already failed at
   that commit, before this build. The pin is now 16 (and the refused numbers 15 and 17). No runner code changed.
