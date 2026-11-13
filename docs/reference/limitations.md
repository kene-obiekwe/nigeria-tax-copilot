# Known limitations

> This project is a research and reference tool, not tax advice. This page lists what the system does not
> do and where it is known to fall short, with the document each item comes from. The figures it describes
> are measurements of this system on these question sets, not guarantees about any other question.

The list is grouped with what matters most to a user first. Decision ids (D-xx) are in the
[decision log](../00_planning/decision_log.md).

## 1. What is outside the corpus

1. **Personal income tax is out of scope.** That includes PAYE, an individual's tax bands, rates and reliefs,
   and sole traders and business names whose profits are taxed as the owner's income. Sections outside the
   v1 corpus (for example Sections 13 and 58 of the Nigeria Tax Act and the Fourth Schedule) are declined,
   and so are other taxes: state and local levies, stamp duties, customs and excise, petroleum taxes,
   surcharge and economic development incentives. A user asking about their own personal tax gets no
   answer. Sources: D-64, D-10; [v1 corpus definition](../phase01_corpus/v1_corpus_definition.md);
   [evaluation report](../phase08_evaluation/evaluation_report.md), Section 6.
2. **The corpus is a snapshot of 2026-10-18.** Amendments, orders, re-gazetting and court rulings after that
   date are not included. Periods before 1 January 2026 are governed by the repealed Acts, which are not
   held. Subsidiary legislation and guidance (the General Transition Guidelines, the Nigeria Tax
   Administration Order 2026 on late-payment interest, any VAT Modification Order 2026 and circulars of the
   Nigeria Revenue Service) are not held. Source: [data inventory](../../data/README.md).
3. **Two official texts disagree.** The corpus cites the Official Gazette text and discloses where the
   National Assembly text differs (D-09, DQ-01 to DQ-05). The Nigeria Tax Act text cited is the Lagos State
   Internal Revenue Service gazette copy, because the Nigeria Revenue Service publishes no copy (DQ-03).

## 2. Carried from gate G2 (not met)

Gate G2 was not met: decision runs 1 to 4 all failed, and under D-97 and D-109 there are no further fixes
or runs (D-115). In run 4 all 603 rendered claims were supported by their cited text and every refusal and
decline was safe; the run failed on two cases, which stay as known limitations
([G2 results, run 4](../phase06_guardrails/g2_results_v4.md), Section 11):

1. **ADV4-009:** a question about subsidiary legislation named "Regulations" gets no scope note, so the answer
   does not say that such legislation is outside the corpus.
2. **ADV4-044:** exported-services questions miss Section 187 and the definition of an exported service,
   because retrieval does not find them; the system declines instead of answering.

## 3. Found in the Phase 8 evaluation

From the [evaluation report](../phase08_evaluation/evaluation_report.md), Sections 3.2 and 6.1:

1. **N05, a retrieval miss.** A question on whether a football club's profits are exempt was declined,
   because Section 163(1)(r) (profits of a company engaged in sporting activities) was not retrieved. It was
   the only false refusal, in both runs, and retrieval caused it.
2. **Q032 lists 2 of the 6 Thirteenth Schedule activities** and does not state the five-year exemption, after
   the verifier moved five claims to gaps. The claims shown are supported; the answer is incomplete.
3. **The N17 levy calculation is declined** without gross turnover, although the question says the company is
   not a small company. The answer states the 4% rule but not the figure.
4. **Q009 and N11 are incomplete:** Q009 states the exemption thresholds but does not apply them to the
   amounts in the question; N11 states some grounds of an objection but not the disputed issues.
5. **The reviewer is a calibrated model session, not an independent person.** It was calibrated blind and
   checked by a second session and by Kene on 10 of the 180 case-runs, but it can share blind spots with the
   model that wrote the claims.
6. **The retrieval test questions have a retrieval bias.** Retrieval was known to find a gold unit for each, so
   they are easier than unseen questions; the 20 new in-scope questions are the better guide, and 20 is small.
7. **0 of 40 must-refuse cases answered** bounds the true leak rate only to about 8.8% per run. It is a check
   for systematic leaks, not proof of a low rate.
8. **The test split and the end-to-end set are spent.** A further measurement needs a fresh held-out set
   (D-133, D-135).

## 4. Carried from Phases 6 and 7

From the evaluation report, Sections 6.3 and 6.4:

1. True claims are sometimes removed as bridging facts or misread.
2. Gaps (the statements of what the retrieved text does not say) have no citation field, so a gap can carry
   accurate legal content without a citation.
3. The gap reference check can bind a section to the wrong Act.
4. A provision link can be invented, and a link can be attributed to the wrong provision.
5. Foreign-law wordings that the pre-check does not match can pass it.
6. A tool call can be skipped: the model chooses automatically, and nothing forces a call.
7. **Interest is never computed:** the interest rates are not in the corpus, and the calculator says so.

## 5. Interface and privacy (Phase 9)

From the [interface design](../phase09_interface/interface_design.md):

1. **The personal-data screen blocks email addresses, phone numbers and long digit runs. It does not detect
   names or addresses**, nor health or religious details. Users are asked not to enter personal or taxpayer
   data.
2. **Each question is answered on its own.** Earlier questions in the same chat are not used.
3. **The log viewer is local only** and is never part of the public app.
4. **The low-relevance refusal still runs the note tagger**, a small Claude Haiku call whose cost is recorded
   in the log.
5. **Sign-in, the usage limits and the daily cost cap are built but switched on only in Phase 11** (D-162,
   D-163).
6. **Rotating the secret behind the one-way sign-in key detaches signed-in histories** from their owners.
7. **Locally nothing is deleted automatically.** The 90-day deletion is a hosted-service setting.

## 6. Platforms

Built and tested on Windows 11 with PowerShell, Python 3.12 and Docker Desktop. Other platforms are untested.
