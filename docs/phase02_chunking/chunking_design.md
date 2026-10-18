# Parsing and Chunking Design (Phase 2a)

Design for turning the cited texts (D-09) into citable chunks. This is a design document: no
parser or chunker code is in the repository yet. Every claim below rests on a probe run on
2026-10-18 against the pinned files in [data/manifest.csv](../../data/manifest.csv). The probe
scripts were throwaway and were not committed; their outputs are quoted here.

**Status: approved by Kene on 2026-10-18 (D-12). The six decisions are listed in section 12.**

| Cited text (D-09) | File | Kind |
|---|---|---|
| Nigeria Tax Act 2025 (NTA) | `nigeria_tax_act_2025_gazette_lirs.pdf` | Text PDF, 215 pages |
| Nigeria Tax Administration Act 2025 (NTAA) | `nigeria_tax_administration_act_2025_gazette_nrs.pdf` | Scanned PDF, 89 pages, poor OCR layer (DQ-04) |
| Comparison: NTA, National Assembly | `nigeria_tax_act_2025_nass.pdf` | Text PDF |
| Comparison: NTAA, Gombe IRS print | `nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf` | Text PDF, earlier print (DQ-02) |
| Comparison: NTAA, National Assembly | `nigeria_tax_administration_act_2025_nass.pdf` | Text PDF |

## 1. Text extraction

### Recommendation: pdfplumber, splitting each page by character geometry

Each gazette page has three regions:

1. A running header line ("A 426 2025 No. 7 Nigeria Tax Act, 2025").
2. A body column in 11 pt type.
3. A marginal-note column in 9 pt type, holding section titles such as "Rates of tax for
   companies". It sits on the left of even pages and the right of odd pages.

Line-based extractors merge the margin into the body, which breaks section detection. pypdf
gives `Rates of tax 56. Tax shall be levied…`.

pdfplumber exposes every character with its position and font size, so a page is split as
follows:

- **Header:** characters within 6 pt of the top-most text line.
- **Margin:** characters smaller than 10 pt that lie wholly outside the body column. The body
  column is the x-range of the 11 pt characters on that page.
- **Body:** everything else. This keeps small-caps headings ("PART IX – RATES OF TAX"), whose
  small capitals are under 10 pt but sit inside the column.

**Probe 1: section detection over the whole cited NTA.** A section starts on a line matching
`^\s*(\d{1,3})\.\s?(?:[—–-]\s?)?(\(1\)|[A-Z“"(])`, accepted only when the number is the next
one expected.

| Extractor | NTA sections found in order (expected 203) | NTAA Gombe (expected 148) |
|---|---|---|
| pypdf, plain text | 23 (first break at section 21: the margin text runs into the line) | 148 |
| **pdfplumber, geometry split** | **203, no anomalies, last on page 127** | **148, no anomalies** |

**Probe 2: three in-scope sections, extracted against expected.** "Expected" was read from
rendered page images of the cited file.

| Section | Extracted body (pdfplumber split) | Expected (page image) | Result |
|---|---|---|---|
| s56 (page 42) | `56. Tax shall be levied, for each year of assessment in respect of total` / `profits of every company, in the case of –` / `(a) a small company, at 0%; and` / `(b) any other company, at the rate of 30 per cent from the commence-` / `ment of this Act.` | Same, with "Rates of tax for companies" in the margin | Match. Margin captured separately: `Rates of tax` / `for compa-` / `nies` |
| s148 (page 88) | `148. Subject to the provisions of Part IV of Chapter Eight of this Act,` / `VAT shall be charged on the value of all taxable supplies at the rate of 7.5%.` | Same; margin "Rate of VAT" | Match |
| s202 "small company" (page 126) | `"small company" means a company that earns gross turnover of` / `N 50,000,000 or less per annum with total fixed assets not exceeding` / `N 250,000,000, provided that any business providing professional services shall` / `not be classified as a small company;` | `₦50,000,000` … `₦ 250,000,000` | Match except the naira sign. The PDF draws ₦ as "N" with a struck line, so the extracted text has `N 50,000,000` |

