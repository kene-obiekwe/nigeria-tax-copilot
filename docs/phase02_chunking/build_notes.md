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

## 2b-6: Discrepancy register and flagged chunk rebuild

```
.venv\Scripts\python python\ingest\build_register.py
.venv\Scripts\python python\ingest\build_chunks.py
.venv\Scripts\python python\ingest\register_pack.py
```

| File | Content |
|---|---|
| `data/derived/register_candidates.csv` | Every candidate difference, with its key, suggested type (`auto_class`), the differing words and readings |
| `data/curated/register_decisions.yml` | The review decision for each candidate, plus the rows taken from the data-quality log |
| `data/curated/discrepancy_register.csv` | The register: built from the candidates and the decisions |

`build_register.py` writes the first and third files. It stops if any candidate has no decision,
or if a candidate's differing words changed after it was reviewed. The chunk build then flags
each chunk that a register row names. `register_pack.py` renders the page crops for the review
(see [register review](register_review.md)).

### Method

The cited text is exactly the chunk text:
- the gazette for the NTA;
- for the NTAA, the Gombe-identical or proofread text.

It is compared with each comparison print through the section map:
- the National Assembly NTA (gazette s89 has no counterpart; from s90 on, section n against
  n - 1);
- the Gombe and the National Assembly NTAA, section for section.

Comparison rules:
- Only letters, digits and "%" are compared, word by word (difflib). Differences separated by up
  to three equal words are joined into one.
- A difference that disappears when spaces are removed is not recorded (design Section 6).
- A National Assembly NTA section number exactly one lower than the gazette's, from 89 on, is
  left to the offset rule (DR-000).
- Interpretation sections are compared definition by definition, matched by term (a term spelt
  slightly differently, at least 0.8 similar, counts as the same definition). The lead-in is
  compared on its own, so a change in definition order is not a difference.
- In the schedules, the "Sections …" reference block at the head is left out. The First
  Schedule is compared for Part I only. The two tables are compared as word multisets.

Each difference is placed in the cited chunk and subsection it falls in, and given an
`auto_class`:
- **missing_content:** one side has 8 or more words the other lacks.
- **substantive:** a figure, "%", a month, shall / may / not, "and" / "or", a party (Service,
  Minister, court, company, person and similar), or a rewrite of 25 or more words.
- **wording:** otherwise. Drafting-style variants of the National Assembly print are suggested
  as wording:
  - "of this section" added;
  - ", 2025" dropped;
  - "LFN";
  - "per cent" against "%", and number words against digits;
  - kobo figures (₦100,000.00);
  - a lone list conjunction.

  Wording differences in the same chunk, subsection and comparison print are merged into one
  candidate.

### Counts

303 candidates (200 NTA, 103 NTAA: 6 against Gombe, 97 against the National Assembly) became 274
register rows, as reviewed:

| Act | Type | Candidates (auto_class) | Register rows (final type) |
|---|---|---|---|
| NTA | substantive | 58 | 15 |
| NTA | missing_content | 34 | 28 |
| NTA | numbering | 0 | 20 |
| NTA | wording | 108 | 116 |
| NTAA | substantive | 27 | 14 |
| NTAA | missing_content | 7 | 5 |
| NTAA | numbering | 0 | 6 |
| NTAA | wording | 69 | 70 |

How the candidates moved:
- 31 were merged into another row about the same point, for example the three s57 lead-in
  candidates into one effective-tax-rate row.
- 1 was dropped (below).
- 23 were reclassified as numbering: cross-reference numbers that differ beyond the offset, and
  subsection renumbering. One of them, DR-029, was moved from wording at review.
- 21 suggested substantive were wording (Gazette names, "and so on", "etc." after "including").
- 5 suggested wording were substantive, for example "Chairman" against "Chief Executive Officer"
  in NTAA s147.
- 3 rows were added from the data-quality log: DR-000 (the offset rule), DR-055 (s57 printed
  without (3), DQ-07) and DR-190 (NTAA Chapter Two placement, DQ-13).

### Chunks flagged

181 of 400 chunks are flagged: 130 of 274 NTA and 51 of 126 NTAA. 49 of them (38 NTA, 11 NTAA)
carry at least one substantive or missing-content row. Wording rows flag their chunks too, as
design Section 6 says. Two consecutive rebuilds are byte-identical.

### Pre-review check against the PDFs

Every reading was matched against its pages by `python/ingest/register_check.py`:
- **Text PDFs:** read straight from the PDF with pdfplumber, word by word in order, with the
  running head removed and up to eight words of marginal note between two words.
- **NRS scan:** read from the committed re-OCR, with at least 90% of the letters aligned.

Five readings in four rows could not be matched automatically. They were checked against page
images, and `test_readings_occur_on_their_pages` pins them:
- DR-075 and DR-117: a marginal note is printed inside a word ("Pen- Cap. A23, sions Act").
- DR-192 and DR-194: the OCR of NRS page 11 is weak, which is why s11 was proofread.

The NRS-side readings of every group A NTAA row were checked against the scan images: s3, s8,
s29, s39, s41, s65, s121 and s147.

### Review

Kene reviewed the register on 2026-10-31 ([register review](register_review.md)) and approved
groups A, B and C with these corrections:
- **Legal-effect notes reworded:** DR-122 (bank), DR-131 (national minimum wage), DR-178
  (Thirteenth Schedule) and DR-261 (authorised officer).
