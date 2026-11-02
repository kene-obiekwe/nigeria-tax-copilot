# Discrepancy Register Review (Phase 2b-6)

**Estimated review time: about 2 hours.**

| Group | Rows | Time |
|---|---|---|
| A, substantive and missing content | 62 | about 90 minutes; check each one |
| B, numbering | 25 | about 10 minutes |
| C, wording | 187 | about 20 minutes; skim |

## Review outcome

Reviewed by Kene on 2026-10-31: groups A, B and C approved with the corrections below. The rows
further down show the register as it was sent for review. The register file holds the corrected
rows.

- **New legal-effect notes:** DR-122, DR-131, DR-178 and DR-261.
- **DR-029** moved from group C to group B (numbering). "Part IX of Chapter Two" in gazette s20(1)
  is a printing slip for Part XI (DQ-14). The groups are now A 62, B 26 and C 186.
- **DR-269 kept as wording:** it covers the kobo figures only. The "less than" against "not more
  than" ₦250,000,000 point is DR-270 (substantive).
- **"Cap. N" references:** the readings of DR-106, DR-108 and DR-131 now show "Cap. N107",
  "Cap. N3" and "Cap. N61" as printed, not "Cap. ₦…" ([build notes](build_notes.md), 2b-6).
- **Defaults kept (D-17); ids pinned (D-18); DQ-16 logged** ([decision log](../00_planning/decision_log.md)).

## What the register is for

Under D-09, answers cite the gazette text of the Nigeria Tax Act and the NRS print of the Nigeria
Tax Administration Act. When an answer cites a chunk that has a register row, it must also state
the other official reading ([chunking design](chunking_design.md), Section 6).

The register is `data/curated/discrepancy_register.csv`, and it has 274 rows. Each row names:
- the cited chunk (`cited_unit`);
- the comparison print;
- a short extract of each reading, with its pages;
- the type;
- for substantive and missing-content rows, a one-line note on the legal effect.

## How the rows were produced

1. **Candidates.** `python/ingest/build_register.py` compared the cited text of every in-scope
   section and schedule with each comparison print:
   - the National Assembly text for the Nigeria Tax Act;
   - the Gombe print and the National Assembly print for the Nigeria Tax Administration Act.

   Only letters, digits and "%" were compared, word by word, along the section map. Differences
   that vanish when spaces are removed ("amonth" against "a month") were not recorded, and nor
   were section numbers that follow the s89 offset. Each difference was placed in its cited chunk
   and subsection and given a suggested type. The drafting-style variants of the National
   Assembly print were suggested as wording:
   - "of this section" added;
   - ", 2025" dropped after an Act's name;
   - "Laws of the Federation of Nigeria" against "LFN";
   - "30 per cent" against "30%", and number words against digits;
   - kobo figures (₦100,000.00);
   - a lone "and" or "or" between list items.

   This gave 303 candidates:

   | Act | Comparison print | Candidates |
   |---|---|---|
   | Nigeria Tax Act | National Assembly | 200 |
   | Nigeria Tax Administration Act | Gombe | 6 |
   | Nigeria Tax Administration Act | National Assembly | 97 |

2. **Pre-review.** Every candidate was read against both texts and given a final type and, where
   needed, a note on the legal effect:
   - 31 candidates were merged into another row about the same point;
   - 1 was dropped, a National Assembly reference to the Nigeria Tax Act that follows the offset
     (covered by DR-000);
   - 3 rows were added from the data-quality log: DR-000 (the offset rule), DR-055 (s57 printed
     without (3)) and DR-190 (where NTAA Chapter Two starts).

   The decisions are in `data/curated/register_decisions.yml`, and the script will not build the
   register unless every candidate has one.
3. **Checks against the PDFs.** Every reading was matched against its pages, read directly from
   the PDF with pdfplumber. For the NRS scan the committed re-OCR text was used
   (`python/ingest/register_check.py`). Four readings could not be matched automatically, and
   they were checked against page images:
   - DR-075 and DR-117: a marginal note is printed inside a word;
   - DR-192 and DR-194: the OCR of NRS page 11 is weak.

   The NRS-side readings of every group A row were also checked against page images.

## Where the known items are

| Item | Rows |
|---|---|
| Section 89 offset (rule row) | DR-000 |
| DQ-01: small company ₦50m against ₦100m; professional services; s56 25% proviso | DR-150, DR-151; DR-050 |
| DQ-02: NTAA s29 thresholds and return frequency | DR-207, DR-208, DR-209 |
| DQ-06: Thirteenth Schedule products | DR-178 |
| DQ-07: s57 labels (1), (2), (4); ₦20bn against ₦50bn; other s57 differences | DR-055; DR-053; DR-051, DR-052, DR-054 |
| Subsection numbering found in 2b-2: s27, s57, s156, s198 | DR-040; DR-055; DR-067, DR-068; DR-115 |
| Subsection numbering found in 2b-2: s13 | No row: s13 is out of scope and has no chunk |
| P1, P3, P4, P5, P6, P7 (proofread report) | DR-206, DR-220, DR-221, DR-229, DR-267, DR-268 |
| P2 ("amonth" against "a month") | No row: a spacing difference, which design Section 6 does not record |
| DQ-12 (NTAA s148 commencement) | No row: s148 is out of scope |
| DQ-13 (NTAA Chapter Two placement) | DR-190 |

## Effect on the chunks

After the rebuild, 181 of 400 chunks are flagged: 130 NTA and 51 NTAA. 49 of them carry at least
one group A row. Wording rows also flag their chunks, as the design says.

## Review steps for Kene

1. Make sure the page crops are present in `docs/phase02_chunking/register_pack/` (they are
   git-ignored). If the folder is empty, run:

   ```
   .venv\Scripts\python python\ingest\register_pack.py
   ```

2. **Group A.** For each row:
   1. Open the two crops it names: the cited print first, then the comparison print.
   2. Check that both readings match the page.
   3. Check that the "Effect" line is a fair one-line statement of the legal difference.
   4. Note any row whose type or effect you would change.
3. **Group B.** Read each numbering note and confirm it.
4. **Group C.** Skim the table. Note any row that you think changes the legal effect; it then
   moves to group A.
5. Reply in chat in this format:

   ```
   approved A; approved B; approved C
   ```

   or with corrections by register id, for example:

   ```
   approved A except DR-052: type wording; approved B; approved C except DR-140: type substantive, note "…"
   ```

6. After your reply, the corrections are applied in `register_decisions.yml`. Then `review.by` is
   set to Kene and `review.on` to the date, which fills `reviewed_by` and `reviewed_on` on every
   row. The register and chunks are then rebuilt. Nothing is marked reviewed before your reply.

## A. Substantive and missing content (62 rows)

### Nigeria Tax Act 2025 (gazette against National Assembly)

**DR-001** · `nta2025:s4(1)-(5)` · substantive · against the National Assembly

- Effect: The gazette charges gains from transactions in digital or virtual assets; the National Assembly text names digital assets only.
- Gazette (LIRS), page 10: or gains from transactions in digital or virtual assets; and (k) any other income, profit or
- National Assembly, page 12: or gains from transactions in digital assets ; (k) any other income, profit or
- Crops: DR-001_cited_p010.png, DR-001_nass_p012.png

**DR-003** · `nta2025:s4(6)(b)-(e)` · missing_content · against the National Assembly

- Effect: Under the gazette a transfer of securities or shares under a regulated securities lending transaction is not a disposal; the National Assembly text has no such rule.
- Gazette (LIRS), page 11: bonds and any other money instrument; and (e) the transfer of securities or shares under a regulated securities lending transaction shall not amount to a disposal, provided that such securities or shares are transferred from a lender and subsequently returned by the borrower to the lender
- National Assembly, page 13: bonds and any other money instrument
- Crops: DR-003_cited_p011.png, DR-003_nass_p013.png

**DR-025** · `nta2025:s18(7)-(9)` · substantive · against the National Assembly

- Effect: The gazette accepts tax clearance certificates for the three preceding tax years as one of three alternative evidences; the National Assembly text lists only filing evidence and evidence of tax declaration and payment.
- Gazette (LIRS), page 24: filing for the preceding tax year; (b) tax clearance certificates, showing income taxes paid for the three preceding tax years; or (c) evidence of tax declaration and payment
- National Assembly, page 25: filing for the preceding tax year ; and (b) evidence of tax declaration and payment
- Crops: DR-025_cited_p024.png, DR-025_nass_p025.png

**DR-039** · `nta2025:s27(1)-(4)` · missing_content · against the National Assembly

- Effect: The National Assembly text requires companies in the priority sector to obtain a certificate of acceptance from the Industrial Inspectorate Department for qualifying capital expenditure; the gazette has no such condition.
- Gazette (LIRS), page 29: generating the assessable profits: Provided that where value added tax is due
- National Assembly, page 30: generating the assessable profits: Provided that –– (a) in the case of companies in the priority sector, certificate of acceptance issued by the Industrial Inspectorate Department shall be obtained inrespect of the qualifying capital expenditure ; and (b) where value added tax is due
- Crops: DR-039_cited_p029.png, DR-039_nass_p030.png

**DR-050** · `nta2025:s56` · missing_content · against the National Assembly · DQ-01

- Effect: The National Assembly text lets the President, on the advice of the National Economic Council, reduce the 30% rate to 25% by order; the gazette has no such provision.
- Gazette (LIRS), page 42: from the commencement of this Act
- National Assembly, page 44: from the commencement of this Act. Provided that the rate under shall be reduced to 25% effective from a date as may be determined in an order issued by the President on the advice of the National Economic Council
- Crops: DR-050_cited_p042.png, DR-050_nass_p044.png

**DR-051** · `nta2025:s57` · substantive · against the National Assembly · DQ-07

- Effect: The minimum tax is measured differently. Gazette: covered tax paid divided by profits, where profits are net profit before tax less 5% of depreciation and personnel cost. National Assembly: covered taxes (companies income tax, petroleum profit tax, hydrocarbon tax, development levy, priority sector tax credit) as a percentage of net income, which excludes franked investment income and unrealised gains or losses.
- Gazette (LIRS), page 42: "effective tax rate" means the rate produced by dividing the aggregate covered tax paid by a company for a year of assessment by the profits of the company; and "profits" means the net profits before tax as reported in the audited financial statement less 5% of depreciation and personnel cost for the year.
- National Assembly, page 44: the effective tax rate that is, covered taxes as a percentage of net income, of a company is less than 15% … (b) cover taxes for the purpose of this section means companies income tax, petroleum profit tax, and hydrocarbon tax paid or payable, development levy, and priority sector tax credit ; and (c) net income refers to the profit before tax as reported in the audited financial statements excluding franked investment income and unrealistic gains or losses
- Crops: DR-051_cited_p042.png, DR-051_nass_p044.png

**DR-052** · `nta2025:s57` · substantive · against the National Assembly · DQ-07