### Normalisation rules (applied after extraction, recorded per chunk)

| Rule | Example |
|---|---|
| `N` followed by optional space and a digit becomes `₦` | `N 50,000,000` → `₦50,000,000` |
| Rejoin end-of-line hyphenation when the next line starts lower-case | `commence-` / `ment` → `commencement` |
| Unicode NFKC; U+FFFD (unmapped glyph in some CID fonts) becomes `–` | `PART IX � RATES` → `PART IX – RATES` |
| Collapse runs of spaces inside a line, keep line breaks for paragraph structure | |
| Never change words, numbers or punctuation otherwise | |

### Rejected options

- **pypdf:** no character geometry (see probe 1).
- **PyMuPDF:** accurate, but AGPL-licensed. That conflicts with the project's MIT licence
  (D-07).
- **Camelot:** fails on these tables, which have no ruling lines (section 4).

### New dependencies (Phase 2b)

| Package | Version probed | Licence | Needed for |
|---|---|---|---|
| `pdfplumber` (brings `pdfminer.six` 20231228) | 0.11.4 | MIT | All text extraction |
| `pypdfium2` | 4.30.0 | Apache-2.0 / BSD-3 | Rendering scan pages for OCR and spot-check images |
| `rapidocr-onnxruntime` (brings `onnxruntime` 1.31.0, `opencv-python` 4.11.0.86, `numpy` 1.26.4) | 1.3.24 | Apache-2.0 | One-off NTAA re-OCR only |
| `tiktoken` | 0.8.0 | MIT | Token counts (`cl100k_base`, the tokenizer of `text-embedding-3-small`) |

The OCR stack is about 100 MB and runs once. It goes in a separate `requirements-ocr.txt`, so
the deployed image (Phase 11) doesn't carry it. No operating-system install (such as
Tesseract) is needed.

## 2. NTAA scan strategy (DQ-04)

The cited NTAA text is the NRS print, a scan. Its embedded images are about 2116 × 2916 px. Three
readings of the same section were compared:

- **G**: the Gombe print (text PDF, earlier gazette print).
- **L**: the NRS file's existing OCR layer.
- **O**: a fresh re-OCR of the NRS page images with RapidOCR. Settings: rendered at native
  resolution (scale 14.4), detector limit set to the full image size, `det_unclip_ratio=1.3`.
  Without the full-size detector limit, whole lines were missed.

**Probe 3: page-level similarity to the Gombe text.** Character similarity, whitespace ignored,
difflib ratio.

| NRS page / Gombe page | Existing OCR layer (L) | Re-OCR (O) |
|---|---|---|
| 10 / 12 | 0.612 | 0.944 |
| 19 / 21 | 0.797 | 0.942 |
| 26 / 28 | 0.573 | 0.908 |
| 40 / 42 | 0.677 | 0.930 |

The existing layer is unusable. Its reading order is scrambled, for example
`capiata) l,a onr in thc eo brpeonreafticeida pl eor`, so not one of the four section starts
could be located in it. Page-level scores are lower than section scores because the two prints
break pages at different places.

**Probe 4: section-level comparison, Gombe (G) against re-OCR (O).** Running headers and
chapter/part headings removed.

| Section | Char similarity (whitespace ignored) | Non-space differences | Word differences (all) | Of which spacing only |
|---|---|---|---|---|
| s10 Suspension and deregistration | 0.972 | 3 dropped `.`/`,`; one heading line left over by the probe | 15 / 205 | 4 |
| s22 Returns for VAT | 0.999 | 2 dropped `.`, one stray `-` | 56 / 339 | 18 |
| s41 Objection and appeal | 0.939 | `(1)` read as `(l)` twice; **the whole Gombe subsection (8), the 20% deposit clause, is absent from NRS** | 160 / 499 | 22 |
| s65 Penalty and interest | 0.9997 | 1 dropped `,` | 72 / 333 | 18 |

Quoted from section 41. Gombe print:

