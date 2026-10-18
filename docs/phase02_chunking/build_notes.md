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
