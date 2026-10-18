# v1 Corpus Definition

Defines exactly which statutory text the v1 system retrieves from. It rests on the
[scope verification](scope_verification.md) (2026-10-18). The full Acts are stored unedited
in `data/raw/`. The parts listed here as in scope are the ones Phase 2 parses and chunks.

**Status: approved by Kene on 2026-10-18 (D-09 option A; D-10). The NRS publishes no copy of
the Nigeria Tax Act text, so the Lagos State IRS gazette copy is cited (DQ-03).**

## 1. Authoritative text (D-09)

Two official-source texts of each 2025 Act exist, and they differ in in-scope provisions
(scope verification, F-7). The corpus has to treat one as the text it cites.

| Option | What it means | Trade-off |
|---|---|---|
| **A. Gazette primary, National Assembly text as pinned comparison (recommended)** | Chunk, embed and cite the Federal Government Printer gazette. Store the National Assembly text unedited and hashed, but don't embed it in v1. Phase 2 produces a discrepancy register for in-scope sections. Any answer that cites a section on that register must say the two texts differ and give both readings. | Matches the text the Nigeria Revenue Service says it administers and that a court treats as in force until set aside. The dispute is disclosed, not hidden. Phase 2 gains a small diff task. |
| B. National Assembly text primary | Cite the copy certified by the Clerk and signed by the President. Keep the gazette as the comparison text. | Arguably the constitutionally passed text, but not the text the tax authority applies. Answers could contradict what taxpayers are assessed on. |
| C. Both embedded, tagged by version | Chunk and embed both. Each chunk carries a `text_version` tag, and retrieval returns both where they differ. | Most complete, but it doubles near-duplicate chunks, complicates hit-rate labelling (gate G1) and citation checks, and pushes version reasoning into generation. Too heavy for v1. |

**Option A was approved.** If a re-gazetted text is published later, it becomes a new edition
in the manifest (a new D-xx entry). Existing raw files stay unchanged.

Where two gazette prints exist (DQ-02, DQ-03), the cited text is the print hosted by the
Nigeria Revenue Service, the federal body that administers the Acts. State-agency copies are
corroborating or comparison copies.

## 2. Nigeria Tax Act 2025: in-scope parts

Sections 1 to 88 are numbered the same in both texts. From section 89 onward the gazette
number is one higher than the National Assembly number, so both are given.

| Chapter / Part | Content | Gazette sections | NA sections | Why |
|---|---|---|---|---|
| Chapter One | Objective and application | 1-2 | 1-2 | Context for every answer |
| Chapter Two, Part I | Imposition of tax on income, profits or gains | 3-5 | 3-5 | Charge to company income tax |
| Chapter Two, Part II (companies only) | Nigerian company, dividends, undistributed profits, partnerships of companies | 6-11 | 6-11 | Company residence and dividends |
| Chapter Two, Part III | Non-resident persons | 17-19 | 17-19 | Foreign-owned SMEs and non-resident suppliers |
| Chapter Two, Part IV | Deductions allowed and not allowed | 20-21 | 20-21 | Core SME question type |
| Chapter Two, Part V | Basis periods, change of accounting date, cessation, continuity | 22-26 | 22-26 | Start-up and year-end questions |
| Chapter Two, Part VI | Total profits of companies | 27 | 27 | Computation |
| Chapter Two, Part VIII | Chargeable gains | 33-55 | 33-55 | Company gains are now taxed under this Act |
| Chapter Two, Part IX (company rate and minimum ETR) | Rate of tax for companies; effective tax rate | 56-57 | 56-57 | 0% / 30% rates and top-up tax |
| Chapter Two, Part X | Development levy (4%) | 59 | 59 | Applies to non-small companies |
| Chapter Six | Value added tax (imposition, supply, rate, value, remittance, invoices, input credit, fiscalisation) | 144-158 | 143-157 | VAT scope |
| Chapter Eight, Part I | Income tax exemptions, deductible donations, research and development | 163-165 | 162-164 | Company exemptions |
| Chapter Eight, Part IV | VAT exempt supplies, zero-rated supplies, Presidential exemption orders | 186-189 | 185-188 | VAT exemptions |
| Chapter Nine | General provisions: restructuring, artificial transactions, arm's length, regulations, repeals, savings, conflict, **general interpretation**, citation | 190-203 | 189-202 | Definitions such as "small company" sit here |
| First Schedule | Capital allowances (Part I, companies) | - | - | Needed for any profit computation |
| Third Schedule | Deductible interest | - | - | Interest restriction |
| Eleventh Schedule | Items on which VAT is suspended | - | - | VAT |
| Thirteenth Schedule | Exemption for agricultural businesses | - | - | Referenced by the income tax exemptions |

Out of scope in v1, listed as known gaps: individuals and personal income tax (sections
12-16, Part VII sections 28-32, section 58, Fourth, Fifth and Twelfth Schedules); specialised
trades (Part XI, sections 60-64: free zones, insurance, lottery, collective investment
schemes, mining; Second and Eighth Schedules); petroleum operations (Chapter Three); double
taxation relief (Chapter Four); stamp duties (Chapter Five, Chapter Eight Part III, Ninth
Schedule); surcharge (Chapter Seven); economic development incentives (Chapter Eight Part II,
Tenth Schedule); petroleum schedules (Sixth and Seventh); the Defence and Security
Infrastructure Fund (Fourteenth Schedule).