> (8) Where a taxpayer is dissatisfied with the judgement of the Tax Appeal Tribunal, it may
> appeal to the High court, provided that it shall pay 20% of the disputed amount into an
> account designated by the High Court as security before the hearing of the appeal, …
> (9) A party dissatisfied with the decision of the High Court may appeal …

NRS re-OCR, which matches the page image:

> (7) … the taxpayer may exercise the right of appeal.
> (8) A party dissatisfied with the decision of the High Court may appeal to the Court of
> Appeal, …

Re-OCR reads the words correctly, but it drops many inter-word spaces (`ofits`, `orwhere`) and
some end-of-line punctuation. Those defects are harmless for alignment and harmful for citation
text.

### Recommendation: (b) alignment, with a fresh re-OCR as the NRS reading

1. **Re-OCR all 89 NRS pages once**, using the settings above (about 20 seconds a page, roughly
   30 minutes in total). Commit the per-page OCR text with the engine versions and settings
   (section 7), so later builds never re-run OCR and stay deterministic.
2. Parse the re-OCR text and the Gombe text into sections (148 each).
3. For each in-scope section, compare the two after normalisation: lower-case, NFKC, and only
   letters, digits and `%` kept, with OCR confusions in subsection labels mapped (`(l)` and
   `(i)` to `(1)` where followed by a capital).
   - **Identical:** use the Gombe text verbatim (clean spacing and punctuation), with
     `text_source = "gombe_verified_identical"`. The citation still points to the NRS file and
     NRS page numbers.
   - **Different:** use the NRS reading, hand-proofread against the page image and saved as a
     curated file (`data/curated/ntaa_nrs_proofread/s041.txt`), with
     `text_source = "nrs_proofread"`. The difference also gets a discrepancy-register entry
     (Gombe print against NRS print). Section 41 is one known case.
4. Option (a) on its own, re-OCR text used directly, is rejected. Missing spaces would degrade
   both embeddings and the citation text shown to users, and proofreading all 65 in-scope
   sections by hand is avoidable.

The rule is strict equality, not a similarity threshold. On the probe, 0.999 still hid a real
dropped word, so "near-identical" isn't safe for legal text.

## 3. Structure detection

The parser is a line-level state machine over the body text in page order. Its states are
`front_matter → body → schedules`.

| Element | Detection | Evidence |
|---|---|---|
| Front matter (cover, arrangement of sections) | Skipped until the first line matching `^1\.\s?The objective` | Arrangement pages repeat every section title and would otherwise create false starts |
| Chapter | Upper-case line `^CHAPTER\s+(ONE…NINE|\d+)` (case-sensitive). The heading text continues on the same or next line | Gazette uses both `CHAPTER 2` and `CHAPTER NINE`; lower-case "Chapter Two of this Act" is a cross-reference, not a heading |
| Part | `^PART\s+[IVXL]+\b` followed by a dash and title | 33 Part headings detected in the NTA |
| Section | Pattern in section 1, accepted only as the next expected number. Out-of-sequence matches are logged | 203/203 (NTA gazette), 148/148 (NTAA Gombe) |
| Subsection | `^\(\d{1,2}\)\s` at line start, or `N.\s?(1)` on the section's first line | 253 subsection units across in-scope NTA sections |
| Paragraph / sub-paragraph | `^\([a-z]{1,2}\)` and `^\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\)` | Kept inside their subsection; used only for splitting long subsections |
| Definitions | Line starting with a quoted term followed by `means` or `includes`, inside an interpretation section | NTA s202 is 5,076 tokens of definitions and is split per definition |
| Marginal notes | Margin region (section 1), joined across lines and de-hyphenated (`for compa-`/`nies`) | Become the section `heading` |
| Schedules | Line equal to `<Ordinal> Schedule` (either case). After the first one, section detection stops. Schedule paragraphs are `^\d+\.` restarting at 1, with `Part` headings inside | 12 of 14 NTA schedule headings found; the Ninth and Tenth (both out of scope) sit in table pages and were missed. All four in-scope schedules (First, Third, Eleventh, Thirteenth) were found |
| Cross-references | Regex over the text: `section(s)? \d+(\(\d+\))*(\([a-z]+\))*`, `subsection \(\d+\)`, `paragraph \([a-z]\)`, `(First|…) Schedule`, `Chapter (One|…)`, `Part [IVX]+`, each with optional "of this Act" or "of the Nigeria Tax Administration Act". Relative references ("subsection (2)") resolve to the enclosing section | Stored as structured `cross_refs` (section 5). Used in Phase 4 for optional neighbour expansion |
| Gazette and National Assembly offset | A generated `section_map.csv`: NTA gazette `n` equals National Assembly `n` for 1 to 88; gazette 89 has no counterpart; gazette `n` equals National Assembly `n-1` for 90 to 203. NTAA: identity map across all three texts | From the Phase 1 arrangement-of-sections diff. Checked again in Phase 2b by heading-title similarity per mapped pair |

