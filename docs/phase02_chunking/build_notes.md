# Phase 2b Build Notes

What the Phase 2b build did differently from the [chunking design](chunking_design.md), the
per-file tolerances it needed, and new data-quality findings. One section per build step.

## 2b-2: Page extraction and structure parser

Code: [`python/ingest/extract.py`](../../python/ingest/extract.py) and
[`python/ingest/parse_structure.py`](../../python/ingest/parse_structure.py). Tests:
`tests/test_extract.py` and `tests/test_parse_structure.py`.

### Results

| File | Sections found in order | Required | Arrangement-of-sections entries | Schedules found |
|---|---|---|---|---|
| `nigeria_tax_act_2025_gazette_lirs.pdf` (cited) | 203 | 203 | 203 | 12 of 14 (Ninth and Tenth are sideways table pages) |
| `nigeria_tax_act_2025_nass.pdf` | 202 | 202 | 202 | 12 of 14 (Ninth and Tenth headings absent from the text) |
| `nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf` | 148 | 148 | 148 | 5 of 5 |
| `nigeria_tax_administration_act_2025_nass.pdf` | 148 | 148 | 148 | 5 of 5 |

All four in-scope schedules of the cited Nigeria Tax Act are found: First (pages 128 to 150),
Third (153), Eleventh (203) and Thirteenth (209).

### Deviations from the design

1. **Second margin rule, per file.** The design separates marginal notes by font size (under
   10 pt). The National Assembly reprint of the Nigeria Tax Act sets some notes at body size,
   for example `Manner of 124.—(1) Duties payable …` on page 86. Rule 2 handles these:
   - The margin side of each page is read from where its page label sits ("A 470" on the left
     or right of the running header).
   - The file's usual column edge on that side is learned from the pages where rule 1 works.
   - Characters wholly beyond that edge become margin, but only on lines with an empty
     3-point gutter at the edge, so wide schedule lines are not cut.
   - Rule 2 is off by default. On the cited gazette it would split the Tenth Schedule tables.
2. **Front-matter skip.** The design anchored on `1. The objective`. The parser anchors on the
   long title line `AN ACT TO …`, which every file has and the arrangement of sections doesn't.
   The arrangement of sections is still read, from the pages before that line.
3. **Subsection rule.** Indentation turned out to be unreliable across pages and in the
   reprints; National Assembly NTAA s47(2), for example, is printed flush left. The rule is
   now:
   - A label opens a subsection if it is the next number and is followed by a capital letter.
   - A jump in numbering (gazette s57: (2) then (4)) is accepted only after a line ending a
     clause (`.` `;` `:` `—` `,`, a closing quote, or "and"/"or"). This stops a wrapped
     reference such as `… in subsection` / `(1)(b) and (c) shall …` opening a subsection.
   - Paragraph labels `(a)`, `(i)` need the clause-end condition.
4. **Heading cross-check and fallback.** Each section's marginal-note heading is compared with
   its title in the arrangement of sections. A similarity below 0.5 is logged. Where no note
   sits level with a section's first line, the heading falls back to the arrangement title and
   `heading_source` is set to `"arrangement"`. A heading starts only at the first line of a
   note block, so the tail of a wrapped note (`technol-` / `ogy`) is never taken as the next
   section's heading.
5. **De-hyphenation.** The design joined every end-of-line hyphen before a lower-case word.
   The build keeps the hyphen when the document itself uses the hyphenated form elsewhere
   ("non-resident") or the first half is a common prefix such as "non" or "self". Otherwise it
   joins ("commence-" + "ment" becomes "commencement").
6. **Sideways pages.** Pages where most characters are not upright are flagged and skipped,
   with an anomaly. These are the Ninth Schedule (dutiable instruments) and Tenth Schedule
   (priority sectors) landscape tables on gazette pages 172 to 202, and the assent pages.
   Both schedules are out of scope (D-10). Without this, their reversed text ("snoitpmexE")
   would have been merged into the Eighth Schedule.
7. `extract_pages(..., only=[…])` reads selected pages for fast tests. It can't be combined
   with rule 2, which needs the whole file.

