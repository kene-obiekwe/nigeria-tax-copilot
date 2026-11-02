# Retrieval Question Set v1 (Phase 4a)

Status: **built and frozen 2026-11-02, after the label check against the statute text.** No
retrieval has been run against it. Gate G1 (D-20) is measured on its test split in Phase 4b.

File: [`eval/retrieval/questions_v1.jsonl`](../../eval/retrieval/questions_v1.jsonl), 50 questions.
Checks: `tests/test_question_set.py` (no database or API needed).

## 1. Record schema

One JSON object per line, fields in this order:

| Field | Content |
|---|---|
| `id` | `Q001` to `Q050` |
| `question` | Plain-language question, worded as an SME owner would ask it |
| `category` | One of the ten categories in section 4 |
| `gold_units` | Every unit that genuinely answers the question (see section 2) |
| `primary_unit` | The single best answer; always one of `gold_units` |
| `evidence` | One quote of at most 24 words, verbatim from a chunk of the primary unit (whitespace normalised) |
| `register_ids` | Every discrepancy register row on any chunk matching a gold unit, in id order |
| `split` | `dev` or `test` (section 5) |

## 2. How the labels were made

- **Statute text only.** Each question was written after reading the gazette text of the
  in-scope chunks (`data/derived/chunks/v1_chunks.jsonl`). No question was embedded or queried,
  and no retrieval output was looked at. Section numbers are the gazette numbers (D-09).
- **Gold unit granularity.** A gold unit is the smallest unit that holds the answer:
  - a section id (`nta2025:s56`) when the section is a single chunk, or when the answer needs the
    whole section (the section-reference questions);
  - a chunk id (`ntaa2025:s41(5)-(8)`) when the section is split across chunks and the answer sits
    in one of them, so that a neighbouring part of the same section does not count as a hit;
  - a definition chunk id (`nta2025:s202:def:small-company`) or a Schedule chunk id
    (`nta2025:sch3`).
- **More than one gold unit** only where each unit is needed for, or by itself gives, the answer.
  Seven questions have more than one:
  - Q017: Section 190(7), plus Section 157, which applies Section 190 to a business sold out;
  - Q018: Section 187, plus both parts of the "basic food items" definition (rice and garri under
    items (c) and (g), and cassava flakes under item (m));
  - Q019: Section 186, plus the "baby products" definition that brings diapers within it;
  - Q025: the "small company" and "professional services" definitions;
  - Q031: the Eleventh Schedule, plus Section 186(2);
  - Q032: the Thirteenth Schedule, plus Section 163(1)(p);
  - Q037: Section 22(4), plus the "small business" definition.

  Provisions that only point elsewhere were left out, for example Section 20(1)(a) (interest
  subject to the Third Schedule) for Q030.
- **Label review.** The overseer checked every label against the statute text on 2026-11-02 and
  approved it with two additions, both made before the version was frozen: `nta2025:s157` for
  Q017, and `nta2025:s189:def:basic-food-items(l)-(p)` for Q018. Neither primary unit changed.
- **Evidence** is a short quote showing why the primary unit answers the question. The test
  checks it is verbatim in a chunk of the primary unit.
- **Register ids** are taken from the chunk file and checked by the test. Thirty-five primary units
  sit in flagged chunks (23 of them in the test split), so Phase 5 must disclose both readings
  (D-09) for most of these answers. The section-number rule row DR-000 is not on any chunk: Q047
  (Section 192) and Q048 (Section 190) are numbered one lower in the National Assembly text.

## 3. Wording rule and overlap check

Questions use plain language, not statute wording. No question may share a run of five or more
consecutive words with the text of any of its gold chunks. The check
(`shared_runs` in the test):

- lowercases both texts and splits them into words of letters and digits (punctuation and the
  Naira sign are dropped, so "₦50,000,000" reads as `50 000 000`);
- ignores **multi-word defined terms**: any run that touches a phrase defined in the corpus
  (taken from the definition chunk ids, for example "small company", "relevant tax authority",
  "taxable person", "commencement of business") does not count. Single-word defined terms such as
  "tax" or "company" are not ignored, so they still count towards a run;
- compares against the `text` of every chunk that matches a gold unit (not the context header).

A self-test confirms the check catches a copied run, skips a run through a defined term, and
passes a reworded question.

## 4. Coverage by category and split

| Category | Act | Questions | Dev | Test |
|---|---|---|---|---|
| `cit_rates_levy` | NTA | 5 | 2 | 3 |
| `deductions_gains` | NTA | 6 | 2 | 4 |
| `vat` | NTA | 6 | 2 | 4 |
| `exemptions_incentives` | NTA | 6 | 2 | 4 |
| `definitions` | NTA | 5 | 2 | 3 |
| `schedules` | NTA | 4 | 2 | 2 |
| `ntaa_registration_returns` | NTAA | 5 | 2 | 3 |
| `ntaa_assessment_objection` | NTAA | 4 | 2 | 2 |
| `ntaa_payment_penalties` | NTAA | 4 | 2 | 2 |
| `section_reference` | 3 NTA, 2 NTAA | 5 | 2 | 3 |
| **Total** | | **50** | **20** | **30** |

Not counting the section-reference questions, 32 questions are on the Nigeria Tax Act and 13 on
the Nigeria Tax Administration Act. Eight questions need a definition among their gold units
(Q018, Q019, Q024 to Q028, Q037). None of the eight Phase 3 smoke questions is reused; some
topics overlap (for example Section 186 and Section 59), but the questions do not.

## 5. Split

20 dev and 30 test, stratified by category:

1. Each category's dev quota is its share of 20 (count × 20 / 50), rounded down, with the three
   leftover places given to the largest remainders (ties broken by category name). Here that gives
   every category 2 dev questions.
2. One `random.Random(20261102)` shuffles each category's sorted ids, taking categories in sorted
   name order. The first ids in each category's quota are dev, and the rest are test.

The test re-derives the split and checks it matches the file.

**The test split is used once only, for the final Phase 4b run.** Dev questions may be used to
develop and tune retrieval. Test questions are not read for tuning and are not run until the
configuration is fixed.

## 6. Hit rule and gate G1 (D-20)

- A retrieved chunk **matches** a gold unit if it is that chunk or one of its parts (the chunk id
  equals the unit, or starts with the unit followed by `(`). This is the rule `smoke_query.matches`
  already uses.
- A question is a **hit at k** when at least one of its gold units matches a chunk among the top k
  retrieved.
- **G1:** hit@5 of at least 90% on the 30 test questions, that is at least 27 of 30.
- hit@1, hit@3 and MRR (reciprocal rank of the first matching chunk) are reported too. They do
  not gate.

## 7. Frozen version

`eval/retrieval/questions_v1.jsonl` sha256 `48daa9fa13afdec2183b62185dbae20400ec0f3ef9d677623c7ac6199987ba45`

`tests/test_question_set.py` fails if the file no longer matches this hash. A change after review
creates a new version with a decision-log entry and a new hash here, and is never a silent edit.

## 8. How to check

From the repository root:

```
.venv\Scripts\python -m pytest -q tests\test_question_set.py
```