Two structural anomalies were found in the cited NTA, and they need new DQ entries:

- **DQ-06:** the gazette's **Thirteenth Schedule** (page 209) has an "Exempted products"
  column heading but no product text. The page has no images, and the 17 characters right of
  the second column are the heading itself. The National Assembly copy lists the products
  (dairy, cocoa, animal feed and so on).
- **DQ-07:** gazette **section 57** runs from subsection (2) to (4); there is no (3). It also
  has a ₦20bn threshold where the National Assembly text has ₦50bn.

The parser must keep the printed labels exactly as they appear. A chunk for s57(4) is labelled
(4), not renumbered.

Comparison texts need the same parser with per-file tolerance. On the probe, the National
Assembly NTA reprint stopped at section 123 (its pages around Chapter Five lay out differently)
and the National Assembly NTAA had one gap at section 15 (`15. .`). Phase 2b must reach
202/202 and 148/148 on them before building the register.

## 4. Tables (DQ-05)

pdfplumber's default (ruling-line) table finder returns no tables. These schedules have no
rules. The text strategy (`vertical_strategy="text"`, `horizontal_strategy="text"`) recovers the
columns: for the Thirteenth Schedule it yields rows such as `['Crop', 'Growing of']`,
`['Production', 'perennial and']`.

Design:

1. Table pages are listed explicitly per file in a small config (`ingest/tables.yml`: file,
   page, schedule, column headers). Auto-detecting tables risks treating indented body text as
   a table.
2. Extract with the text strategy. Rebuild logical rows by merging physical rows until the first
   column changes (a new sub-sector starts).
3. Write each logical row as one line, `Sub-sector: Crop Production | Activity: Growing of
   perennial and non-perennial crops | Exempted products: (none printed)`, so retrieval sees
   the column meaning.
4. **Check:** for every configured table, the multiset of words in the rebuilt rows must equal
   the multiset of words pdfplumber finds in the table's bounding box. Any lost or duplicated
   word fails the build. One rendered image per table goes to the spot-check pack (section 9).

In-scope tables are few. The Thirteenth Schedule is one; the First Schedule (capital allowances)
has rate tables. Out-of-scope tables (Ninth and Tenth Schedules, petroleum schedules) are not
configured.

## 5. Chunk schema

One JSON object per line in `data/derived/chunks/v1_chunks.jsonl`.