### Per-file tolerances (all in `FILE_PROFILES`)

| File | Tolerance | Why |
|---|---|---|
| Cited NTA gazette | None | |
| National Assembly NTA | `fullsize_margin_notes=True` (rule 2) | Marginal notes at body size on some pages |
| National Assembly NTA | `dash_without_period=True`: accepts `148—(1)` | Section 148 is printed without the full stop |
| Gombe NTAA | None | |
| National Assembly NTAA | None. The probe's gap at section 15 is fixed by the general pattern, which allows a stray `. ` (`15. . Notwithstanding …`) | |

### Anomalies left in the output (pinned by `test_anomalies_are_exactly_the_known_ones`)

| File | Anomaly | Assessment |
|---|---|---|
| Cited NTA | s57: subsection (4) follows (2) | Real, DQ-07 |
| Cited NTA | s140: no note level with the section; heading from the arrangement | The note "Duplicates and counterparts" is printed 70 pt above. Out of scope (stamp duties) |
| Cited NTA | 33 sideways pages not parsed (172 to 202, 214 to 215) | Ninth and Tenth Schedules, assent pages. Out of scope |
| Cited NTA, National Assembly NTA | Ninth and Tenth Schedule headings not found | As above. In the National Assembly copy their pages fall inside the Eighth Schedule's span; out of scope |
| Gombe NTAA | s34: heading from the arrangement | Note not level with the first line |
| Gombe NTAA | s79: note "Power of the President to designate relevant authority" against the arrangement's "Taxation of digital and virtual transactions or assets" | Real inconsistency in that print; the note matches the section text. Proposed DQ-08 |
| National Assembly NTAA | s66, s67, s95: heading from the arrangement | Notes not level with the first line |
| National Assembly NTAA | s145: note "Supemacy of this Act" (sic) against the arrangement's "Conflict with other laws" | Real inconsistency in the reprint. Proposed DQ-08 |

### Subsection numbering differences found (input for the discrepancy register, step 2b-6)

Comparing subsection labels across the section map (gazette `n` against National Assembly `n`
up to 88, and `n-1` from 90), 197 of 202 mapped sections agree. The 5 that differ were each
checked against the text, and all are real differences, not parser errors:

| Gazette | National Assembly | Gazette labels | National Assembly labels |
|---|---|---|---|
| s13 | s13 | (1) to (6) | (1), (2) |
| s27 | s27 | (1) to (6) | (1) to (7) |
| s57 | s57 | (1), (2), (4) | (1) to (4) |
| s156 | s155 | (1) to (6) | (1) to (5) |
| s198 | s197 | (1) to (3) | (1) |

Gombe against National Assembly NTAA: 11 sections differ (s20, s29, s41, s61, s62, s64, s65,
s79, s121, s123, s129), for example the Gombe s41(8) deposit clause and the Gombe s65(4)
compound-interest clause. These are comparison prints. The cited NTAA (the NRS print) is
compared in step 2b-3.

### Proposed data-quality entries

- **DQ-08:** marginal notes that contradict the same print's arrangement of sections: Gombe
  NTAA s79 and National Assembly NTAA s145. Both are comparison texts; the cited texts have
  none.
- **DQ-09:** the cited gazette's Ninth and Tenth Schedules are landscape tables set as rotated
  text (pages 172 to 202), and the National Assembly copy's text layer has no headings for
  them. Both are out of scope for v1, and their content is not extracted. Bringing them into
  scope later needs a rotated-table extractor.

### Run time

Parsing all four files takes about 55 seconds (the cited Nigeria Tax Act about 20 seconds).
The structure tests parse each file once per test session.

## 2b-3: NTAA re-OCR, parse and alignment with the Gombe print

