# NTAA Proofread Report (Phase 2b-4)

The strict alignment in 2b-3 ([ntaa_alignment.csv](../../data/derived/ntaa_alignment.csv)) found
six in-scope sections where the cited NRS print of the Nigeria Tax Administration Act 2025
differs from the Gombe print: s11, s29, s34, s39, s41 and s147. Each was transcribed from the
NRS page images into
[data/curated/ntaa_nrs_proofread/](../../data/curated/ntaa_nrs_proofread/index.csv), then
checked against two independent readings: the Gombe print (text PDF) and the re-OCR of the NRS
scan.

**Status: reviewed.** Kene checked all six sections against the page images on 2026-10-18 and
approved them without corrections. P2 was decided as "keep 'amonth' exactly as printed".

## How each draft was checked

- Each draft was compared with the Gombe text and with the re-OCR text, on letters and digits
  only (the alignment's comparison). Every difference is listed in the tables below with the
  three readings side by side.
- A second comparison against the Gombe text included punctuation, with whitespace ignored. It
  found **no punctuation differences** other than the substantive ones below, so commas,
  semicolons and dashes in the drafts match the Gombe print.
- Classification:
  - **Print difference:** the NRS print and the Gombe print genuinely differ. In every case
    below, the draft and the re-OCR agree against Gombe. These go to the discrepancy register
    in 2b-6.
  - **OCR error:** the re-OCR is wrong, and the draft and Gombe agree.
- Normalisation applied (design section 1): ₦ written as a character, NFKC, spaces collapsed,
  end-of-line hyphenation rejoined. Labels are kept as printed. Marginal notes and running
  headers are excluded.

Not covered: differences in **spacing only**. The comparisons ignore whitespace, so a missing
space in the print would not be flagged. One such case was seen by eye and is listed in s29.

## Summary

| Section | NRS pages | Print differences | OCR errors | Draft against Gombe |
|---|---|---|---|---|
| s11 | 11-12 | 0 | 3 | identical |
| s29 | 21-22 | 2 (one is spacing only) | 1 | differs |
| s34 | 23-24 | 0 | 2 | identical |
| s39 | 26 | 2 | 0 | differs |
| s41 | 26-27 | 1 | 1 | differs |
| s147 | 69-74 | 2 | 1 | differs |
| **Total** | | **7** | **8** | |

### Print differences (input for the discrepancy register, 2b-6)

| # | Section | NRS page | NRS print (cited) | Gombe print | Type |
|---|---|---|---|---|---|
| P1 | s29(b)(ii) | 22 | "all transactions where the cumulative transactions in amount to ₦100,000,000 or more." | "all transactions where the cumulative transactions in a month amount to ₦100,000,000 or more." | wording ("a month" missing in NRS; likely a typesetting slip, but it changes the literal text) |
| P2 | s29(b)(i) | 22 | "…cumulative transactions in amonth amount to ₦25,000,000…" | "…in a month amount to ₦25,000,000…" | wording (spacing only; draft keeps "amonth" as printed) |
| P3 | s39(2) | 26 | "…shall be paid in that currency, or the Nigerian Naira at the prevailing exchange rate in the official foreign exchange market." | "…shall be paid in that currency." | **substantive** (payment in Naira allowed) |
| P4 | s39(3) | 26 | "all computations relating to tax and payments shall be in US Dollars." | "all computations relating to tax shall be in US Dollars." | **substantive** |
| P5 | s41(8) | 27 | (8) "A party dissatisfied with the decision of the High Court may appeal to the Court of Appeal…" (no deposit clause) | (8) "Where a taxpayer is dissatisfied with the judgement of the Tax Appeal Tribunal, it may appeal to the High court, provided that it shall pay 20% of the disputed amount…"; (9) "A party dissatisfied…" | **missing_content** (deposit clause absent; Gombe (9) is NRS (8)) |
| P6 | s147 "small business" | 73 | "gross turnover of ₦100,000,000 or less per annum" | "gross turnover of ₦50,000,000 or less per annum" | **substantive** |
| P7 | s147 "small business" | 73 | "…per annum with a total fixed assets less than ₦250,000,000" | "…per annum with total fixed assets less than ₦250,000,000" | wording |

## Section tables

Context is quoted from the draft. "Draft" is the transcription from the page image.

### s11, Income tax returns for companies (NRS pages 11-12)

| # | NRS page | Draft (from image) | Gombe | Re-OCR | Classification |
|---|---|---|---|---|---|
| 1 | 11 | "(f) a computation of the effective tax rate and additional tax payable," | same as draft | "() a computation…" (label lost) | OCR error |
| 2 | 12 | "(c) in the case of a company that permanently ceases to carry on trade" | same as draft | line missing | OCR error |
| 3 | 12 | "(6) For the purpose of this section – (a) every company shall designate…" | same as draft | "(D) every company…" | OCR error |

### s29, Information to be delivered by bankers and others (NRS pages 21-22)

| # | NRS page | Draft (from image) | Gombe | Re-OCR | Classification |
|---|---|---|---|---|---|
| 1 | 22 | "(ii) … cumulative transactions in amount to ₦100,000,000 or more." | "… in a month amount to ₦100,000,000 or more." | "…inamountto ₦100.000.000…" (agrees with draft) | print difference (P1) |
| 2 | 22 | "(i) … cumulative transactions in amonth amount to ₦25,000,000" | "… in a month amount to …" | "…in amonth amountto…" (agrees with draft) | print difference, spacing only (P2) |
| 3 | 21 | "(a) new customers ; and" | same as draft | "(D) )new customers" | OCR error |

### s34, Self-assessment of tax payable (NRS pages 23-24)

| # | NRS page | Draft (from image) | Gombe | Re-OCR | Classification |
|---|---|---|---|---|---|
| 1 | 23 | "…period to which the return relates. (3) A tax return in the approved form…" | same as draft | stray "1.3" before "(3)" | OCR error |
| 2 | 24 | "(c) reject the tax return and, to the best of its judgement, determine" | same as draft | label "(c)" lost | OCR error |

### s39, Currency of assessment and payment (NRS page 26)

| # | NRS page | Draft (from image) | Gombe | Re-OCR | Classification |
|---|---|---|---|---|---|
| 1 | 26 | "(2) … shall be paid in that currency, or the Nigerian Naira at the prevailing exchange rate in the official foreign exchange market." | "… shall be paid in that currency." | agrees with draft | print difference (P3) |
| 2 | 26 | "(3) … all computations relating to tax and payments shall be in US Dollars." | "… relating to tax shall be in US Dollars." | agrees with draft | print difference (P4) |

### s41, Revision of assessment in case of objection (NRS pages 26-27)

| # | NRS page | Draft (from image) | Gombe | Re-OCR | Classification |
|---|---|---|---|---|---|
| 1 | 27 | (8) "A party dissatisfied with the decision of the High Court may appeal to the Court of Appeal, while an appeal against the judgement of the Court of Appeal shall be to the Supreme Court." Section ends at (8). | (8) is the 20% deposit clause; (9) is "A party dissatisfied…" | agrees with draft | print difference (P5) |
| 2 | 27 | "(iii) justification for the amendments," | same as draft | "(ii) justification…" (an "i" lost) | OCR error |

### s147, Interpretation (NRS pages 69-74)

| # | NRS page | Draft (from image) | Gombe | Re-OCR | Classification |
|---|---|---|---|---|---|
| 1 | 73 | "small business" … "gross turnover of ₦100,000,000 or less per annum" | "₦50,000,000" | agrees with draft | print difference (P6) |
| 2 | 73 | "… per annum with a total fixed assets less than ₦250,000,000" | "with total fixed assets" | agrees with draft | print difference (P7) |
| 3 | 74 | "taxpayer information" (b) "any information in any form received…" | same as draft | "anyf form" | OCR error |

s147 has 55 definitions. Apart from "small business" (rows 1 and 2), the draft matches the
Gombe print word for word and punctuation for punctuation.

## Found while reading (outside the six sections)

- **NRS s148 (citation), page 74:** "…and shall come into effect on the 1st day of January,
  2025." The same print's long title (page 7) gives "[1st Day of January, 2026]". Proposed
  **DQ-12**. s148 is out of scope, but commencement questions depend on it.

## Review steps for Kene

1. Open the review pack folder `docs/phase02_chunking/proofread_pack/` (it is git-ignored and
   regenerated). Each NRS page has three images: `nrs_pNNN.png` (whole page),
   `nrs_pNNN_top.png` and `nrs_pNNN_bottom.png` (half pages at full resolution).
2. Open this report beside the images.
3. For each section, check every row in its table against the page named in the "NRS page"
   column. The draft column must match the image exactly.
4. For the seven print differences (P1 to P7), also confirm that the Gombe reading is genuinely
   different. Gombe page numbers: s29 on 23-24, s39 on 28, s41 on 28-29, s147 on 70-75 of the
   Gombe file.
5. Decide P2: keep "amonth" exactly as printed (recommended, because the cited text is quoted
   verbatim and the register discloses it), or write "a month". **Decided: keep as printed.**
6. Optionally skim each whole draft file in `data/curated/ntaa_nrs_proofread/` against its
   pages. The cross-checks above found no other differences.
7. Reply with one line per section, either `approved s011` or the correction (section, page,
   what the draft should say). For example: `approved s011, s034, s039; s029: keep amonth`.
8. After your reply, the status in `index.csv` is set to `reviewed`, with `reviewed_by` set to
   Kene and the date. The test suite turns green only when all six are reviewed.