| Field | Type | Example | Notes |
|---|---|---|---|
| `chunk_id` | string | `nta2025:s56` | Deterministic, built from legal numbering in the cited text. Forms: `nta2025:s17(1)-(3)` (packed subsections), `nta2025:s202:def:small-company`, `nta2025:sch1:pt1:para2`, `nta2025:sch13:row:crop-production`, `ntaa2025:s41(8)` |
| `citation` | string | `Nigeria Tax Act 2025, section 56` | Human label shown in answers: `… section 17(1) to (3)`, `… section 202, definition of "small company"`, `… First Schedule, Part I, paragraph 2` |
| `act_id` | string | `nta2025` | `nta2025` or `ntaa2025` |
| `act_title` | string | `Nigeria Tax Act, 2025` | |
| `unit_type` | string | `section` | `section`, `subsection_group`, `definition`, `schedule_paragraph`, `schedule_row` |
| `chapter` | object | `{"number": "TWO", "title": "Taxation of Income of Persons"}` | `null` for schedules |
| `part` | object | `{"number": "IX", "title": "Rates of Tax"}` | `null` if none |
| `schedule` | object | `null` | e.g. `{"number": "Thirteenth", "title": "Exemption for agricultural businesses", "part": null, "paragraph": null}` |
| `section` | string | `56` | Gazette (cited) number, as printed |
| `subsections` | array of string | `[]` | e.g. `["(1)","(2)","(3)"]` for a packed group; empty for a whole section |
| `na_section` | string | `56` | National Assembly number from `section_map.csv`; `null` if none (gazette s89) |
| `heading` | string | `Rates of tax for companies` | Marginal note, de-hyphenated |
| `context_header` | string | `Nigeria Tax Act 2025 > Chapter Two > Part IX Rates of Tax > s56 Rates of tax for companies` | Prepended to `text` at embedding time (Phase 3); kept separate so `text` stays verbatim |
| `text` | string | `56. Tax shall be levied, … in the case of –\n(a) a small company, at 0%; and\n(b) any other company, at the rate of 30 per cent from the commencement of this Act.` | Normalised per section 1; line breaks kept |
| `text_source` | string | `pdf_text` | `pdf_text`, `gombe_verified_identical`, `nrs_proofread` |
| `source` | object | `{"file": "raw/nigeria_tax_act_2025_gazette_lirs.pdf", "sha256": "f15edd3b…5b91", "pages": [42]}` | Pages are 1-based PDF page indices. Gazette page labels (A426) go in `page_labels` |
| `page_labels` | array of string | `["A 426"]` | From the running header |
| `cross_refs` | array of object | `[]` | e.g. `{"raw": "section 67 of this Act", "act_id": "ntaa2025", "target": "s67"}`; `target` is `null` if unresolved |
| `discrepancy` | object | `{"flag": true, "register_ids": ["DR-012"]}` | From the register (section 6) |
| `corpus_snapshot` | string (date) | `2026-10-18` | D-10 |
| `edition_date` | string (date) | `2025-06-26` | Gazette date of the cited file (manifest) |
| `tokens_cl100k` | integer | `61` | `text` only |
| `tokens_with_header` | integer | `83` | `context_header` plus `text` |
| `text_sha256` | string | | Detects silent text changes between rebuilds |

### Granularity rule

**Probe 5: token sizes of in-scope units** (`cl100k_base`, body text only).

| | NTA (84 in-scope sections) | NTAA (65 in-scope sections, Gombe text as proxy) |
|---|---|---|
| Total tokens | 30,461 (plus schedules: First 11,449; Third 401; Eleventh 373; Thirteenth 109) | 15,094 |
| Section tokens, median / p90 / max | 208 / 870 / 5,076 (s202 definitions) | 150 / 438 / 2,308 (s147 interpretation) |
| Sections over 512 / over 8,192 | 13 / 0 | 5 / 0 |
| Sections under 40 tokens | 8 (e.g. s1, s144, s195) | 3 |
| Subsection units, median / p90 / max; over 512 | 62 / 156 / 5,076; 8 | 53 / 125 / 2,308; 1 |

Rules:

1. **One chunk per section** when `tokens_with_header ≤ 450`.
2. Otherwise, **pack consecutive subsections greedily** up to 450 tokens. Never cross a section
   boundary. The id and citation use the range, for example `s17(1)-(3)`.
3. A single subsection over 450 tokens is split at its paragraphs `(a)`, `(b)`, … (id
   `s17(2)(a)-(d)`). An interpretation section is split **one definition per chunk**.
4. Schedules: one chunk per schedule paragraph (packed in the same way). Tables get one chunk
   per logical row group of up to 450 tokens.