- Effect: The gazette applies the 15% minimum to every constituent entity of an MNE group; the National Assembly text only where the group's turnover is at least 750 million (printed with a pound sign) or its equivalent.
- Gazette (LIRS), page 42: that is a constituent entity of an MNE group; and (b) any other company with
- National Assembly, page 44: that is a constituent entity of a Multinational Entity group, with aggregate group turnover of at least £750 million or its equivalent ; and (b) any other company with
- Crops: DR-052_cited_p042.png, DR-052_nass_p044.png

**DR-053** · `nta2025:s57` · substantive · against the National Assembly · DQ-07

- Effect: The 15% minimum effective tax rate applies from an aggregate turnover of ₦20,000,000,000 in the gazette and ₦50,000,000,000 in the National Assembly text.
- Gazette (LIRS), page 42: company with an aggregate turnover of ₦20,000,000,000 and above in
- National Assembly, page 44: company with an aggregate turnover of ₦50,000,000,000 and above in
- Crops: DR-053_cited_p042.png, DR-053_nass_p044.png

**DR-054** · `nta2025:s57` · missing_content · against the National Assembly · DQ-07

- Effect: The National Assembly text exempts approved free-zone enterprises (except for sales into the customs territory and MNE entities) and lets the Service set a higher turnover threshold by regulation; the gazette has neither and measures turnover in the relevant year of assessment rather than the financial year.
- Gazette (LIRS), page 42: ₦20,000,000,000 and above in the relevant year of assessment. (4) For the purposes of this section –
- National Assembly, page 44: and above in the financial year. (3) The provisions of subsection (1) (a) of this section shall not apply to an approved enterprise operating in a free zone with rspect to its approved activities except — (a) with respect to sale within the customs territory ; (b) an entity as prescribed under subsection (2) (a) of this section. (4) The service may issue regulations to give effect to the provisions under this section and may prescribe a higher threshold under subsection (2) (b) of this section.
- Crops: DR-054_cited_p042.png, DR-054_nass_p044.png

**DR-067** · `nta2025:s156` · missing_content · against the National Assembly · 2b-2 s156

- Effect: The gazette lets a person whose supplies are zero-rated pay VAT on its inputs and then claim a refund; the National Assembly text has no such subsection.
- Gazette (LIRS), page 91: the VAT on the goods. (4) A person whose supplies are chargeable to VAT at 0%, shall pay VAT on taxable supplies consumed in the production of its supplies, and may thereafter request for a refund of the VAT paid. (5) Input tax incurred by a registered
- National Assembly, page 94: the VAT on the goods. (4) Input tax incurred by a registered
- Crops: DR-067_cited_p091.png, DR-067_nass_p094.png

**DR-074** · `nta2025:s163(1)(m)-(v)` · missing_content · against the National Assembly

- Effect: The gazette exempts a Nigerian company's profits from goods or services exported from Nigeria (other than petroleum companies) if the export proceeds are repatriated through official channels; the National Assembly text has no such exemption.
- Gazette (LIRS), page 94: wages and salaries of military officers; and (v) the profits of any Nigerian company (other than companies engaged in the upstream, midstream or downstream petroleum operations) in respect of goods or services exported from Nigeria if the proceeds of such exports are repatriated through official channels
- National Assembly, page 97: wages and salaries of military officers. (2) The following shall not constitute
- Crops: DR-074_cited_p094.png, DR-074_nass_p097.png

**DR-078** · `nta2025:s186` · missing_content · against the National Assembly

- Effect: The National Assembly text requires the humanitarian donor to pay the VAT first and request a refund from the Service; the gazette exempts the supply without that condition.
- Gazette (LIRS), page 105: use in humanitarian donor funded projects; (d) baby products; (e) locally manufactured
- National Assembly, page 108: use in humanitarian donor funded projects provided that the humanitarian donor shall first pay the VAT and request a refund from the Service ; (d) baby products ; (e) locally manufactured
- Crops: DR-078_cited_p105.png, DR-078_nass_p108.png

**DR-079** · `nta2025:s186` · substantive · against the National Assembly

- Effect: The National Assembly text adds 'stakes' to the VAT-exempt item for money and securities.
- Gazette (LIRS), page 106: in land or building; (m) money or securities including interest in money
- National Assembly, page 109: in land or building ; (m) money, stakes or securities including interest in money
- Crops: DR-079_cited_p106.png, DR-079_nass_p109.png

**DR-082** · `nta2025:s187` · missing_content · against the National Assembly

- Effect: The gazette zero-rates electric vehicles and parts and semi-knock-down units for assembling them; the National Assembly text does not list them.
- Gazette (LIRS), page 107: exported incorporeal property; (q) medical equipment; (r) electric vehicles; and (s) parts, semi-knock-down units for the assembly of Electric Vehicles
- National Assembly, page 110: exported incorporeal property ; (q) medical equipment
- Crops: DR-082_cited_p107.png, DR-082_nass_p110.png

**DR-083** · `nta2025:s189:def:agricultural-seeds-and-seedlings` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'agricultural seeds and seedlings'.
- Gazette (LIRS), page 107: "agricultural seeds and seedlings" refer to seeds and seedlings for the cultivation of agricultural plants and crops;
- National Assembly, page (none): (not in this print)
- Crops: DR-083_cited_p107.png

**DR-089** · `nta2025:s189:def:locally-produced-animal-feeds` · substantive · against the National Assembly

- Effect: The gazette's locally produced animal feeds are feeds for poultry, cattle or fish only; the National Assembly text adds 'etc.', leaving the list open.
- Gazette (LIRS), page 109: feeds" means feeds for poultry, cattle or fish
- National Assembly, page 113: feeds" means feeds for poultry, cattle, fish, etc
- Crops: DR-089_cited_p109.png, DR-089_nass_p113.png

**DR-095** · `nta2025:s189:def:shared-passenger-road-transport-service` · missing_content · against the National Assembly

- Effect: Only the National Assembly text defines 'stake'.
- Gazette (LIRS), page (none): (not in this print)
- National Assembly, page 114: "Stake" means amount waged on a game ;
- Crops: DR-095_nass_p114.png

**DR-098** · `nta2025:s190(1)(b)-(c)` · missing_content · against the National Assembly

- Effect: The gazette applies the chargeable gains provisions to assets sold or transferred in a business restructuring; the National Assembly text omits this item.
- Gazette (LIRS), page 111: they are sold or transferred, (iii) the provisions of Part VIII of Chapter two of this Act as they relate to chargeable gains shall apply on any asset sold or transferred, (iv) unutilised capital allowance on the assets
- National Assembly, page 115: they are sold or transferred, (iii) unutilised capital allowance on the assets
- Crops: DR-098_cited_p111.png, DR-098_nass_p115.png

**DR-104** · `nta2025:s196` · missing_content · against the National Assembly

- Effect: The gazette also repeals the Taxes and Levies (Approved List for Collection) Act; the National Assembly text does not.
- Gazette (LIRS), page 114: i) Stamp Duties Act, Cap. S8, Laws of the Federation of Nigeria, 2004; (j) Taxes and Levies (Approved List for Collection) Act, Cap. T2, Law of the Federation of Nigeria, 2004 ; (k) Value Added Tax Act, Cap. V1
- National Assembly, page 117: i) Stamp Duties Act, Cap. S8, LFN, 2004 ; (j) Value Added Tax Act, Cap. V1
- Crops: DR-104_cited_p114.png, DR-104_nass_p117.png

**DR-112** · `nta2025:s197(10)-(14)` · substantive · against the National Assembly