Code: [`python/ingest/ocr_ntaa.py`](../../python/ingest/ocr_ntaa.py) (runs in `.venv-ocr`, D-13),
the OCR input path in [`python/ingest/parse_structure.py`](../../python/ingest/parse_structure.py),
[`python/ingest/align_ntaa.py`](../../python/ingest/align_ntaa.py) and
[`python/ingest/scope.py`](../../python/ingest/scope.py). Tests: `tests/test_ntaa_ocr.py`, plus the
NRS print added to `tests/test_parse_structure.py`. Outputs: `data/derived/ocr/ntaa_nrs/`
(89 `pNNN.json` box files, 89 `pNNN.txt` reading-order views, `ocr_manifest.json`; 1.9 MB) and
[`data/derived/ntaa_alignment.csv`](../../data/derived/ntaa_alignment.csv).

### Determinism

- Full-page pass: pages 10 and 26 were OCR'd twice. JSON, text and rendered-image hashes were
  byte-identical.
- Gap-fill pass (below): pages 11 and 42 were refilled twice from the same pass-1 boxes. JSON
  and text were byte-identical.
- The committed output was produced as a full pass-1 run followed by a `--refill` run. A single
  from-scratch run composes the same two deterministic steps, but it has not been run end to
  end as one job.

### Results

| Measure | Value |
|---|---|
| Pages | 89, all with output |
| Text boxes | 3,964, of which 60 come from the gap-fill pass (on 29 pages) |
| Run time | Pass 1: 2,039.7 s (34 min). Gap-fill refill: 2,031.2 s (34 min; `ocr_manifest.json` records this run). A from-scratch run is about 60 minutes |
| Sections parsed in order | **148 of 148**; arrangement of sections 148 of 148; all 5 schedules found |
| In-scope NTAA sections (65) | **59 identical** to the Gombe print, **6 different**: s11, s29, s34, s39, s41, s147 |

### Deviations from the design

1. **Gap-fill second OCR pass.** The full-page pass sometimes drops a whole line. On page 42 it
   missed the first line of section 69, which stopped the parse at section 68. Missed lines
   leave gaps that look like paragraph breaks (both are about twice the line pitch), so every
   body gap over 1.8 times the page's median pitch is re-read on its own, along with a band
   below the last line. The detector size for these bands is 1,280 px. Only boxes that overlap
   no existing box are kept, and they are tagged `"pass": 2`. This recovered s69, s11(3) and
   others. `--refill` reruns only this pass over the existing pass-1 boxes.
2. **Comparison mappings added** in `align_ntaa.py`. They are applied to both texts the same
   way, so they can only make two prints that read the same compare equal:
   - ₦ removed. The OCR keeps the sign as "N" when the space before it is lost.
   - "o" mapped to "0" next to a digit or comma inside a figure (`N1,0oo.oo0`). This is
     applied after whitespace removal, because the OCR also drops spaces ("to10"), and only
     then is the mapping symmetric.
   - "(l)" or "(I)" read as "(1)" anywhere. The design allowed label mappings only before a
     capital letter.
3. **In-scope list in code.** `scope.py` holds the 65 in-scope NTAA sections. A test checks the
   list against the table in the v1 corpus definition.

### NRS print profile: tolerances

Profile `nigeria_tax_administration_act_2025_gazette_nrs.pdf` in `FILE_PROFILES`. It reads the
committed boxes and never opens the scan.

| Setting or fix | What it handles |
|---|---|
| Boxes rescaled to a 612-pt page | So the PDF thresholds apply |
| Column edges from the median long line | A few lines overrun the justified edge |
| `OCR_RIGHT_NOTE_START = 0.72` | Right-hand notes can start just inside the median edge (0.76 to 0.79 of the width) |
| `note_line_gap = 20`, `align_tolerance = 6`; notes grouped into blocks and given to the nearest section start; a block also splits at a gap of at least 15 pt level with a section start | In the scan, notes are centred on their section, and within-note gaps run up to 15.3 pt. The notes of back-to-back sections are 18.5 pt apart |
| O1 | Spaces inside a label: `(1 )` becomes `(1)` |
| O2 | `l`, `I` or an empty label at a section start: `10. (l)` and `94.()` become `(1)` |
| O3 | `(1l)` becomes `(11)` |
| O4 | No space after a label: `(2)An` becomes `(2) An` |
| O5 | `CHAPTERTWO`; `PARTII=REGISTRATION`; title-case `Part II-Offences` |
| O6 | `ANACTTO` becomes `AN ACT TO` (the front-matter anchor) |
| O7 | `l`, `I` or `O` inside a leading number: `10l.` becomes `101.` |
| O8 | `FirstSchedule` becomes `First Schedule` |
| O9 | A box that reads a note together with its body line is split at an embedded section number (note on the left) or at the word nearest the column edge (note on the right) |
| O10 | `ofN5,000,000` becomes `of ₦5,000,000` |
| Arrangement of sections | Up to two stray glyphs between the number and the title (`1.( Objective`, `74.-Rejection`, `30.1 Disclosure`). This applies to every file; entries are accepted only in sequence |