5. **No merging across sections.** Tiny sections (under 40 tokens) stay whole. Merging two
   sections would give a chunk two citations and break the one-chunk-one-citation rule. The
   context header gives tiny chunks enough retrieval signal.

**Why 450:** the 512-token local baseline (`bge-small-en-v1.5`, which Phase 4 also tests) would
silently truncate anything longer. Its WordPiece tokenizer usually produces somewhat more
tokens than `cl100k_base` on legal text with numbers, so 450 leaves about 12% headroom.
Phase 2b checks this with the bge tokenizer. `text-embedding-3-small` (8,192) could take whole
sections, but one chunk set for both models keeps the Phase 4 comparison fair. Smaller,
single-provision chunks also make citations precise.

Estimated output: 500 to 650 chunks, about 75,000 tokens, under 1 MB as JSONL.

## 6. Discrepancy register (D-09)

**File:** `data/curated/discrepancy_register.csv`, committed and hand-reviewed. It is curated,
not purely derived: every row has a reviewer.

| Field | Example |
|---|---|
| `register_id` | `DR-012` |
| `act_id` | `nta2025` |
| `cited_unit` | `s202:def:small-company` |
| `gazette_section` | `202` |
| `comparison_text` | `nass` (or `gombe` for the NTAA earlier print) |
| `comparison_section` | `201` |
| `type` | `substantive`, `wording`, `numbering` or `missing_content` |
| `cited_reading` | `a company that earns gross turnover of ₦50,000,000 or less … professional services shall not be classified as a small company` |
| `comparison_reading` | `a business that earns gross turnover of ₦100,000,000 or less …` |
| `similarity` | `0.684` (letters and digits only) |
| `auto_class` | Classifier suggestion |
| `reviewed_by`, `reviewed_on` | `Kene`, `2026-10-…` |
| `notes` | |

Types:

- **substantive:** a difference in a number, amount, rate, percentage, date, `shall`/`may`/
  `not`, a party, or an added or removed subsection.
- **wording:** a word-level difference that doesn't change the legal effect, in the reviewer's
  judgement. It is still flagged.
- **numbering:** a numbering anomaly beyond the systematic offset (for example gazette s57
  missing (3)). The systematic offset from s89 is one rule row, `DR-000`, plus `section_map.csv`,
  not one row per section.
- **missing_content:** text present in one copy and absent in the other (Thirteenth Schedule
  products; NTAA s41(8) deposit clause).

Pure punctuation and spacing differences (` ;` against `;`, en dash against em dash) are not
recorded.

**Method:**

1. Parse both texts with the same parser.
2. Align by `section_map.csv`; check each mapped pair's marginal-note titles match.
3. Per in-scope section, compare letters-and-digits-only text. Identical means no row.
4. For non-identical sections, diff at subsection level to locate each difference.
5. Auto-classify, with the `substantive` triggers above.
6. A reviewer confirms every candidate row against both PDFs and fills `reviewed_by`.

**Probe 6: expected review load.** Of 48 in-scope NTA sections in the range 1 to 59 that both
parsers found, 25 are identical once only letters, digits and `%` are kept. 11 are at 0.99 or
above, 9 between 0.95 and 0.99, and 3 below 0.95: s27 (0.934), s56 (0.684), s57 (0.425). Scaled
to all in-scope NTA and NTAA sections, expect roughly 60 to 90 candidate rows, about two to
three hours of review.

**How answers use it** (Phase 5 and Phase 6):

- A retrieved chunk with `discrepancy.flag = true` brings its register rows into the
  generation context.
- The answer must cite the gazette reading and then state the other reading, for example "The
  National Assembly text of this Act gives ₦100m instead (see DR-012)."
- The Phase 6 verification pass fails any answer that cites a flagged chunk without the
  disclosure.

## 7. Derived data: location and commit policy

