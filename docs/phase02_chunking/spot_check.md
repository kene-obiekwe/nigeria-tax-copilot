# Spot-check of the v1 chunks (exit gate, Phase 2)

Protocol: [chunking_design.md](chunking_design.md) section 9. This page records the method and,
once the review is done, the result.

## Method

- **Script:** `python/ingest/spot_check_sample.py --seed 20261018` draws 20 chunks from
  `data/derived/chunks/v1_chunks.jsonl`. It uses `random.Random(seed)` over chunk ids in sorted
  order, so the same seed and the same chunk file give the same sample. If a constraint cannot be
  met it stops with an error.
- **Strata:**

  | Stratum | Chunks | Constraints |
  |---|---|---|
  | NTA, outside schedules | 10 | at most 3 definition chunks |
  | NTAA | 6 | at most 2 definition chunks; at least 2 with text source `nrs_proofread` or `gombe_verified_identical` |
  | NTA schedules | 4 | at least 1 `schedule_table` chunk |

  Every stratum also has at least 2 chunks with `discrepancy.flag = true`.
- **Refinement of the design (recorded in [build_notes.md](build_notes.md)):** the definition caps
  are not in design section 9. Without them, definition chunks (about 200 of 400) would fill much
  of the sample, and most of the sample should test section numbering.
- **Review pack:** the script writes `docs/phase02_chunking/spot_check/` (git-ignored,
  regenerated). It holds each chunk's JSON, PNG renders of the cited pages (whole page, top half
  and bottom half) from the cited file, and `review_sheet.md`. The sheet gives, for each chunk,
  its place in the Act, heading, full text, linked register rows, resolved cross-references and
  the seven checks.
- **Checks (Pass or Fail, with a note):** 1 number, 2 text, 3 heading, 4 pages, 5 chapter and
  Part, 6 discrepancy flag against the register, 7 cross-references.
- **Pass rule:** 20/20 on checks 1 and 2, and at most 2 minor fails across checks 3 to 7. A fail on
  check 1 or 2 means fixing the cause, rebuilding, and re-sampling with seed `20261019`; the one
  chunk is never corrected by hand.
- **Reviewers:** an independent reviewer who did not build the chunker checks all 20. The project
  owner then confirms 5 of the 20, chosen by that reviewer.
- **Record:** [spot_check_results.csv](spot_check_results.csv), one row per sampled chunk.

## Results

All 20 sampled chunks (seed 20261018) passed all seven checks, so the pass rule is met (20/20 on
checks 1 and 2, no fails on checks 3 to 7) and the Phase 2 exit gate is passed. An independent
reviewer checked all 20 on 2026-11-02, and the project owner confirmed 5 of them
(`nta2025:s26`, `nta2025:s42(1)-(4)`, `ntaa2025:s122`, `ntaa2025:s29` and
`nta2025:sch1:pt1:table1`) without corrections. No re-sample was needed, so seed `20261019` was
not used.

Three observations, none a fail:

- **DQ-17.** The cited gazette prints the marginal note of NTA s26 with the same wording as s22.
  The heading is kept as printed (D-09); the section text is unaffected.
- **DR-262 anchoring.** DR-262 (the missing "Chief Executive Officer" definition) is anchored on
  `ntaa2025:s147:def:calendar-year` by position only. Answer disclosure will have to handle it.
- **Range cross-references.** A range such as "sections 22 to 25" is stored as its two endpoints
  (s22 and s25), not as every section in between.