- Effect: The gazette amends section 17(1) of the Students Loans (Access to Higher Education) Act, 2024; the National Assembly text deletes paragraphs (c) and (d) of section 12 of the Act No. 69 of 2023.
- Gazette (LIRS), page 115: Loans (Access to Higher Education) Act, 2024 is amended by deleting section 17 … Act, 2024 is amended by deleting section 17 (1). (14) The Cybercrime (Prohibition, Prevention, Etc
- National Assembly, page 118: Loans (Access to Higher Education) Act, No. 69, 2023 is amended by deleting paragraphs (c … 69, 2023 is amended by deleting paragraphs (c) and (d) of Section 12. (14) The Cybercrime (Prohibition, Prevention, Etc
- Crops: DR-112_cited_p115.png, DR-112_nass_p118.png

**DR-114** · `nta2025:s198` · missing_content · against the National Assembly

- Effect: The National Assembly text also revokes the Oil and Gas Companies (Tax Incentives, Exemptions, Remissions, etc.) Orders, S.I. No. 21 of 2024 and S.I. No. 1 of 2024; the gazette does not.
- Gazette (LIRS), page 116: Added Tax Act (Modification) Order, 2021 is revoked. (2) The Companies Income Tax (Significant Economic
- National Assembly, page 119: Added Tax Act (Modification) Order 2021 ; (b) Oil and Gas Companies (Tax Incentives, Exemptions, Remissions, etc.) Order, S.I No. 21, 2024 ; and (c) Oil and Gas Companies (Tax Incentives, Exemptions, Remissions, etc.) Order, S.I No. 1, 2024 ; (d) the Companies Income Tax (Significant Economic
- Crops: DR-114_cited_p116.png, DR-114_nass_p119.png

**DR-122** · `nta2025:s202:def:bank` · substantive · against the National Assembly

- Effect: The gazette defines 'bank' by the Banks and Other Financial Institutions Act No. 3, 2020; the National Assembly text by Cap. B3, LFN 2004, which that Act replaced.
- Gazette (LIRS), page 118: Banks and Other Financial Institutions Act, Act No. 3, 2020
- National Assembly, page 121: Banks and Other Financial Institutions Act, Cap. B3, Laws of the Federation of Nigeria, 2004
- Crops: DR-122_cited_p118.png, DR-122_nass_p121.png

**DR-124** · `nta2025:s202:def:company` · substantive · against the National Assembly

- Effect: The gazette's 'company' includes a limited liability partnership; the National Assembly text's does not.
- Gazette (LIRS), page 118: company" a company or corporation, including limited liability partnership, established by or under any law
- National Assembly, page 122: company" a company or corporation, established by or under any law
- Crops: DR-124_cited_p118.png, DR-124_nass_p122.png

**DR-125** · `nta2025:s202:def:communication-infrastructure` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'communication infrastructure'.
- Gazette (LIRS), page 118: "communication infrastructure'' means telecommunications fibre optics, satellite cellular networks, broadband systems radio transmission towers, undersea cables and other similar infrastructure;
- National Assembly, page (none): (not in this print)
- Crops: DR-125_cited_p118.png

**DR-131** · `nta2025:s202:def:national-minimum-wage` · substantive · against the National Assembly

- Effect: The gazette ties the national minimum wage to the National Minimum Wage (Amendment) Act 2024; the National Assembly text to the 2004 Act, so the wage referred to may differ.
- Gazette (LIRS), page 122: prescribed by the National Minimum Wage (Amendment) Act 2024
- National Assembly, page 125: prescribed by the National Minimum Wage Act Cap. ₦61, Laws of the Federation of Nigeria, 2004
- Crops: DR-131_cited_p122.png, DR-131_nass_p125.png

**DR-134** · `nta2025:s202:def:participation-directly-or-indirectly-in-the-management-control-or-capital-of-the-other-person` · missing_content · against the National Assembly

- Effect: Only the gazette defines when a person participates in the management, control or capital of another (a 30% control test).
- Gazette (LIRS), page 123: "participation, directly or indirectly, in the management, control, or capital of the other person" means where a person either alone
- National Assembly, page (none): (not in this print)
- Crops: DR-134_cited_p123.png

**DR-137** · `nta2025:s202:def:priority-product` · missing_content · against the National Assembly

- Effect: Only the National Assembly text defines 'practical ability to control'.
- Gazette (LIRS), page (none): (not in this print)
- National Assembly, page 127: "practical ability to control" shall include voting right, management contract, sole distributorship and representative arrangement, and the like ;
- Crops: DR-137_nass_p127.png

**DR-138** · `nta2025:s202:def:professional-services` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'professional services', the term that excludes a business from being a small company.
- Gazette (LIRS), page 124: "professional services" means services provided by an individual or a firm having specialised knowledge, skills, and qualifications in specific fields,
- National Assembly, page (none): (not in this print)
- Crops: DR-138_cited_p124.png

**DR-141** · `nta2025:s202:def:regulated-securities-lending-transaction` · substantive · against the National Assembly

- Effect: The gazette limits the term to a 'Tenth Schedule' securities lending transaction; the National Assembly text covers any securities lending transaction conducted under SEC rules.
- Gazette (LIRS), page 125: regulated securities lending transaction'' means any Tenth Schedule securities lending transaction conducted under rules
- National Assembly, page 128: regulated Securities Lending Transaction'' any securities lending transaction conducted under rules
- Crops: DR-141_cited_p125.png, DR-141_nass_p128.png

**DR-144** · `nta2025:s202:def:remainder-of-assessable-income` · missing_content · against the National Assembly

- Effect: The National Assembly text has no definition of 'remainder of assessable income'; it prints that definition under the term 'remainder of assessable profit'.
- Gazette (LIRS), page 125: "remainder of assessable income" means the total assessable income of an individual less the deductions under section 28(2) of this
- National Assembly, page (none): (not in this print)
- Crops: DR-144_cited_p125.png

**DR-145** · `nta2025:s202:def:remainder-of-assessable-profit` · substantive · against the National Assembly

- Effect: The National Assembly text defines 'remainder of assessable profit' twice: once as an individual's assessable income less section 28(2) deductions, once as a company's assessable profit less section 27(5) deductions.
- Gazette (LIRS), page 125: assessable profit" means the total assessable profit of a company less the deductions under section 27 … company less the deductions under section 27(5) of this Act, in the case of a company
- National Assembly, page 128: assessable profit" means the total assessable income of an individual less the deductions under section 28 … individual less the deductions under section 28(2) of this Act … "remainder of assessable profit" means the total assessable profit of a company less the deductions under subsection (5) of section
- Crops: DR-145_cited_p125.png, DR-145_nass_p128.png

**DR-147** · `nta2025:s202:def:royalties` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'royalties'.
- Gazette (LIRS), page 125 126: "royalties" includes payments of any kind, received as a consideration for– (a) the use of, or the right to use
- National Assembly, page (none): (not in this print)
- Crops: DR-147_cited_p125.png

**DR-149** · `nta2025:s202:def:services` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'services' for the VAT Part.
- Gazette (LIRS), page 126: "services" for the purposes of Part IV of Chapter Eight of this Act, means – (a) anything, other than goods,
- National Assembly, page (none): (not in this print)
- Crops: DR-149_cited_p126.png

**DR-150** · `nta2025:s202:def:small-company` · substantive · against the National Assembly · DQ-01

- Effect: A small company has gross turnover of ₦50,000,000 or less in the gazette and ₦100,000,000 or less in the National Assembly text, which also says 'business' instead of 'company'.
- Gazette (LIRS), page 126: company that earns gross turnover of ₦50,000,000 or less per annum … small company" means a company that earns gross turnover of ₦50
- National Assembly, page 129: business that earns gross turnover of ₦100,000,000 or less per annum … small company" a business that earns gross turnover of ₦100
- Crops: DR-150_cited_p126.png, DR-150_nass_p129.png

**DR-151** · `nta2025:s202:def:small-company` · missing_content · against the National Assembly · DQ-01

- Effect: The gazette excludes any business providing professional services from being a small company; the National Assembly text has no such exclusion.
- Gazette (LIRS), page 126: assets not exceeding ₦250,000,000, provided that any business providing professional services shall not be classified as a small company
- National Assembly, page 129: assets not exceeding ₦250,000,000
- Crops: DR-151_cited_p126.png, DR-151_nass_p129.png

**DR-152** · `nta2025:s202:def:supplies` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'supplies'.
- Gazette (LIRS), page 126: "supplies" means any transaction, whether it is the sale of goods or the performance of a service for a consideration;
- National Assembly, page (none): (not in this print)
- Crops: DR-152_cited_p126.png

**DR-153** · `nta2025:s202:def:supply-of-goods` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'supply of goods'.
- Gazette (LIRS), page 126: "supply of goods" means any transaction where the property in the goods is transferred or where the agreement expressly contemplates
- National Assembly, page (none): (not in this print)
- Crops: DR-153_cited_p126.png

**DR-154** · `nta2025:s202:def:supply-of-services` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'supply of services'.
- Gazette (LIRS), page 126: "supply of services" means any service provided for a consideration;
- National Assembly, page (none): (not in this print)
- Crops: DR-154_cited_p126.png

**DR-157** · `nta2025:s202:def:taxable-person` · substantive · against the National Assembly

- Effect: The gazette's 'taxable person' includes any person who earns income; the National Assembly text's only a person who carries out an economic activity.
- Gazette (LIRS), page 126: or any other legal arrangement, or any person who earns income, or carries out an economic activity, a
- National Assembly, page 129: or any other legal arrangement, or a person who carries out an economic activity, a
- Crops: DR-157_cited_p126.png, DR-157_nass_p129.png

**DR-161** · `nta2025:s202:def:venture-capitalist` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'venture capitalist'.
- Gazette (LIRS), page 127: "venture capitalist" is as defined in the Nigeria Startup Act No. 32, 2022;
- National Assembly, page (none): (not in this print)
- Crops: DR-161_cited_p127.png

**DR-164** · `nta2025:s203` · missing_content · against the National Assembly

- Effect: The gazette's citation section says the Act comes into effect on 1 January 2026; the National Assembly text's gives no date.
- Gazette (LIRS), page 127: as the Nigeria Tax Act, 2025 and shall come into effect on the first day of January, 2026
- National Assembly, page 129: as the Nigeria Tax Act, 2025
- Crops: DR-164_cited_p127.png, DR-164_nass_p129.png

**DR-176** · `nta2025:sch3` · missing_content · against the National Assembly

- Effect: Only the gazette defines 'debt' for the deductible-interest rule (loan, financial instrument, finance lease, financial derivative or similar).
- Gazette (LIRS), page 153: excess interest expenditure was first computed. 5. For the purposes of this Schedule, the expression "debt" means any loan, financial instrument, finance lease, financial derivative, or any arrangement that gives rise to interest, discounts or other finance charges that are deductible in the computation of income chargeable under the head "profits and gains of business or profession
- National Assembly, page 155: excess interest expenditure was first computed
- Crops: DR-176_cited_p153.png, DR-176_nass_p155.png

**DR-178** · `nta2025:sch13:table1` · missing_content · against the National Assembly · DQ-06

- Effect: The gazette's 'Exempted products' column is empty. The National Assembly text lists products for each sub-sector and adds sub-sector names for 'Livestock processing' and 'Cocoa processing' and an 'Animal feeds' row.
- Gazette (LIRS), page 209: Exempted products: (none printed)
- National Assembly, page 209: Crop Production: All crops; Livestock: Cattle, swine/pigs, sheep, goat, and poultry, including processed eggs; Livestock processing: Meat and poultry processing; Forestry: Latex and gum arabic; Dairy: Fresh liquid milk … Cheese, curd and lactose; Cocoa processing: Cocoa, cocoa butter, cocoa fat, cocoa oil; Chocolate; Animal feeds (Manufacture of animal feeds): Animal feed, edible oils and by-products, concentrates, grain mill products, and feed supplements (cells as printed, joined row by row)
- Crops: DR-178_cited_p209.png, DR-178_nass_p209.png

### Nigeria Tax Administration Act 2025 (NRS print against Gombe and National Assembly)

**DR-179** · `ntaa2025:s3(1)` · substantive · against the National Assembly

- Effect: In the cited text the Service has exclusive responsibility for the surcharge on fossil fuels; the National Assembly text omits it, and adds taxation of income from petroleum and value added tax to the taxes the Service has power to administer.
- NRS print, page 7 8: 1) The Nigeria Revenue Service (in this Act referred to as "the Service") established under the Nigeria … carrying out specialised trade or business, tax of income from petroleum operations … tax of income from petroleum operations, surcharge on fossil fuels, value added tax, economic development tax … tax, economic development tax incentive, and exemptions from value added tax ; (b) have … following taxes— (i) income tax, (ii) stamp duties, (iii) tax incentives ; and (c) exercise such other powers and … the Nigeria Revenue Service (Establishment) Act, 2025, and any other law as may
- National Assembly, page 9 10: 1) The Nigeria Revenue Service (the Service) established under the Nigeria … carrying out specialised trade or business as contained in this Act, tax of income from petroleum operations … tax of income from petroleum operations, value added tax, economic development tax … tax, economic development tax incentive, and exemption from value added tax; (b) have … following taxes — (i) income tax, (ii) taxation of income from petroleum, (iii) stamp duties, (iv) value added tax, and (v) tax incentives ; (c) exercise such other powers and … the Nigeria Revenue Service (Establishment) Act, and any other law as may
- Crops: DR-179_cited_p007.png, DR-179_nass_p009.png

**DR-186** · `ntaa2025:s8` · substantive · against the National Assembly

- Effect: The cited text requires financial institutions to ensure every taxable person provides a Tax ID; the National Assembly text makes a Tax ID a precondition for opening or operating any account.
- NRS print, page 9: other financial services in Nigeria shall ensure that every taxable person provides a Tax ID
- National Assembly, page 12: other financial services in Nigeria shall make the provision of a tax ID precondition for opening any account or operating an exiting account
- Crops: DR-186_cited_p009.png, DR-186_nass_p012.png

**DR-207** · `ntaa2025:s29` · substantive · against the National Assembly · DQ-02

- Effect: The cited text requires quarterly returns to the relevant tax authority of the names and addresses of new and existing customers; the National Assembly text requires annual returns to the Service of names, location and transactions.
- NRS print, page 21: with or without demand by the relevant tax authority, quarterly returns to the relevant tax authority specifying the names and addresses of – (a) new customers ; and (b) existing customers in the case of … Without prejudice to section 142 of
- National Assembly, page 24: with or without demand by the Service, annual returns specifying the names, customer location and transactions of new and existing customers in the case of … 1) For the purposes of tax and without prejudice to section 142 of
- Crops: DR-207_cited_p021.png, DR-207_nass_p024.png

**DR-208** · `ntaa2025:s29` · substantive · against the National Assembly · DQ-02

- Effect: The individual threshold is cumulative transactions of ₦25,000,000 a month in the cited text and ₦50,000,000 in the National Assembly text.
- NRS print, page 22: transactions where the cumulative transactions in amonth amount to ₦25,000,000 or more, or (ii) a body corporate, all transactions where the cumulative transactions in amount … existing customers in the case of – (i) an individual, all transactions where the cumulative transactions in amonth
- National Assembly, page 24: individual, where the cumulative transactions in a month amount to ₦50,000,000.00 or more ; or (b) a body corporate, where the cumulative transactions in a … existing customers in the case of — (a) an individual, where the cumulative transactions in a
- Crops: DR-208_cited_p022.png, DR-208_nass_p024.png

**DR-209** · `ntaa2025:s29` · substantive · against the National Assembly · DQ-02

- Effect: The body corporate threshold is ₦100,000,000 in the cited text (printed without 'a month') and ₦250,000,000 a month in the National Assembly text, which also adds subsections (2) to (4) on prescribed returns and information notices.
- NRS print, page 22: transactions where the cumulative transactions in amount to ₦100,000,000 or more
- National Assembly, page 24: corporate, where the cumulative transactions in a month amount to ₦250,000,000.00 or more. (2) Every bank, insurance company, stock-broking firm, other financial institution, or any other legal arrangement shall, as may be prescribed by way of notice, rules, regulations, guidelines, or circulars issued by the relevant tax authority prepare and submit returns of — (a) transactions involving the specified sum ; (b) names, addresses, including foreign addresses, or any other information of its customers connected with those transactions ; or (c) names, addresses, or any other information of new or existing customers. (3) Without prejudice to subsections (1) and (2) of this section, for the purpose of obtaining information relative to taxation, the relevant tax authority may give notice to any person including a person engaged in banking business in Nigeria to provide within the time stipulated in the notice, information including the name and address of any person specified in the notice. (4) Notwithstanding the provision of subsection (3) of this section a person engaged in banking business in Nigeria, shall not be required to disclose any additional information about his customer or his bank under this section unless such additional disclosure is required by a notice signed by the Chief Executive Officer of the relevant tax authority
- Crops: DR-209_cited_p022.png, DR-209_nass_p024.png

**DR-220** · `ntaa2025:s39` · substantive · against the Gombe print · P3

- Effect: The cited print lets tax assessed in a foreign currency be paid in Naira at the official exchange rate; the Gombe print requires payment in that currency.
- NRS print, page 26: shall be paid in that currency, or the Nigerian Naira at the prevailing exchange rate in the official foreign exchange market. (3) In the case of any
- Gombe print, page 28: shall be paid in that currency. (3) In the case of any
- Crops: DR-220_cited_p026.png, DR-220_gombe_p028.png

**DR-221** · `ntaa2025:s39` · substantive · against the Gombe print · P4

- Effect: For petroleum operations the cited print puts both tax computations and payments in US Dollars; the Gombe print only computations.
- NRS print, page 26: operations, all computations relating to tax and payments shall be in US Dollars
- Gombe print, page 28: operations, all computations relating to tax shall be in US Dollars
- Crops: DR-221_cited_p026.png, DR-221_gombe_p028.png

**DR-222** · `ntaa2025:s39` · substantive · against the National Assembly

- Effect: The cited print lets tax assessed in a foreign currency be paid in Naira at the official exchange rate; the National Assembly text requires payment in that currency.
- NRS print, page 26: shall be paid in that currency, or the Nigerian Naira at the prevailing exchange rate in the official foreign exchange market. (3) In the case of any
- National Assembly, page 29: shall be paid in that currency. (3) In the case of any
- Crops: DR-222_cited_p026.png, DR-222_nass_p029.png

**DR-223** · `ntaa2025:s39` · substantive · against the National Assembly

- Effect: For petroleum operations the cited print puts tax computations and payments in US Dollars; the National Assembly text puts computations in the currency of transaction.
- NRS print, page 26: relating to petroleum operations, all computations relating to tax and payments shall be in US Dollars
- National Assembly, page 29: relating to petroleum operations, all computations shall be in currency of transaction
- Crops: DR-223_cited_p026.png, DR-223_nass_p029.png

**DR-229** · `ntaa2025:s41(5)-(8)` · missing_content · against the Gombe print · P5

- Effect: The Gombe print requires a taxpayer appealing from the Tax Appeal Tribunal to the High Court to pay 20% of the disputed amount first; the cited print has no deposit clause, and Gombe's (9) is its (8).
- NRS print, page 27: exercise the right of appeal. (8) A party dissatisfied with the decision
- Gombe print, page 29: exercise the right of appeal. (8) Where a taxpayer is dissatisfied with the judgement of the Tax Appeal Tribunal, it may appeal to the High court, provided that it shall pay 20% of the disputed amount into an account designated by the High Court as security before the hearing of the appeal, and include the evidence of payment while filing the notice of the appeal. (9) A party dissatisfied with the decision
- Crops: DR-229_cited_p027.png, DR-229_gombe_p029.png

**DR-231** · `ntaa2025:s41(5)-(8)` · missing_content · against the National Assembly

- Effect: The cited print's s41(8) provides appeals from the High Court to the Court of Appeal and then the Supreme Court; the National Assembly text has no such subsection.
- NRS print, page 27: may exercise the right of appeal. (8) A party dissatisfied with the decision of the High Court may appeal to the Court of Appeal, while an appeal against the judgement of the Court of Appeal shall be to the Supreme Court
- National Assembly, page 30: may exercise the right of appeal
- Crops: DR-231_cited_p027.png, DR-231_nass_p030.png

**DR-243** · `ntaa2025:s65` · substantive · against the National Assembly

- Effect: Interest is SOFR plus 10% or such other spread as the authority determines in the cited text; the National Assembly text says SOFR plus a spread to be determined, without the 10%.
- NRS print, page 40: prevailing Secured Overnight Financing Rate ("SOFR") or any successor rate, plus 10% or such other spread to be determined by the
- National Assembly, page 43: prevailing Secured Overnight Financing Rate ("SOFR") orany successor rate, plus spread to be determined by the
- Crops: DR-243_cited_p040.png, DR-243_nass_p043.png

**DR-245** · `ntaa2025:s65` · missing_content · against the National Assembly

- Effect: Only the cited text makes the interest in subsection (1)(b) and (c) compound and applies it to tax debts unpaid at commencement.
- NRS print, page 41: Act or any other tax law. (4) For the purpose of this section, the interest specified in subsection (1)(b) and (c) shall be compound interest and shall apply to all tax debts that remain unpaid as at the commencement of this Act
- National Assembly, page 43: Act or any other tax law
- Crops: DR-245_cited_p041.png, DR-245_nass_p043.png

**DR-254** · `ntaa2025:s121` · missing_content · against the National Assembly

- Effect: The National Assembly text adds s121(2): the penalty does not stop the tax authority prosecuting the person.
- NRS print, page 58 59: Bank of Nigeria Monetary Policy Rate … A person that receives a refund
- National Assembly, page 61: Bank of Nigeria Monetary Policy Rate. (2) The provision in subsection (1) of this section shall not be construed as preventing the relevant tax authority from prosecuting that person in accordance with the relevant provisions of this Act … 1) A person that receives a refund
- Crops: DR-254_cited_p058.png, DR-254_nass_p061.png

**DR-261** · `ntaa2025:s147:def:authorised-officer` · substantive · against the National Assembly

- Effect: Officers are authorised by the Board or the Chairman in the cited text, and by the Board or the Chief Executive Officer in the National Assembly text.
- NRS print, page 70: authorised by the Board or the Chairman to perform or carry out specific … this Act, the Nigeria Tax Act, 2025, or any other tax law enacted
- National Assembly, page 72: authorised by the Board or the Chief Executive Officer to perform or carry out specific … this Act, the Nigeria Tax Act or any other tax law enacted
- Crops: DR-261_cited_p070.png, DR-261_nass_p072.png

**DR-262** · `ntaa2025:s147:def:calendar-year` · missing_content · against the National Assembly

- Effect: Only the National Assembly text defines 'Chief Executive Officer'.
- NRS print, page (none): (not in this print)
- National Assembly, page 73: "Chief Executive Officer" means the chief executive officer of the relevant tax authority ;
- Crops: DR-262_nass_p073.png

**DR-265** · `ntaa2025:s147:def:employee-of-the-tax-authority` · substantive · against the National Assembly

- Effect: The cited text's employees of the tax authority include the Chairman; the National Assembly text's the Chief Executive Officer.
- NRS print, page 71: of the tax authority" includes the Chairman or other members of staff and
- National Assembly, page 74: of the tax authority" includes the Chief Executive Officer or other members of staff and
- Crops: DR-265_cited_p071.png, DR-265_nass_p074.png

**DR-267** · `ntaa2025:s147:def:small-business` · substantive · against the Gombe print · P6

- Effect: A small business has gross turnover of ₦100,000,000 or less in the cited print and ₦50,000,000 or less in the Gombe print.
- NRS print, page 73: business that earns gross turnover of ₦100,000,000 or less per annum
- Gombe print, page 74: business that earns gross turnover of ₦50,000,000 or less per annum
- Crops: DR-267_cited_p073.png, DR-267_gombe_p074.png

**DR-270** · `ntaa2025:s147:def:small-business` · substantive · against the National Assembly

- Effect: Total fixed assets must be less than ₦250,000,000 in the cited text and not more than ₦250,000,000 in the National Assembly text, so exactly ₦250,000,000 qualifies only under the National Assembly text.
- NRS print, page 73: 000 or less per annum with a total fixed assets less than ₦250,000,000, provided that
- National Assembly, page 76: 00 or less per annum with total fixed assets not more than ₦250,000,000.00 provided
- Crops: DR-270_cited_p073.png, DR-270_nass_p076.png

## B. Numbering (25 rows)

| ID | Unit | Against | Note |
|---|---|---|---|
| DR-000 | `nta2025:s89` | National Assembly | Rule row. The National Assembly text has no counterpart of gazette s89 (Petroleum royalty), so from s90 on each gazette section n is National Assembly section n - 1 (data/derived/section_map.csv). Section-number differences that follow this rule, including cross-references, are not listed separately. |
| DR-007 | `nta2025:s8` | National Assembly | Cross-reference: the gazette cites section 51 of the Nigeria Tax Administration Act, the National Assembly text section 50. |
| DR-011 | `nta2025:s10(7)` | National Assembly | Cross-reference: the gazette gives the right of appeal under Chapter Two of the Nigeria Tax Administration Act, the National Assembly text under Chapter Four. |
| DR-013 | `nta2025:s17(4)-(5)` | National Assembly | Cross-reference: section 51 (gazette) against section 50 (National Assembly) of the Nigeria Tax Administration Act. |
| DR-027 | `nta2025:s19` | National Assembly | Cross-reference: section 51 (gazette) against section 50 (National Assembly) of the Nigeria Tax Administration Act. |
| DR-032 | `nta2025:s20(2)-(4)` | National Assembly | Cross-reference: the gazette cites section 193 of this Act; the National Assembly text cites section 194, which is gazette section 195. |
| DR-033 | `nta2025:s21(a)-(o)` | National Assembly | Cross-reference: section 20(1)(h) (gazette) against section 20(j) (National Assembly). |
| DR-040 | `nta2025:s27(5)-(6)` | National Assembly | The gazette's s27(5) proviso is a separate subsection (6) in the National Assembly text, so gazette (6) is National Assembly (7). |
| DR-055 | `nta2025:s57` | National Assembly | The gazette numbers the subsections (1), (2) and (4), with no (3); the labels are kept as printed. The National Assembly text has (1) to (4). |
| DR-059 | `nta2025:s148` | National Assembly | Cross-reference: VAT is charged subject to Part IV of Chapter Eight (gazette) against Part IX of this Chapter (National Assembly). |
| DR-061 | `nta2025:s154` | National Assembly | Cross-reference: the rate is in section 148 (gazette); the National Assembly text cites section 146, which is not its rate section (147). |
| DR-068 | `nta2025:s156` | National Assembly | Because of the gazette's extra subsection (4), gazette (5) and (6) are National Assembly (4) and (5). |
| DR-069 | `nta2025:s157` | National Assembly | Cross-reference: section 190 (gazette) against section 191 (National Assembly, which is gazette section 192). |
| DR-071 | `nta2025:s163(1)(d)-(l)` | National Assembly | Cross-reference: dividends 'under section 4 of this Act' (gazette) against 'section (5)(2)(c)' (National Assembly). |
| DR-075 | `nta2025:s163(4)` | National Assembly | The definitions of 'other rank' and 'personal emoluments' sit in s163(4) in the gazette and in the section corresponding to gazette s164 in the National Assembly text. |
| DR-081 | `nta2025:s187` | National Assembly | Cross-reference: the gazette refers to paragraph 2 of the 'Thirtenth' Schedule, which has no paragraph 2; the National Assembly text refers to the Tenth Schedule. |
| DR-115 | `nta2025:s198` | National Assembly | The gazette sets out s198 as subsections (1) to (3); the National Assembly text as one subsection with paragraphs (a) to (e). |
| DR-116 | `nta2025:s199` | National Assembly | Cross-reference: the savings clause cites sections 196 and 197 (gazette) against 197 and 198 (National Assembly), which do not match that text's own repeal and amendment sections. |
| DR-143 | `nta2025:s202:def:relevant-tax-authority` | National Assembly | Cross-reference: the relevant tax authority is determined by 'the provision of this Act' (gazette) against 'the Thirteenth Schedule to this Act' (National Assembly). |
| DR-190 | `ntaa2025:s10` | Gombe print | Structure only: the Gombe print starts Chapter Two, Part I (Returns) before s10; the cited NRS print and the National Assembly print after it. The text of s10 is the same. |
| DR-198 | `ntaa2025:s22(1)-(10)` | National Assembly | Cross-references: subsection (4), sections 105 and 107 (cited) against subsection (5), sections 100 and 102 (National Assembly). |
| DR-204 | `ntaa2025:s27` | National Assembly | Cross-reference: section 26 (cited) against section 23 (National Assembly) of the Nigeria Revenue Service (Establishment) Act. |
| DR-224 | `ntaa2025:s40` | National Assembly | Cross-reference: sections 42 and 43 (cited) against sections 40 and 41 (National Assembly). |
| DR-232 | `ntaa2025:s43` | National Assembly | Cross-references: sections 41, 65 and 67 (cited) against sections 40, 64 and 66 (National Assembly). |
| DR-234 | `ntaa2025:s46` | National Assembly | Cross-reference: section 191 of the Nigeria Tax Act (cited) against section 193 (National Assembly, which is gazette section 194). |

## C. Wording (187 rows)

Readings are cut to about 90 characters; the register holds them in full.

| ID | Unit | Against | Cited reading | Comparison reading |
|---|---|---|---|---|
| DR-002 | `nta2025:s4(6)(b)-(e)` | National Assembly | money markets including government securities, treasury Acts, treasury or savings certifi… | money markets including government securities, treasury bills, treasury or savings certif… |
| DR-004 | `nta2025:s6` | National Assembly | for the purposes of subsection (1). (3) Where the income tax paid | for the purposes of subsection (1) of this section. (3) Where the income tax paid |
| DR-005 | `nta2025:s6` | National Assembly | minimum effective tax rate. (4) The Service shall provide detailed rules for | minimum effective tax rate. (4) The Nigeria Revenue Service (the Service) shall provide d… |
| DR-006 | `nta2025:s6` | National Assembly | implementation of subsections (2) and (3 | implementation of subsections (2) and (3) of this section |
| DR-008 | `nta2025:s8` | National Assembly | 51 of Nigeria Tax Administration Act, 2025 | 50 of Nigeria Tax Administration Act |
| DR-009 | `nta2025:s10(1)-(6)` | National Assembly | company, and shall specify the – (a) day to be taken for the … the purposes of this secti… | company, and shall specify the — (a) the day to be taken for the … the purposes of this s… |
| DR-010 | `nta2025:s10(1)-(6)` | National Assembly | prescribed by Nigeria Tax Administration Act, 2025; and (d) net amount after the deductio… | prescribed by Nigeria Tax Administration Act; and (d) the net amount after the deduction … |
| DR-012 | `nta2025:s17(1)-(3)` | National Assembly | is borne by that permanent establishment; or (c) payment is made to that | is borne by that permanent establishment ; (c) payment is made to that |
| DR-014 | `nta2025:s17(4)-(5)` | National Assembly | 51 of Nigeria Tax Administration Act, 2025 from the payments made for any … in subsection… | 50 of Nigeria Tax Administration Act from the payments made for any … in subsection (3) (… |
| DR-015 | `nta2025:s17(4)-(5)` | National Assembly | reasonably be expected to have in accordance with section 191 of this Act | reasonably be expected to have in line with section 191 of this Act |
| DR-016 | `nta2025:s17(6)-(8)` | National Assembly | ascertained in accordance with subsection (5), the total profits shall be the | ascertained in accordance with subsection (5) of this section, the total profits shall be… |
| DR-017 | `nta2025:s17(6)-(8)` | National Assembly | provisions of subsections (6) and (7), the tax payable under this section … source under … | provisions of subsections (6) and (7) of this section, the tax payable under this section… |
| DR-018 | `nta2025:s17(9)(a)-(b)` | National Assembly | cloud computing, online teaching services, and profit can be attributable to such | cloud computing, online teaching services, and so on, and profit can be attributable to s… |
| DR-019 | `nta2025:s17(9)(c)-(e)` | National Assembly | or any supervisory activity in connection to it, (viii) facilities, including vessel, any… | or any supervisory activity in connection thereto, (viii) facilities, including vessel, a… |
| DR-020 | `nta2025:s18(1)-(3)` | National Assembly | 2) The provisions of subsection (1) shall not apply to passengers, mails | 2) The provisions of subsection (1) of this section shall not apply to passengers, mails |
| DR-021 | `nta2025:s18(1)-(3)` | National Assembly | For the purposes of subsection (1), where the Service is satisfied that | For the purposes of subsection (1) of this section, where the Service is satisfied that |
| DR-022 | `nta2025:s18(4)-(6)` | National Assembly | For the purposes of subsection (3), the total profits of a period | For the purposes of subsection (3) of this section, the total profits of a period |
| DR-023 | `nta2025:s18(4)-(6)` | National Assembly | provisions of subsections (3) and (4) cannot for any reason be satisfactorily | provisions of subsections (3) and (4) of this section cannot for any reason be satisfacto… |
| DR-024 | `nta2025:s18(7)-(9)` | National Assembly | obtaining any relevant approvals or permits, mandate all persons taxable under the provis… | obtain any relevant approvals or permits, present the following — (a) evidence of … trans… |
| DR-026 | `nta2025:s19` | National Assembly | under the Nigeria Tax Administration Act, 2025. (2) Nothing in this Act shall | under the Nigeria Tax Administration Act. (2) Nothing in this Act shall |
| DR-028 | `nta2025:s19` | National Assembly | of the Nigeria Tax Administration Act, 2025 | of the Nigeria Tax Administration Act |
| DR-029 | `nta2025:s20(1)(a)-(h)` | National Assembly | 18 of this Act or Part IX of Chapter Two apply, for the … h) bad or doubtful debts incurr… | 18 of this Act or Part XI of Chapter Two apply, for the … h) bad or doubtful debts incurr… |
| DR-030 | `nta2025:s20(1)(i)-(m)` | National Assembly | of transaction with a related party, or (ii) doubtful debts estimated in accordance … a d… | of transaction with a related party, (ii) doubtful debts estimated in accordance … a dedu… |
| DR-031 | `nta2025:s20(2)-(4)` | National Assembly | the provision of subsection (1)(h), in determining bad or doubtful debts | the provision of subsection (1)(h) of this section, in determining bad or doubtful debts |
| DR-034 | `nta2025:s22` | National Assembly | Notwithstanding the provisions of subsection (1), the assessable profits of the first | Notwithstanding the provisions of subsection (1) of this section, the assessable profits … |
| DR-035 | `nta2025:s23` | National Assembly | the accounting date under subsection (1), the taxable person shall notify the | the accounting date under subsection (1) of this section, the taxable person shall notify… |
| DR-036 | `nta2025:s24` | National Assembly | Where the provisions of subsection (2) apply, any additional assessment or claim … of the… | Where the provisions of subsection (2) of this section apply, any additional assessment o… |
| DR-037 | `nta2025:s26` | National Assembly | For the purpose of subsection (1), income from an employment shall be | For the purpose of subsection (1) of this section, income from an employment shall be |
| DR-038 | `nta2025:s27(1)-(4)` | National Assembly | ascertained in accordance with subsection (6), and capital allowance in accordance with | ascertained in accordance with subsection (6) of this section, and capital allowance in a… |
| DR-041 | `nta2025:s35` | National Assembly | of this Act. (2) Subsection (1) shall apply, notwithstanding that no asset | of this Act. (2) Subsection (1) of this section shall apply, notwithstanding that no asset |
| DR-042 | `nta2025:s42(1)-(4)` | National Assembly | 4) The provisions of subsection (1) shall not apply to any part | 4) The provisions of subsection (1) of this section shall not apply to any part |
| DR-043 | `nta2025:s46(a)-(h)` | National Assembly | situated; (e) subject to paragraph (d), registered shares or securities are situated … Ni… | situated ; (e) subject to paragraph (d) of this section, registered shares or securities … |
| DR-044 | `nta2025:s46(i)-(n)` | National Assembly | n) notwithstanding paragraphs (k) and (l), incorporeal property including digital assets … | n) notwithstanding paragraphs (k) and (l) of this section, incorporeal property including… |
| DR-045 | `nta2025:s48` | National Assembly | worth. (3) Subject to subsection (2), the surrender of a policy of | worth. (3) Subject to subsection (2) of this section, the surrender of a policy of |
| DR-046 | `nta2025:s50` | National Assembly | purposes of subsection (1) and (2), any person who pays compensation for | purposes of subsection (1) and (2) of this section, any person who pays compensation for |
| DR-047 | `nta2025:s50` | National Assembly | of Tax at Source Regulations issued pursuant to the Nigeria Tax Administration Act, 2025 … | of Tax at Source Regulations issued the Nigeria Tax Administration Act … issued the Niger… |
| DR-048 | `nta2025:s52` | National Assembly | over tangible movable property, subsection (1) shall apply in relation to the | over tangible movable property, subsection (1) of this section shall apply in relation to… |
| DR-049 | `nta2025:s56` | National Assembly | other company, at the rate of 30 per cent from the commencement of this Act | other company, at the rate of 30% from the commencement of this Act |
| DR-056 | `nta2025:s59` | National Assembly | the distribution in subsection (3)(f | the distribution in subsection (3)(f) of this section |
| DR-057 | `nta2025:s146` | National Assembly | services of agents, experts, engineers, architects and valuers, where the property is loc… | services of agents, experts, engineers, architects, valuers, etc., where the property is … |
| DR-058 | `nta2025:s147(3)` | National Assembly | provisions of subsections (1) or (2) – (a) where goods are supplied under | provisions of subsections (1) or (2) of this section – (a) where goods are supplied under |
| DR-060 | `nta2025:s153` | National Assembly | a) supplier's tax ID; (b) an invoice number; (c) name and address of the supplier … or bu… | a) supplier's tax ID ; (b) name and address of the supplier … or business registration nu… |
| DR-062 | `nta2025:s154` | National Assembly | a taxable person under subsection (1) shall be known as output VAT | a taxable person under subsection (1) of this section, shall be known as output VAT |
| DR-063 | `nta2025:s155` | National Assembly | Act, the Nigeria Tax Administration Act, 2025, or any regulation made pursuant thereto | Act, the Nigeria Tax Administration Act, or any regulation made pursuant thereto |
| DR-064 | `nta2025:s155` | National Assembly | VAT under subsections (1) and (2) shall be accompanied with a schedule | VAT under subsections (1) and (2) of this section, shall be accompanied with a schedule |
| DR-065 | `nta2025:s155` | National Assembly | Act, the Nigeria Tax Administration Act, 2025 or related tax laws shall apply | Act, the Nigeria Tax Administration Act or related tax laws shall apply |
| DR-066 | `nta2025:s156` | National Assembly | by the Nigeria Tax Administration Act, 2025, where the – (a) output VAT exceeds | by the Nigeria Tax Administration Act, where the — (a) output VAT exceeds |
| DR-070 | `nta2025:s158` | National Assembly | accordance with Nigeria Tax Administration Act, 2025. (2) The fiscalisation system may in… | accordance with Nigeria Tax Administration Act. (2) The fiscalisation system may include |
| DR-072 | `nta2025:s163(1)(d)-(l)` | National Assembly | h) pension funds and assets created pursuant to the Pension Reform Act; (i) pension | h) pension funds and assets created under the Pension Reform Act ; (i) pension |
| DR-073 | `nta2025:s163(1)(m)-(v)` | National Assembly | assets by an angel investor, venture capitalist, private equity fund, accelerators or inc… | assets by an angel investor, venture capitalists, private equity fund, accelerators or in… |
| DR-076 | `nta2025:s164` | National Assembly | any donation made by a company pursuant to subsection (1) may be deducted from the profits | any donation made by a company under subsection (1) of this section may be deducted from … |
| DR-077 | `nta2025:s165` | National Assembly | to a company under subsection (1) for any year of assessment shall | to a company under subsection (1) of this section for any year of assessment shall |
| DR-080 | `nta2025:s186` | National Assembly | by an order, published in the Official Gazette, specifies the date of collection | by an order, published in the Federal Government Gazette, specifies the date of collection |
| DR-084 | `nta2025:s189:def:baby-products` | National Assembly | use of babies from birth to three years of age and described below … a) baby activity and… | use of babies from birth to 3 years of age and described below … a) baby activity and ent… |
| DR-085 | `nta2025:s189:def:basic-food-items(a)-(k)` | National Assembly | oranges, mangoes, guavas, grape fruit, banana and pawpaw, whether fresh or dried; (i) liv… | oranges, mangoes, guavas, grape fruit, banana, pawpaw, etc., whether fresh or dried ; (i)… |
| DR-086 | `nta2025:s189:def:basic-food-items(l)-(p)` | National Assembly | beans, lentils, peas, chickpeas and tamarind, whether raw, roasted, fried, boiled, salted… | beans, lentils, peas, chickpeas and tamarind, etc., whether raw, roasted, fried, boiled, … |
| DR-087 | `nta2025:s189:def:equipment` | National Assembly | equipment" means tools, which may be devices, machines | Equipment" refers tools, which may be devices, machines |
| DR-088 | `nta2025:s189:def:locally-produced-agricultural-or-veterinary-medicines` | National Assembly | anti-sera, plasma, bacterins, hormones, toxoids, veterinary biosecurity for example, disi… | anti-sera, plasma, bacterins, hormones, toxoids, etc. ; veterinary biosecurity e.g. disin… |
| DR-090 | `nta2025:s189:def:machinery` | National Assembly | machinery" means a mechanical device or the parts | Machinery" refers to a mechanical device or the parts |
| DR-091 | `nta2025:s189:def:medical-equipment` | National Assembly | medical equipment" means devices requiring calibration, maintenance, repair, user | Medical Equipment" refers to devices requiring calibration, maintenance, repair, user |
| DR-092 | `nta2025:s189:def:medical-products` | National Assembly | medical products" means articles, instruments, apparatus, machine or software | Medical Products" refers to articles, instruments, apparatus, machine or software |
| DR-093 | `nta2025:s189:def:pharmaceutical-products` | National Assembly | pharmaceutical products" means components or finished products of both | Pharmaceutical Products" refer to components or finished products of both |
| DR-094 | `nta2025:s189:def:utilisation-of-gas-in-downstream-petroleum-operations` | National Assembly | of gas in downstream petroleum operations" means the marketing and distribution of natura… | of gas in downstream petroleum operations" refers to the marketing and distribution of na… |
| DR-096 | `nta2025:s190(1)(a)` | National Assembly | useful life of the asset transferred as a result of the merger, (v) unutilised capital | useful life of the asset transferred because of the merger, (v) unutilised capital |
| DR-097 | `nta2025:s190(1)(b)-(c)` | National Assembly | shall be recognised at the value at which they are sold or transferred … new or surviving… | shall be recognised at the value of which they are sold or transferred … new or surviving… |
| DR-099 | `nta2025:s190(2)-(7)` | National Assembly | to the new company takes place; or (b) such date within the calendar … of the Service, an… | to the new company takes place ; (b) such date within the calendar … of the Service, and … |
| DR-100 | `nta2025:s190(2)-(7)` | National Assembly | period referred to in subsection (2), shall constitute "Accounting Period" under Chapter | period referred to in subsection (2) of this Section, shall constitute "Accounting Period… |
| DR-101 | `nta2025:s190(2)-(7)` | National Assembly | of the Nigeria Tax Administration Act, 2025 , or registerable as a result of | of the Nigeria Tax Administration Act or registerable as a result of |
| DR-102 | `nta2025:s191` | National Assembly | of the Nigeria Tax Administration Act, 2025 , shall apply to a direction made | of the Nigeria Tax Administration Act shall apply to a direction made |
| DR-103 | `nta2025:s196` | National Assembly | Capital Gains Tax Act, Cap. C1, Laws of the Federation of Nigeria, 2004; (b) Casino Act, … | Capital Gains Tax Act, Cap. C1, LFN, 2004 ; (b) Casino Act, Cap. C3 … 2004 ; (b) Casino A… |
| DR-105 | `nta2025:s197(1)-(9)` | National Assembly | and 12, of the Seventh Schedule; and (d) paragraph 14 (6) of Part IV of the Seventh | and 12, of the Seventh Schedule ; (d) subparagraph 6 of paragraph 14 of Part IV of the Se… |
| DR-106 | `nta2025:s197(1)-(9)` | National Assembly | Export Processing Zones Act, Cap. ₦107, Laws of the Federation of Nigeria, 2004 is amende… | Export Processing Zones Act, Cap. ₦107, LFN, 2004 is amended by deleting sections |
| DR-107 | `nta2025:s197(1)-(9)` | National Assembly | Free Trade Zone Act, Cap. O5, Laws of the Federation of Nigeria, 2004 is amended by delet… | Free Trade Zone Act, Cap. O5, LFN, 2004 is amended by deleting sections |
| DR-108 | `nta2025:s197(1)-(9)` | National Assembly | Engineering Infrastructure (Establishment) Act, Cap. ₦3 Laws of the Federation of Nigeria… | Engineering Infrastructure (Establishment) Act, Cap. ₦3 LFN, 2004 is amended by deleting … |
| DR-109 | `nta2025:s197(1)-(9)` | National Assembly | Tariffs, Etc. (Consolidation) Act Cap. C49 Laws of the Federation of Nigeria, 2004 is ame… | Tariffs, Etc. (Consolidation) Act Cap. C49 LFN, 2004 is amended by deleting section |
| DR-110 | `nta2025:s197(10)-(14)` | National Assembly | and Miscellaneous Provisions) Act, Cap. E19, Laws of the Federation of Nigeria, 2004 is a… | and Miscellaneous Provisions) Act, Cap. E19, LFN, 2004 is amended by deleting section |
| DR-111 | `nta2025:s197(10)-(14)` | National Assembly | Agency (Establishment, Etc.) Act Cap. F38, Laws of the Federation of Nigerian 2004 is ame… | Agency (Establishment, Etc.) Act Cap. F38, No. 7, 2002 is amended by deleting section 14 |
| DR-113 | `nta2025:s197(10)-(14)` | National Assembly | 17 2015 is amended by deleting section 44 (2)(a) and (4), and the … section 44 (2)(a) and… | 17, 2015 is amended by deleting subsections (2)(a) and (4) of section … deleting subsecti… |
| DR-117 | `nta2025:s200` | National Assembly | The discharge of the duties and obligations, as … of the Nigeria Tax Administration Act, … | The performance of the duties and obligations, as … of the Nigeria Tax Administration Act |
| DR-118 | `nta2025:s201` | National Assembly | the same taxpayer or tax base; and (c) the Minister may issue regulations | the same taxpayer or tax base ; (c) the Minister may issue regulations |
| DR-119 | `nta2025:s201` | National Assembly | regulations or guidelines published in the Official Gazette for the effective implementat… | regulations or guidelines published in the Federal Government Gazette for the effective i… |
| DR-120 | `nta2025:s201` | National Assembly | levy, royalty, surcharge on fossil fuel, discharge such duty or obligation in accordance | levy, royalty, surcharge on fossil fuel, carry out such duty or obligation in accordance |
| DR-121 | `nta2025:s202:def:authorised-officer` | National Assembly | authority to perform any function under this Act | authority to perform any function under or in pursuant to this Act |
| DR-123 | `nta2025:s202:def:borrower` | National Assembly | borrower" means an approved borrower in a regulated securities lending … in a regulated s… | borrower " in a Regulated Securities Lending … in a Regulated Securities Lending Transact… |
| DR-126 | `nta2025:s202:def:digital-assets` | National Assembly | that can be digitally exchanged, including crypto assets, utility tokens, security tokens | that can be digitally exchanged, including, but not limited to, crypto assets, utility to… |
| DR-127 | `nta2025:s202:def:family-income` | National Assembly | family income" refers to any income accruing to a family | family income" means any income accruing to a family |
| DR-128 | `nta2025:s202:def:lender` | National Assembly | lender'' means an approved lender in a regulated securities lending … in a regulated secu… | lender'' in a Regulated Securities Lending … in a Regulated Securities Lending Transactio… |
| DR-129 | `nta2025:s202:def:minister` | National Assembly | Minister" means the Minister responsible for finance | Minister" means the Minister in charge of finance |
| DR-130 | `nta2025:s202:def:mortgage` | National Assembly | a mortgage, or any other security of any land, estate, or property | a mortgage, or any other security as aforesaid of any land, estate, or property |
| DR-132 | `nta2025:s202:def:nigeria` | National Assembly | international law has been or may be designated, under the law of | international law has been or may hereafter be designated, under the law of |
| DR-133 | `nta2025:s202:def:official-market-rate` | National Assembly | official market rate" means currency exchanged market approved by the Central Bank of | official market rate" means currency exchange market rate approved by the Central Bank of |
| DR-135 | `nta2025:s202:def:policy-of-insurance` | National Assembly | or conditions of an insurance policy, and the expression "insurance" includes assurance | or conditions of an insurance policy |
| DR-136 | `nta2025:s202:def:policy-of-marine-insurance` | National Assembly | or fittings belonging to the ship while under construction or repair or on | or fittings belonging to the ship whilst under construction or repair or on |
| DR-139 | `nta2025:s202:def:public-fund` | National Assembly | public fund" means any fund set up by any | public fund" any fund set up by any |
| DR-140 | `nta2025:s202:def:real-estate-investment-company` | National Assembly | real estate investment company'' means a company duly approved by the | real estate investment'' a company duly approved by the |
| DR-142 | `nta2025:s202:def:related-parties` | National Assembly | the same meaning as connected persons, and unconnected, non-related or unrelated party sh… | the same meaning as connected persons |
| DR-146 | `nta2025:s202:def:resident` | National Assembly | means resident person, individual or company | means resident person, individual or company, the context requires |
| DR-148 | `nta2025:s202:def:service` | National Assembly | Service'' means the Nigeria Revenue Service established under Nigeria Revenue … the Niger… | service" the Nigerian Revenue Service established under the Nigeria … the Nigerian Revenu… |
| DR-155 | `nta2025:s202:def:tax` | National Assembly | tax" means any imposition, duty, levy, royalty or | tax" any imposition, duty, levy, royalty or |
| DR-156 | `nta2025:s202:def:tax-id` | National Assembly | for under Nigeria Tax Administration Act, 2025 | for under Nigeria Tax Administration Act |
| DR-158 | `nta2025:s202:def:taxable-person` | National Assembly | way of trade or business, or any person or agency of government acting | way of trade or business, or a person or agency of government acting |
| DR-159 | `nta2025:s202:def:trade-or-business` | National Assembly | trade or business" means any activity or venture from which | trade or business" any activity or venture from which |
| DR-160 | `nta2025:s202:def:transaction-at-arm-s-length` | National Assembly | transaction at arm's length" means a transaction on normal open market | transaction at arm's length" a transaction on normal open market |
| DR-162 | `nta2025:s202:def:vessel` | National Assembly | water, of human beings or goods ; and | water, of human beings or goods |
| DR-163 | `nta2025:s202:def:year-of-assessment` | National Assembly | year of assessment" means government tax year being 1 January | year of assessment" government tax year being 1 January |
| DR-165 | `nta2025:sch1:pt1:para1:def:mining-operation` | National Assembly | underground mines, subsea mines, placer mines and solution mines | underground mines, subsea mines, placer mines, solution mines ; "qualifying capital expen… |
| DR-166 | `nta2025:sch1:pt1:para1:def:qualifying-capital-expenditure(a)-(d)` | National Assembly | ii) machinery, (iii) manufacturing Industrial plant, and (iv) construction plant, includi… | ii) machinery, (iii) manufacturing Industrial plant, (iv) construction plant, including p… |
| DR-167 | `nta2025:sch1:pt1:para1:def:qualifying-capital-expenditure(e)-(i)` | National Assembly | ii) in respect of ranching on – (1) structures used primarily for ranching, (2 … 1) struc… | ii) in respect of ranching on : (A) structures used primarily for ranching ; (B … A) stru… |
| DR-168 | `nta2025:sch1:pt1:para1:def:qualifying-capital-expenditure(e)-(i)` | National Assembly | the life expectancy is more than five years; (f) capital expenditure (qualifying intangib… | the life expectancy is more than 5 years ; (f) capital expenditure (qualifying intangible… |
| DR-169 | `nta2025:sch1:pt1:para6-8` | National Assembly | Capital allowance 6. Where in a basis period for a | a wasting nature. Capital allowance 6. In a basis period for a |
| DR-170 | `nta2025:sch1:pt1:para9` | National Assembly | Disposal under qualifying expenditure 9. In this Part – (a) a building, structure | assessment. Disposal under qualifying expenditure 9. Under this part — (a) a building, st… |
| DR-171 | `nta2025:sch1:pt1:para10` | National Assembly | of the sale. (3) Subparagraph (2) shall not apply where the compensation | of the sale. (3) Subparagraph (2) of this paragraph shall not apply where the compensation |
| DR-172 | `nta2025:sch1:pt1:para11-12` | National Assembly | For the purposes of subparagraph (1), assets purchased or disposed in one | For the purposes of subparagraph (1) of this paragraph, assets purchased or disposed in o… |
| DR-173 | `nta2025:sch1:pt1:para13-14` | National Assembly | for the purpose of subparagraph (2). Exclusion of certain expenditure 14. Under | for the purpose of subparagraph (2) of this paragraph. Exclusion of certain expenditure 1… |
| DR-174 | `nta2025:sch1:pt1:para18-20` | National Assembly | of the application of subparagraph (2), all such additional assessments or repayments | of the application of subparagraph (2) of this paragraph, all such additional assessments… |
| DR-175 | `nta2025:sch1:pt1:para21-22` | National Assembly | be carried forward to subsequent period. Manner of making allowance 22. (1 | be carried forward to subsequent period(s). Manner of making allowance 22.—(1 |
| DR-177 | `nta2025:sch11` | National Assembly | UNDER THIS ACT Items on which VAT may be suspended or delayed 1 … the Minister in the Off… | OF THIS ACT Items on which Value Added Tax may be suspended or delayed 1 … the Minister i… |
| DR-180 | `ntaa2025:s3(2)-(4)` | National Assembly | or the Federal Capital Territory, shall pursuant to the First Schedule to this | or the Federal Capital Territory, shall under to the First Schedule to this |
| DR-181 | `ntaa2025:s3(2)-(4)` | National Assembly | to subsection (1) (a) (ii) - (iv) ; and (b) exercising such other powers and | to subsection (1) (a) (ii) - (iv) of this section. (b) exercising such other powers and |
| DR-182 | `ntaa2025:s3(2)-(4)` | National Assembly | purpose of subsections (1) and (2), the relevant tax authority may do … this Act, the Nig… | purpose of subsections (1) and (2) of this section, the relevant tax authority may do … t… |
| DR-183 | `ntaa2025:s4` | National Assembly | Taxpayer Identification ("Tax ID") for the purpose of compliance with tax obligations | Taxpayer Identification ("Tax ID") for the purposes of compliance with tax obligations |
| DR-184 | `ntaa2025:s7` | National Assembly | ID upon request under subsection (1), the relevant tax authority shall, within | ID upon request under subsection (1) of this section, the relevant tax authority shall, w… |
| DR-185 | `ntaa2025:s7` | National Assembly | Tax ID under subsection (3) of the registration and Tax ID. (5 | Tax ID under subsection (3) of this section of the registration and Tax ID. (5 |
| DR-187 | `ntaa2025:s9` | National Assembly | change referred to in subsection (1) includes— (a) name, including trading name … e-mail … | change referred to in subsection (1) of this section includes — (a) name, including tradi… |
| DR-188 | `ntaa2025:s9` | National Assembly | case of— (i) an incorporated person, a person holding 5% or more of its | case of — (i) an incorporated person, persons holding 5% or more of its |
| DR-189 | `ntaa2025:s9` | National Assembly | contact details of all the partners, and (iv) sale, liquidation, acquisition, take-over | contact details of all the partners, or (iv) sale, liquidation, acquisition, take-over |
| DR-191 | `ntaa2025:s10` | National Assembly | ID where it is satisfied that the— (a) taxable person is deceased or, in … wound up or di… | ID where it is satisfied that (a) the taxable person is deceased, or, in … wound up or di… |
| DR-192 | `ntaa2025:s11(1)-(2)` | National Assembly | pay tax under Nigeria Tax Act, 2025 or any other tax law, for | pay tax under Nigeria Tax Act or any other tax law, for |
| DR-193 | `ntaa2025:s11(1)-(2)` | National Assembly | Two of the Nigeria Tax Act, 2025, such company shall submit a return | Two of the Nigeria Tax Act, such company shall submit a return |
| DR-194 | `ntaa2025:s11(3)-(6)` | National Assembly | final tax under Nigeria Tax Act, 2025. (4) Where a company permanently ceases | final tax under Nigeria Tax Act. (4) Where a company permanently ceases |
| DR-195 | `ntaa2025:s11(3)-(6)` | National Assembly | a person designated by a company pursuant to paragraph (a) of this subsection is a paid a… | a person designated by a company under paragraph (a) is a paid agent, such person |
| DR-196 | `ntaa2025:s22(1)-(10)` | National Assembly | in respect of value added tax, with or without a notice, and | in respect of Value Added Tax (VAT), with or without a notice and |
| DR-197 | `ntaa2025:s22(1)-(10)` | National Assembly | 4) The provision of subsection (1) shall not apply to a small | 4) The provision of subsection (1) of this section shall not apply to a small |
| DR-199 | `ntaa2025:s22(1)-(10)` | National Assembly | 152 of the Nigeria Tax Act, 2025. (9) The provisions of subsection (5 | 151 of the Nigeria Tax Act. (9) The provisions of subsection (5 |
| DR-200 | `ntaa2025:s22(1)-(10)` | National Assembly | 9) The provisions of subsection (5) shall not apply to companies engaged … defined in the… | 9) The provisions of subsection (5) of this section shall not apply to companies engaged … |
| DR-201 | `ntaa2025:s25` | National Assembly | email address of any counterparty involved in the transaction ; and (h) such other | email address of any counterparty involved to the transaction ; and (h) such other |
| DR-202 | `ntaa2025:s25` | National Assembly | the relevant tax authority. (3) Notwithstanding subsections (1) and (2), the relevant tax… | the relevant tax authority. (3) Notwithstanding subsection (1) and (2), the relevant tax … |
| DR-203 | `ntaa2025:s27` | National Assembly | section 60 of Nigeria Tax Act, 2025, shall, in addition to annual tax | section 60 of Nigeria Tax Act, shall, in addition to annual tax |
| DR-205 | `ntaa2025:s27` | National Assembly | received under subsections (1) and (2). (4) The Minister may issue a | received under subsections (1) and (2) of this section. (4) The Minister may issue a |
| DR-206 | `ntaa2025:s29` | Gombe print | transactions where the cumulative transactions in amount to ₦100,000,000 or | transactions where the cumulative transactions in a month amount to ₦100,000,000 or |
| DR-210 | `ntaa2025:s30` | National Assembly | a person in relation to a disclosable transaction or agreement ; (b) the form … informati… | a person in relation to a discloseable transaction or agreement ; (b) the form … informat… |
| DR-211 | `ntaa2025:s30` | National Assembly | to deduct or account for tax ; and "transaction" includes course of action, agreement | to deduct or account for tax ; "transaction" includes course of action, agreement |
| DR-212 | `ntaa2025:s31` | National Assembly | to be maintained under subsection (1) shall be in English language and | to be maintained under subsection (1) of this section shall be in English language and |
| DR-213 | `ntaa2025:s34` | National Assembly | a tax return, under subsection (1), the relevant tax authority may – (a | a tax return, under subsection (1) of this section, the relevant tax authority may : (a |
| DR-214 | `ntaa2025:s35` | National Assembly | The assessment issued under subsection (1) shall not affect the imposition of | The assessment issued under subsection (1) of this section shall not affect the impositio… |
| DR-215 | `ntaa2025:s36` | National Assembly | authority may, within six years of the assessment, assess the taxable person at | authority may, within six years of an assessment, assess the taxable person at |
| DR-216 | `ntaa2025:s36` | National Assembly | limitation period stipulated in subsection (1) shall not preclude the relevant tax | limitation period stipulated in subsection (1) of this section shall not preclude the rel… |
| DR-217 | `ntaa2025:s36` | National Assembly | time limit specified in subsection (1), where there is a deliberate misstatement … impose… | time limit specified in subsection (1) of this section, where there is a deliberate misst… |
| DR-218 | `ntaa2025:s36` | National Assembly | have been charged under subsection (1), even where such facts were not | have been charged under subsection (1) of this section, even where such facts were not |
| DR-219 | `ntaa2025:s38` | National Assembly | For the purpose of subsection (1) "profit margin" shall be the proportion | For the purpose of subsection (1) of this section "profit margin" shall be the proportion |
| DR-225 | `ntaa2025:s41(1)-(4)` | National Assembly | and amendment of the assessment made on it. (2) An application under subsection | and amendment of the assessment made upon it. (2) An application under subsection |
| DR-226 | `ntaa2025:s41(1)-(4)` | National Assembly | 2) An application under subsection (1) shall only be valid if it | 2) An application under subsection (1) of this section shall only be valid if it |
| DR-227 | `ntaa2025:s41(1)-(4)` | National Assembly | Notwithstanding the provisions of subsection (2), the relevant tax authority may, for … a… | Notwithstanding the provisions of subsection (2) of this section, the relevant tax author… |
| DR-228 | `ntaa2025:s41(1)-(4)` | National Assembly | objection referred to in subsection (1), the relevant tax authority may – (a | objection referred to in subsection (1) of this section, the relevant tax authority may —… |
| DR-230 | `ntaa2025:s41(5)-(8)` | National Assembly | the tax payable shall be served on the taxable person. (6) The relevant | the tax payable shall be served upon the taxable person. (6) The relevant |
| DR-233 | `ntaa2025:s43` | National Assembly | 3) The provision of subsection (2) shall be subject only to the | 3) The provision of subsection (2) of this section shall be subject only to the |
| DR-235 | `ntaa2025:s47` | National Assembly | section 30 (3) of this Act; and "arrangement" includes any agreement, understanding, sche… | section 30 (3) of this Act; "arrangement" includes any agreement, understanding, scheme |
| DR-236 | `ntaa2025:s51(8)-(9)` | National Assembly | 8) Notwithstanding subsections (1) to (6), a non-resident company that provides | 8) Notwithstanding subsections (1) to (6) of this section, a non-resident company that pr… |
| DR-237 | `ntaa2025:s55` | National Assembly | the refund mentioned in subsection (1). (3) Any tax refund due shall | the refund mentioned in subsection (1) of this section. (3) Any tax refund due shall |
| DR-238 | `ntaa2025:s55` | National Assembly | of the relevant tax authority made pursuant to subsection (2), with the option of a set | of the relevant tax authority made under subsection (2) of this section, with the option … |
| DR-239 | `ntaa2025:s55` | National Assembly | For the purpose of subsection (4), the relevant tax authority shall provide | For the purpose of subsection (4) of this section, the relevant tax authority shall provi… |
| DR-240 | `ntaa2025:s55` | National Assembly | refunds. (6) The dedicated accounts created pursuant to subsection (4), shall, be adminis… | refunds. (6) The dedicated accounts created under subsection (4) of this section, shall, … |
| DR-241 | `ntaa2025:s56` | National Assembly | request referred to in subsection (1) shall be made not later than | request referred to in subsection (1) of this section shall be made not later than |
| DR-242 | `ntaa2025:s65` | National Assembly | Act— (a) a sum equal to 10% of the amount of the tax … amount of the tax payable shall be… | Act — (a) a sum equal to ten percent of the amount of the tax … amount of the tax payable… |
| DR-244 | `ntaa2025:s65` | National Assembly | authority shall serve a demand notice on the company or person in whose | authority shall serve a demand notice upon the company or person in whose |
| DR-246 | `ntaa2025:s72` | National Assembly | the transactions mentioned in subsection (4), shall demand from such person a | the transactions mentioned in subsection (4) of this section, shall demand from such pers… |
| DR-247 | `ntaa2025:s72` | National Assembly | 4) The provision of subsection (2) shall apply in relation to— (a | 4) The provision of subsection (2) of this section shall apply in relation to — (a |
| DR-248 | `ntaa2025:s101` | National Assembly | administrative penalty of— (a) ₦100,000 in the first month in which … failure occurs ; an… | administrative penalty of — (a) ₦100,000.00 in the first month in which … failure occurs … |
| DR-249 | `ntaa2025:s102` | National Assembly | other than a company, ₦10,000, and (ii) a company, ₦50,000 | other than a company, ₦10,000.00, and (ii) in the case of a company, ₦50,000.00 |
| DR-250 | `ntaa2025:s102` | National Assembly | and (ii) a company, ₦50,000 | case of a company, ₦50,000.00 |
| DR-251 | `ntaa2025:s103` | National Assembly | administrative penalty of ₦1,000,000 for the first day of default … day of default and ₦1… | administrative penalty of ₦1,000,000.00 for the first day of default … day of default and… |
| DR-252 | `ntaa2025:s108` | National Assembly | an administrative penalty of ₦100,000 in the first day of default | an administrative penalty of ₦100,000.00 in the first day of default |
| DR-253 | `ntaa2025:s108` | National Assembly | default, in addition to ₦10,000 for each subsequent day in which | default, in addition to ₦10,000.00 for each subsequent day in which |
| DR-255 | `ntaa2025:s124` | National Assembly | administrative penalty of ₦1,000,000 in addition to the payment of … respect of any repay… | administrative penalty of ₦1,000,000.00 in addition to the payment of … respect of any re… |
| DR-256 | `ntaa2025:s127` | National Assembly | Any person who contravenes any provisions or commits an offence against this Act for whic… | Any person who contravenes the provisions of this Act for which no specific … Act for whi… |
| DR-257 | `ntaa2025:s147:def:advance-ruling` | National Assembly | this Act, the Nigeria Tax Act, 2025, or any other tax law enacted | this Act, the Nigeria Tax Act or any other tax law enacted |
| DR-258 | `ntaa2025:s147:def:assessable-income` | National Assembly | meaning under the Nigeria Tax Act, 2025 | meaning under the Nigeria Tax Act |
| DR-259 | `ntaa2025:s147:def:assessable-profit` | National Assembly | meaning under the Nigeria Tax Act, 2025 | meaning under the Nigeria Tax Act |
| DR-260 | `ntaa2025:s147:def:authorised-officer` | National Assembly | authorised officer" means any person employed in | Authority Officer" means any person employed in |
| DR-263 | `ntaa2025:s147:def:dispute` | National Assembly | or law or both, which arises pursuant to the issuance of an assessment, action | or law or both, which arises under the issuance of an assessment, action |
| DR-264 | `ntaa2025:s147:def:distrain` | National Assembly | Act and the Nigeria Tax Act, 2025 | Act and the Nigeria Tax Act |
| DR-266 | `ntaa2025:s147:def:service` | National Assembly | the Nigeria Revenue Service (Establishment) Act, 2025 | the Nigeria Revenue Service (Establishment) Act |
| DR-268 | `ntaa2025:s147:def:small-business` | Gombe print | 000 or less per annum with a total fixed assets less than ₦250 | 000 or less per annum with total fixed assets less than ₦250 |
| DR-269 | `ntaa2025:s147:def:small-business` | National Assembly | gross turnover of ₦100,000,000 or less per annum with a … assets less than ₦250,000,000, … | gross turnover of ₦100,000,000.00 or less per annum with total … not more than ₦250,000,0… |
| DR-271 | `ntaa2025:s147:def:stamp-duty` | National Assembly | this Act, the Nigeria Tax Act, 2025, or any other laws | this Act, the Nigeria Tax Act or any other laws |
| DR-272 | `ntaa2025:s147:def:tax` | National Assembly | this Act, the Nigeria Tax Act, 2025, or any other law | this Act, the Nigeria Tax Act or any other law |
| DR-273 | `ntaa2025:s147:def:taxable-goods-and-services` | National Assembly | defined in Part IV of Chapter Seven of Nigeria Tax Act, 2025 … Chapter Seven of Nigeria T… | defined in Part IV of Chapter 7 of Nigeria Tax Act … Chapter 7 of Nigeria Tax Act |