| Artefact | Path | Committed? | Why |
|---|---|---|---|
| Re-OCR page text, NTAA NRS | `data/derived/ocr/ntaa_nrs/p001.txt` … `p089.txt` plus `ocr_manifest.json` (engine versions, settings, page image sha256) | **Yes** (about 200 KB) | OCR is slow and not bit-reproducible across library versions. Committing it makes every later build deterministic |
| Chunks | `data/derived/chunks/v1_chunks.jsonl` plus `build_manifest.json` (input sha256s, code commit, library versions, counts) | **Yes** (under 1 MB) | Diffable rebuilds; Phase 4 hit-rate results tie to an exact chunk set; reviewers can read chunks without running anything |
| Section map | `data/derived/section_map.csv` | Yes | Small, generated, reviewed once |
| Discrepancy register | `data/curated/discrepancy_register.csv` | Yes | Hand-reviewed |
| NTAA proofread overrides | `data/curated/ntaa_nrs_proofread/s<nnn>.txt` | Yes | Hand-proofread; never regenerated |
| Embeddings | Database only (Phase 3) | No | Rebuilt from chunks |

**Recommendation: commit the derived chunks and OCR text** as above. The alternative, keeping
`data/derived/` git-ignored and rebuilding on demand, saves under 1.5 MB but loses diffability.
It also makes the 30-minute OCR step part of every fresh clone.

## 8. Fetch script (D-11, built in Phase 2b)

`python/ingest/fetch_raw.py`:

1. Read `data/manifest.csv`; select rows with `committed` starting `no`.
2. For each: if the target file exists, verify its sha256. If it matches, report `present` and
   do nothing; if not, **stop with an error and never overwrite**.
3. If absent, download `source_url` to `<target>.part`, streaming, with a timeout and a
   size cap of 1.5 × the manifest `bytes`. Compute sha256 while writing.
4. If size and hash match the manifest, rename `.part` to the target; otherwise delete `.part`
   and exit non-zero, printing expected and actual values.
5. Optional `--mirror <url>` for the GitHub Release backup (D-11, Phase 10), tried only if the
   source fails.
6. Exit 0 only when every non-committed file is present and verified. Never execute or open
   the file other than hashing it.

Tests use a local `http.server` on a temporary directory: success, hash mismatch, existing
correct file, existing wrong file (no overwrite), and an unreachable URL.

## 9. Exit-gate spot-check protocol

- **Sample:** `python/ingest/spot_check_sample.py --seed 20261018` draws 20 chunks, stratified:
  10 from NTA sections, 6 from NTAA sections (at least 2 of them `nrs_proofread` or
  `gombe_verified_identical`), and 4 from schedules (at least 1 table row). Within each
  stratum, at least 2 must have `discrepancy.flag = true`. Same seed and same chunk set give
  the same sample.
- **Pack:** for each sampled chunk, the script writes the chunk JSON and a rendered crop of its
  source page(s) from the **cited** file into `docs/phase02_chunking/spot_check/`. The PNGs
  are git-ignored and regenerated; only the results are committed.
- **Reviewer checks** (each Pass or Fail, with a note):
  1. The section, subsection or schedule number is correct in the cited file.
  2. The text matches the page verbatim after the section 1 normalisation: nothing missing,
     nothing extra, no marginal-note bleed.
  3. The heading (marginal note) is correct.
  4. Page numbers are correct.
  5. Chapter and Part are correct.
  6. The discrepancy flag is consistent with the register.
  7. Cross-references are resolved correctly.
- **Pass rule:** 20/20 on checks 1 and 2, and at most 2 minor fails across checks 3 to 7. Any
  fail on check 1 or 2 means fixing the cause, rebuilding, and re-sampling with seed
  `20261019`. It never means correcting the one chunk.
- **Record:** `docs/phase02_chunking/spot_check_results.csv` (chunk_id, seed, each check,
  reviewer, date, note) plus a one-paragraph summary in `spot_check.md`.
- **Reviewer (proposed):** an independent reviewer who didn't build the chunker checks all 20.
  The project owner then confirms 5 of the 20, chosen by that reviewer.

## 10. Test plan (Phase 2b, pytest)