## 3. Nigeria Tax Administration Act 2025: include selected parts

**Decision: include.** SME questions about company income tax and VAT are as often about
procedure as about liability: how to register and obtain a Tax ID, when a company's return is
due, when VAT returns are filed (the 21st of the following month), what the penalty is for
filing or paying late, and how to object to an assessment. The Nigeria Tax Act doesn't answer
any of these. The administration Act does. Without it, the refusal rate on ordinary SME
questions would be high and the system would look less useful than it is. The cost is
modest: about 60 short sections. The same gazette-versus-National-Assembly question applies
(D-09), and section numbering is the same in both texts.

| Part | Content | Sections |
|---|---|---|
| Chapter One, Part I | Objective, application, jurisdiction of tax authorities | 1-3 |
| Chapter One, Part II | Registration, Tax ID, changes, deregistration | 4-10 |
| Chapter Two, Part I (non-petroleum) | Company returns (11), simplified annual return (15), VAT returns and fiscalisation (22-23), virtual asset providers (25), incentive returns (26-27), deduction at source (28), banker information (29), tax planning disclosure (30), books of account (31-33) | 11, 15, 22-23, 25-33 |
| Chapter Two, Part II (non-petroleum) | Self-assessment, administrative and additional assessments, deemed-profit assessment, currency, notices, objections and appeals, finality, artificial and avoidance arrangements, payment, deduction at source, winding-up, refunds and VAT refunds | 34-36, 38-49, 51-53, 55-56 |
| Chapter Two, Part III (selected) | Penalty and interest for non-payment; remission of penalty | 65-66 |
| Chapter Three, Part I (selected) | Tax clearance certificate; advance rulings | 72-76 |
| Chapter Four, Part I (selected) | Offences and penalties of general application: failure to register, file, keep books, use fiscalisation, deduct or remit tax, attend to notices; false refund claims; false declaration; body corporate; general penalty | 100-108, 121-122, 124, 126-127 |
| Chapter Five (selected) | Interpretation | 147 |

Out of scope: individual, PAYE and petroleum or mining returns and assessments, enforcement
powers (57-64, 67-69), state revenue bodies (87-99), stamp-duty and petroleum offences, and
miscellaneous procedure.

## 4. Known gaps (v1)

- Personal income tax, PAYE and sole proprietors or partnerships of individuals (taxed as
  individuals). An SME run as an unincorporated business is outside v1.
- State and local government taxes and levies.
- Stamp duties, capital gains of individuals, petroleum taxes, surcharge, economic development
  incentives.
- Subsidiary legislation and guidance: the General Transition Guidelines (18 June 2026), the
  Nigeria Tax Administration Order 2026 (late-payment interest, from 1 October 2026), any VAT
  Modification Order 2026, and NRS circulars and guides. These change practical answers but
  are not the Act. They are candidates for v2.
- Pre-2026 periods, which are governed by the repealed Acts (not held).
- The unresolved text dispute (D-09). Any later re-gazetting means a new corpus edition.

## 5. Source files

Filenames follow `data/raw/<act>_<edition>_<host>.pdf`. Files 2 to 6 were downloaded on
2026-10-18 and are pinned in [data/manifest.csv](../../data/manifest.csv). File 1 doesn't
exist: the NRS "Nigeria Tax Act, 2025" page carried only a written overview and no PDF when
checked by hand on 2026-10-18.

| # | Filename | Source | Size | Commit? |
|---|---|---|---|---|
| 1 | (none) `nigeria_tax_act_2025_gazette_nrs.pdf` | Nigeria Revenue Service, "Tax Laws" page: overview only, no PDF published | - | - |
| 2 | `nigeria_tax_act_2025_gazette_lirs.pdf` | <https://www.lirs.gov.ng/assets/docs/Gazette%20-%20NIGERIA%20TAX%20ACT,%202025.pdf> | 1,757,316 B | yes |
| 3 | `nigeria_tax_act_2025_nass.pdf` | <https://nass.gov.ng/documents/download/11249> | 598,389 B | yes |
| 4 | `nigeria_tax_administration_act_2025_gazette_nrs.pdf` | <https://www.nrs.gov.ng/uploads/NIGERIA_TAX_ADMINISTRATION_ACT_2025_8c945071a7.pdf> | 27,677,259 B | **no** (10 MB or more: git-ignored, URL and hash recorded) |
| 5 | `nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf` | <https://irs.gm.gov.ng/docs/national/NIGERIA_TAX_ADMINISTRATION_ACT.pdf> | 874,593 B | yes |
| 6 | `nigeria_tax_administration_act_2025_nass.pdf` | <https://nass.gov.ng/documents/download/11250> | 291,016 B | yes |

Cited texts: for the Nigeria Tax Act, file 2, because no NRS copy is published (DQ-03). For the
Administration Act,
file 4 is cited: it is the NRS print, and it differs from the earlier Gombe print, file 5
(DQ-02). Files 3, 5 and 6 are comparison texts.