The four text PDFs' pinned anomalies and section counts are unchanged.

### Anomalies left (pinned by the tests)

| Anomaly | Assessment |
|---|---|
| s12: subsection (5) follows (3); s62: (5) follows (3) | The OCR missed the line holding (4) even after gap fill: on page 13 it recovered only the last word, "gas". Both sections are out of scope |
| s34: heading from the arrangement | The note is printed about 56 pt below the section start, as in the Gombe print |
| s79: note "Power of the President to designate relevant authority" against the arrangement's "Taxation of digital and virtual transactions or assets" | The **cited** print has the same inconsistency as the Gombe print. DQ-08 said the cited texts had none; proposed DQ-10 corrects that |

### Alignment: the 6 differing in-scope sections (for proofreading in 2b-4)

| Section | NRS pages | What differs (from the diff summary; not proofread) | Likely cause |
|---|---|---|---|
| s11 | 11-12 | A paragraph (c) line and an "(f)" missing; "(a)" read as "(d)" | OCR misses and misreads |
| s29 | 21-22 | "(a)" read as "(d)"; words around a threshold ("…month amount…") | OCR misreads; the threshold may also differ between prints (DQ-02 reported ₦25m and ₦100m in Gombe) |
| s34 | 23-24 | NRS has an extra "13"; a "(c)" missing | OCR misread, or a cross-reference that differs |
| s39 | 26 | NRS adds "or the Nigerian Naira at the prevailing exchange rate…" and "and payments" | **Genuine print difference** (payment in Naira allowed) |
| s41 | 26-27 | NRS has no "(8) Where a taxpayer is dissatisfied … pay 20% …" | **Genuine print difference** (deposit clause removed; DQ-02) |
| s147 | 69-74 | "5" against "10" in one place; extra "a", "f" | To be checked: a figure in the interpretation section, or OCR |

The other 59 in-scope sections are identical after the mappings. Their chunks will use the
Gombe text verbatim (`text_source = "gombe_verified_identical"`), citing the NRS print and NRS
pages.

### Proposed data-quality entries

- **DQ-10:** corrects DQ-08. The cited NRS print's s79 marginal note also contradicts its own
  arrangement of sections, as the Gombe print's does. Headings are kept as printed.
- **DQ-11:** the re-OCR of the NRS scan misses whole lines. One pass missed lines on many
  pages. After gap fill, s12(4) and s62(4), both out of scope, are still missing, and in-scope
  sections s11, s29, s34 and s147 have OCR misreads. Strict alignment sends every affected
  in-scope section to proofreading; none can be marked identical by mistake.

## 2b-4: Proofreading the six differing NTAA sections

Files: [`data/curated/ntaa_nrs_proofread/`](../../data/curated/ntaa_nrs_proofread/index.csv)
(`s011.txt`, `s029.txt`, `s034.txt`, `s039.txt`, `s041.txt`, `s147.txt`, `index.csv`), the
[proofread report](proofread_report.md), and `tests/test_ntaa_proofread.py`. The review pack
(page images) is in `docs/phase02_chunking/proofread_pack/`, which is git-ignored.

### Method

1. 14 NRS pages (11, 12, 21, 22, 23, 24, 26, 27, 69 to 74) were rendered to PNG with
   pypdfium2. Each has a whole-page image and two half-page images at full scan resolution.
2. Each section was transcribed from the half-page images, applying only the design
   section 1 normalisation. The OCR and Gombe texts were not used as a starting point.