| Test | Expectation |
|---|---|
| Section count, cited NTA | Exactly 203, numbered 1 to 203 in order |
| Section count, cited NTAA (re-OCR) and Gombe | Exactly 148 each |
| Section count, National Assembly NTA and NTAA | Exactly 202 and 148 |
| In-scope coverage | Every in-scope section and schedule in [v1_corpus_definition.md](../phase01_corpus/v1_corpus_definition.md) appears in at least one chunk, and every subsection label appears in exactly one chunk |
| No out-of-scope leakage | No chunk from an out-of-scope section (for example NTA s58, Chapter Three) |
| Monotonic order | Chunks in file order are monotonic by (act, section, first subsection) |
| Unique, stable ids | `chunk_id` unique. Two builds from the same inputs give byte-identical JSONL (sha256 compared) |
| Size limits | Every `tokens_with_header ≤ 450` (cl100k); with the bge tokenizer every chunk ≤ 512 |
| Verbatim text | For 30 fixed chunk ids (golden file), `text` equals a hand-checked expected string |
| Known anomalies | s57 keeps labels (1), (2), (4); the Thirteenth Schedule row text says "(none printed)"; the NTAA s41 text has no deposit clause |
| No margin bleed | No chunk `text` contains a known marginal note string at line start (for example `Rates of tax`) |
| ₦ normalisation | No `N` immediately followed by optional space and a digit remains in any `text` |
| Section map | Gazette 89 has no counterpart; gazette 148 maps to National Assembly 147; marginal-note titles of mapped pairs are similar (ratio of at least 0.8) |
| Table check | Word multiset of rebuilt table rows equals the bounding-box words |
| Register integrity | Every `register_ids` entry exists in the register; every register row with `type ≠ numbering` points to an existing chunk; every row has `reviewed_by` |
| Manifest | Existing `test_manifest.py` still passes; every `source.sha256` in the chunks equals the manifest |
| Fetch script | The cases in section 8 |

## 11. Phase 2b build breakdown

| Step | Work | Kind of work |
|---|---|---|
| 2b-1 | `fetch_raw.py` and its tests (section 8); add `requirements-ocr.txt`; pin `pdfplumber`, `pypdfium2`, `tiktoken` in `requirements.txt` | Well specified |
| 2b-2 | Page extraction module (geometry split, normalisation) and the structure parser, with section-count and anomaly tests for all five files | Design-heavy: layout edge cases decide citation correctness |
| 2b-3 | NTAA re-OCR run (one-off, about 30 minutes), committed OCR text and `ocr_manifest.json`, Gombe/NRS alignment, list of differing sections | Well specified, long-running |
| 2b-4 | Hand-proofreading the differing NTAA sections against page images | Human review |
| 2b-5 | Chunker (granularity rules, schema, cross-references, tables config) and its tests | Design-heavy |
| 2b-6 | Section map, register candidate generation and classifier; then review of every candidate row | Well specified generator, then human review |
| 2b-7 | Spot-check sample script, pack generation, the 20-chunk review, results committed | Well specified script, then independent review |

Steps 2b-1 and 2b-2 can run in parallel; they touch different files. 2b-3 needs 2b-2;
2b-5 needs 2b-2 and 2b-4; 2b-6 needs 2b-5; 2b-7 comes last.

## 12. Decisions (approved 2026-10-18, D-12)

| # | Decision | Approved choice |
|---|---|---|
| 1 | Extraction library | pdfplumber with the geometry split (section 1) |
| 2 | NTAA scan strategy | Alignment with a fresh re-OCR as the NRS reading (section 2) |
| 3 | Chunk granularity cap | 450 cl100k tokens including the header; section-first, subsection packing, no cross-section merging (section 5) |
| 4 | Derived data | Commit chunks, OCR text, section map and register (section 7) |
| 5 | Spot-check reviewer | An independent reviewer checks 20; the project owner confirms 5 (section 9) |
| 6 | Thirteenth Schedule gap (DQ-06) | Cite the gazette as printed ("none printed") and give the National Assembly products list through the register, consistent with D-09 |