- **DR-029 moved to numbering:** the gazette's "Part IX of Chapter Two" in s20(1) is a printing
  slip for Part XI, as the National Assembly text reads. It was added to DQ-14.
- **DR-269 unchanged (wording):** it covers the kobo figures only. The fixed-assets point ("less
  than" against "not more than" ₦250,000,000) is already DR-270, a substantive row.
- **DQ-16 logged:** NTA s191(2) refers objections and appeals to Chapter Four of the NTAA, which
  in the cited print is Offences and Penalties. Both NTA texts read the same, so no row covers it.
- **D-17:** the construction defaults of the review pack were kept.
- **D-18:** register ids were pinned: each reviewed row's id is stored with its decision.

Every row now records `reviewed_by` Kene and `reviewed_on` 2026-10-31.

### False candidates and dropped rows

Found during pre-review and removed at the source, so that the candidate file does not contain
them:

| Artefact | Cause | Fix |
|---|---|---|
| s189 and s202 lead-in lines ("189. For the purposes of this Part …") compared as part of the first definition | The lead-in split matched the line before a definition | Split at the first line that itself opens a definition |
| "authority officer" (NTAA) and "real estate investment" (NTA) each counted as one missing and one extra definition | Misprinted terms in the National Assembly prints | Terms at least 0.8 similar are paired |
| The "SCHEDULES" banner compared as the end of NTA s203 | The parser attached the banner between s203 and the First Schedule to s203 | Parser fix; the s203 chunk text no longer ends with "SCHEDULES" |
| Table I title words ("CAPITAL ALLOWANCE") counted as table content | The comparison table region started at its title | The table body now starts after the column-heading line |
| About 30 kobo, "of this section", ", 2025" and "LFN" candidates suggested as substantive | Drafting style of the National Assembly prints | Style rules in `style_only` |

Dropped at review: one candidate, `ntaa2025:s22(1)-(10)|nass|5`. The National Assembly NTAA cites
"section 151 of the Nigeria Tax Act", which is gazette s152 under the offset (DR-000).

Not recorded:
- P2 ("amonth" against "a month") is a spacing difference only.
- Gazette s13's numbering difference is out of scope (no chunk).
- DQ-12 concerns NTAA s148, which is out of scope.

### Deviations from the design and the prompt

- **Register fields.** `cited_pages`, `comparison_pages` and `refs` (the DQ, P or 2b-2 item a row
  comes from) are added. The design's `gazette_section` holds the cited section number for both
  Acts.
- **Two new files.** `register_candidates.csv` (derived) and `register_decisions.yml` (curated)
  make the register reproducible and keep each review decision next to its candidate.
- **Numbering rows** cover cross-reference numbers that differ beyond the offset, and DQ-13 (a
  structure difference). Each names its chunk, so that chunk is flagged.
- **Readings.** A merged row joins its readings with " … ". DR-178 (Thirteenth Schedule) gives the
  National Assembly cells joined row by row, not one verbatim line.
- **Pinned ids (D-18).** `register_id` is stored in each decision and manual row. A row added
  later takes the next free number, one above the highest id in use, so ids no longer follow
  chunk order once rows are added.
- **Extraction fix.** `extract.py` turned "N" before a figure into the naira sign even in a Laws
  of the Federation chapter number. "Cap. N107" and "Cap. N3" (gazette s197) and "Cap. N61"
  (National Assembly s201) read "Cap. ₦…". These references are now left as printed. The fix
  changed the s197 chunk text and the readings of DR-106, DR-108 and DR-131; no row was added or
  removed.
- **Chunker.** `apply_register` matches `cited_unit` to the full chunk id only. The parser change
  for the "SCHEDULES" banner changed the s203 chunk (and the JSONL hash, now
  `780467e7d27c38aadd727596db9ecdee87c14c4b66d30bf11e7e1e7a7198e9c0`).

### Surprising

- **The National Assembly NTA leaves out six definitions in s202:** royalties, services,
  supplies, supply of goods, supply of services and venture capitalist. It also defines
  "remainder of assessable profit" twice and "remainder of assessable income" not at all.
- **s57 (minimum effective tax rate) is a different provision in the two texts.** They differ in
  the computation base, the MNE threshold, the turnover threshold and a free-zone exception
  (DR-051 to DR-055).
- **Some provisions exist only in the gazette:**
  - the s163(1)(v) export-profits exemption;
  - the s187 zero rating of electric vehicles;
  - the s196(j) repeal of the Taxes and Levies (Approved List for Collection) Act;
  - the s203 commencement date.
- **Cross-references in the National Assembly prints do not match their own numbering.** For
  example, NTA s154 (its s153) cites section 146 for the VAT rate, which is in its section 147. NTAA s43
  cites sections 40, 64 and 66 where 41, 65 and 67 are meant (DQ-15).
- **The cited gazette has printing slips:** "treasury Acts" and "commercial Acts" for bills
  (s4(6)(d)), "Part IX" for Part XI (s20(1)), a "Thirtenth Schedule" paragraph 2 that does not exist (s187) and "Tenth Schedule
  securities lending transaction" (s202). They are cited as printed (DQ-14).

### Run time

`build_register.py` takes about 100 seconds, most of it parsing the five prints. The register
tests take about 2 minutes on their own, of which the page check is about 30 seconds; in the full
suite the parses are shared.