3. Each draft was then diffed against the Gombe text and the re-OCR text, on letters and digits
   with the alignment mappings. A second diff against Gombe included punctuation, with
   whitespace ignored.
4. Every difference was classified. In every print difference, the draft and the re-OCR agree
   against Gombe. In every OCR error, the draft and Gombe agree against the re-OCR. So each
   point rests on two independent readings.

### Counts

| | Count | Sections |
|---|---|---|
| Print differences | 7 (3 substantive, 1 missing content, 3 wording) | s29 (2), s39 (2), s41 (1), s147 (2) |
| OCR errors corrected | 8 | s11 (3), s29 (1), s34 (2), s41 (1), s147 (1) |
| Sections identical to Gombe once OCR errors are corrected | 2 | s11, s34 |
| Punctuation differences from Gombe beyond the substantive ones | 0 | |

Time was not measured precisely. Transcription was most of the work, and s147 (six pages, 55
definitions) was about half of it.

### Surprising

- **Typesetting slips in the cited print.** s29(b)(ii) omits "a month" ("cumulative
  transactions in amount to ₦100,000,000"), and s29(b)(i) prints "amonth". Both are kept as
  printed, pending Kene's decision on P2.
- **s147 "small business" is ₦100m in the NRS print**, against ₦50m in Gombe. This is the NTAA
  counterpart of the NTA small-company dispute (DQ-01).
- **NRS s148 gives commencement as 1 January 2025**, against 1 January 2026 in the long title.
  Proposed DQ-12.

### Missed-line guard

`test_no_line_missed_by_the_reocr_on_in_scope_pages` reads the scan's original OCR layer with
pdfplumber for the 41 NRS pages holding in-scope sections. Every word of 6+ letters that also
occurs in the Gombe print must appear in the re-OCR text of that page or a neighbouring page
(letters only, case-folded). It finds no misses and runs in about 20 seconds. If the scan is
absent, it fails with an instruction to run `fetch_raw.py`.

The allowlist holds the entry the task specified, (58, "though"): an old-layer misread of
"through" in s121. pdfplumber reads that word as "through", so the entry is not currently
triggered. It is kept, documented, for a reading that does produce it.

### Review

Before review, `tests/test_ntaa_proofread.py` had 17 passing and the 6 review-gate tests
(`status = reviewed`, `reviewed_by` filled) failing by design.

Kene reviewed all six sections against the page images on 2026-10-18 and approved them with no
corrections. On P2 (s29(b)(i) "amonth"), Kene decided to keep the text exactly as printed.
`index.csv` now records `reviewed`, `Kene` and `2026-10-18` for each section, and all 23 tests
pass.

## 2b-5: Chunker, section map and v1 chunk build

```
.venv\Scripts\python python\ingest\build_chunks.py
```

The command writes three files: `data/derived/section_map.csv`,
`data/derived/chunks/v1_chunks.jsonl` and `data/derived/chunks/build_manifest.json`. It takes
about 70 seconds, most of it parsing the five prints. Neither output records a time.

The manifest records:
- the sha256 of every input (raw prints, OCR manifest, alignment, proofread files, `tables.yml`,
  tokenizer, `manifest.csv`);
- the Python and library versions;
- the counts, the token statistics and the sha256 of the JSONL.

Two consecutive builds are byte-identical. `test_committed_chunks_equal_a_fresh_build`
rebuilds in process and compares the result with the committed files.

### Counts

400 chunks: 274 from the Nigeria Tax Act and 126 from the Nigeria Tax Administration Act.

| Unit type | NTA | NTAA |
|---|---|---|
| section (whole section in one chunk) | 68 | 57 |
| subsection_group (packed subsections) | 24 | 14 |
| paragraph_group (an oversize subsection split at its paragraphs) | 15 | 0 |
| definition (interpretation sections: NTA s189, s202; NTAA s147) | 143 | 55 |
| schedule_paragraph (First Schedule Part I; Third and Eleventh Schedules) | 22 | 0 |
| schedule_table (Table I; Thirteenth Schedule) | 2 | 0 |

The 143 NTA definition chunks are s202's 120 definitions and s189's 22. "Basic food items" is
split at its paragraphs into (a) to (k) and (l) to (p), which gives one extra chunk.

Text sources:

| Text source | Chunks |
|---|---|
| `pdf_text` | 274 |
| `gombe_verified_identical` | 64 |
| `nrs_proofread` | 62 |

Heading sources:

| Heading source | Chunks |
|---|---|
| `margin` | 250 |
| `gombe_margin` | 121 |
| `schedule_title` | 24 |
| `gombe_arrangement` (NTAA s10, s65, s122) | 3 |
| `gombe_margin_nrs_arrangement` (NTAA s49) | 2 |

Tokens, counted with the context header:

| Measure | cl100k | bge-small |
|---|---|---|
| Minimum | 27 | 24 |
| Median | 95.5 | 89 |
| p90 | 379 | 358 |
| Maximum | 445 | 447 |

By cl100k range: 204 chunks under 100 tokens, 69 from 100 to 199, 46 from 200 to 299, 54 from
300 to 399, and 27 from 400 to 450. No chunk exceeds the 450 cap, and none is over 450 bge
tokens, against a limit of 512.

Cross-references: 229 are captured and 195 have a target. The other 34 are Part references,
paragraph references and references to the other Act.

### Golden chunks and how they were checked

`tests/data/golden_chunks.json` pins 30 chunks with their exact text, citation, unit type, text
source, heading, pages and page labels.

| Group | Chunks |
|---|---|
| NTA sections and subsections | s1, s2, s10(7), s21(p), s56, s57, s144, s148 |
| NTA definitions | s189 "basic food items" (l) to (p), "farming machinery …" (a term that wraps), "fertilisers"; s202 "insurance", "lender" (closed with `''`), "resident", "small company" |
| NTA schedules | First Schedule paragraph 1 "concession", Table I, the Eleventh Schedule, the Thirteenth Schedule table |
| NTAA `gombe_verified_identical` | s1, s2, s15, s22(11), s26, s49(7), s105 |
| NTAA `nrs_proofread` | s29, s39, s41(5) to (8), s147 "small business" |

Method:

1. Each cited page was rendered to PNG with pypdfium2: the LIRS gazette for the NTA, the NRS
   scan for the NTAA.
2. Each chunk's text was read by eye against the printed lines, word by word, including
   figures and naira amounts. The page number and the printed page label were checked too.
3. All 30 matched.

For `gombe_verified_identical` chunks the words match the NRS page. Punctuation and line breaks
are the Gombe print's, by design (D-12 (2)). For example, NRS s1 prints "in order to –" and the
chunk has "in order to—".

The golden check found a parser gap, described under Surprises below.

### Deviations from the design and the prompt

- **Parser (`parse_structure.py`).**
  - The text-input path `parse_section_text` was added (approved). It parses the proofread
    files.
  - `parse_cached` gives one parse per file per process. `align_ntaa.py` and the test modules
    now share it.
  - The per-line section logic moved into `_add_section_line`.
  - Chapter and Part titles now continue onto a following all-capitals line. This fixed the
    truncated NTAA chapter titles.
  - Definition detection was broadened in three ways:
    - a term may be followed by any lower-case word, not only "means" or "includes";
    - the gazette's closers `''` and `'` are accepted (an apostrophe inside a term, as in
      "arm's length", is not a closer);
    - a term that wraps onto a second line is recognised after a line that ends a clause.

  The pinned 2b-2 and 2b-3 tests pass unchanged.
- **Schema.**
  - The extra field `tokens_bge_with_header` records the baseline-model token count.
  - The `heading_source` values are extended for the NTAA heading rules (`gombe_margin`,
    `gombe_arrangement`, `gombe_margin_nrs_arrangement`, `proofread_index`, `nrs_ocr`). The
    last two are not used in v1.
  - Unit types `paragraph_group` and `schedule_table` are added.
- **Definition chunks.**
  - A section's lead-in line ("202. In this Act –") goes with its first definition.
  - An oversize definition splits at its lettered paragraphs.
- **Schedules.** An oversize schedule paragraph splits in three steps:
  1. one chunk per definition (First Schedule paragraph 1 is 1,339 tokens);
  2. an oversize definition at its lettered paragraphs ("qualifying capital expenditure" splits
     into (a) to (d), (e) to (i), and (j));
  3. otherwise at its numbered sub-paragraphs.

  A cross-heading moves to the paragraph it introduces.
- **Tables.**
  - The two tables have no ruling lines, so pdfplumber's table finder is not used.
  - Columns split at the x-position of each header word.
  - Rows split on vertical space: more than 1.35 line pitches, or more than 1.1 when the first
    column starts a new entry.
  - The word-multiset check compares the rebuilt cells with the parser's own text lines for
    the table body. These come from a separate extraction.
  - The Thirteenth Schedule prints blank sub-sector cells for "Livestock processing" and
    "Processing of cocoa". These show "(none printed)", like the empty products column
    (D-15).
- **Cross-references.**
  - Bare words that the committed `CROSS_REF` pattern matches ("this section shall" reads as
    "section s") are dropped in the chunker.
  - References to the other Act keep a null target.
- **NTAA structure and titles.**
  - Chapter and Part come from the NRS print.
  - The display title is the Gombe title when it reads the same letter for letter. Otherwise it
    is the NRS OCR title, re-spaced with a word list. Re-spacing cannot restore lost commas, so
    Chapter One reads "OBJECTIVE JURISDICTION AND REGISTRATION OF TAX AUTHORITIES" where the
    print has a comma after "OBJECTIVE".
  - The section map's NTAA `cited_heading` is the raw NRS OCR reading ("Citationand
    commencement"). The chunks carry the corrected heading.
- **tiktoken** downloads the `cl100k_base` encoding on first use and caches it. The bge
  tokenizer is committed (`python/ingest/assets/`, sha256 pinned in the tests). The cl100k
  file is not, so a fresh machine needs the network once (D-16; the Phase 11 Docker build
  pre-fetches it).

### Section map

`data/derived/section_map.csv` has 499 rows:
- NTA gazette to National Assembly: 203 rows. Gazette s89 "Petroleum royalty" is unmapped, and
  gazette s90 onwards maps to the National Assembly number minus one.
- NTAA NRS to Gombe: 148 rows.
- NTAA NRS to National Assembly: 148 rows.

16 title pairs have similarity below 0.8. Each was looked at and the sections correspond. The
test pins the list.

| Reason | Pairs |
|---|---|
| Reworded between prints | NTA 26/26, 105/104, 114/113, 150/149, 157/156; NTAA National Assembly 7, 8, 81, 145, 148; NTAA Gombe 148 |
| Comparison heading cut short where its margin note wraps | National Assembly NTA 124 "Manner of", 125 "Obligation", 126 "Admissible"; Gombe NTAA 131 "accounts" |
| Marginal note against arrangement title | NTAA 79 (DQ-08, DQ-10) |

### Surprising

- **Definitions closed with two apostrophes.** The gazette closes many terms with `''` instead
  of a quote mark ("lender'' means"). In s202, 16 definitions were folded into the definition
  before them: 15 closed with `''` or `'`, and one whose term wraps over two lines
  ("participation, directly or indirectly, …"). In s189, one wrapped term was folded the same
  way ("farming machinery and implements and farming transportation equipment"). The golden page
  check found this, and the parser fix now gives s202 120 definitions (was 104).
  `test_definitions_do_not_swallow_the_next_definition` guards it.
- **Oversize units.** NTA s189 "basic food items" and First Schedule paragraph 1 exceed the cap
  on their own and split as above.
- **Chapter Two starts in a different place in the Gombe print.** Gombe prints the "CHAPTER
  TWO—RETURNS, ASSESSMENT AND PAYMENTS" and "PART I—RETURNS" headings before s10 (page 12). The
  NRS and National Assembly prints place them after s10, which stays in Chapter One, Part II
  (Registration). Proposed DQ-13.

### Run time

The full test suite runs in about 90 seconds (313 tests). Each print is parsed once per session
through `parse_cached`, and the chunk build runs once in process.
