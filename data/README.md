# Data

`data/raw/` holds the source statutory texts. Raw data is **immutable**: files are never edited in
place, and each is pinned by size and SHA-256 hash in [manifest.csv](manifest.csv). Derived data
(chunks, embeddings) is rebuilt from raw by scripts and is not stored here.

**The corpus is a dated snapshot, taken on 2026-10-18.** It reflects the law as published up
to that date. Later amendments, orders, re-gazetting or court rulings are not included until a
new snapshot is taken and logged in the [decision log](../docs/00_planning/decision_log.md).

Check the files against the manifest (exits non-zero on any missing or changed file):

```powershell
.venv\Scripts\python python\ingest\verify_manifest.py
```

## Inventory

Role: **cited** texts are parsed, embedded and cited by the system. **Comparison** texts are kept
unedited to record where official texts differ (D-09); they are not embedded in v1.

| File (`data/raw/`) | Act | Edition | Host | Role | Size | Text layer | Committed |
|---|---|---|---|---|---|---|---|
| `nigeria_tax_act_2025_gazette_lirs.pdf` | Nigeria Tax Act 2025 | Official Gazette No. 117, 26 June 2025 | Lagos State IRS | cited | 1.7 MB | text | yes |
| `nigeria_tax_act_2025_nass.pdf` | Nigeria Tax Act 2025 | National Assembly reprint, January 2026 | National Assembly | comparison | 0.6 MB | text | yes |
| `nigeria_tax_administration_act_2025_gazette_nrs.pdf` | Nigeria Tax Administration Act 2025 | Official Gazette No. 117, 26 June 2025 (current print) | Nigeria Revenue Service | cited | 27.7 MB | scan, poor OCR | **no** (10 MB or more) |
| `nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf` | Nigeria Tax Administration Act 2025 | Official Gazette No. 117 (earlier print) | Gombe State IRS | comparison | 0.9 MB | text | yes |
| `nigeria_tax_administration_act_2025_nass.pdf` | Nigeria Tax Administration Act 2025 | National Assembly reprint, January 2026 | National Assembly | comparison | 0.3 MB | text | yes |

Both Acts are federal statutes. The Nigeria Tax Act commences on 1 January 2026. Full source
URLs, hashes, commencement dates and the exact in-scope parts are in the manifest. The
reasoning is in the [scope verification](../docs/phase01_corpus/scope_verification.md) and the
[v1 corpus definition](../docs/phase01_corpus/v1_corpus_definition.md).

## Fresh clone

One file is not committed (the 27.7 MB Nigeria Tax Administration Act scan from the Nigeria
Revenue Service). After cloning, fetch it and check everything against the manifest:

```powershell
.venv\Scripts\python python\ingest\fetch_raw.py
```

```powershell
.venv\Scripts\python python\ingest\verify_manifest.py
```

`fetch_raw.py` downloads each non-committed file from its manifest URL, accepts it only if the
size and SHA-256 match, and never overwrites an existing file that fails the hash. The source URL
is <https://www.nrs.gov.ng/uploads/NIGERIA_TAX_ADMINISTRATION_ACT_2025_8c945071a7.pdf>.

**No NRS copy of the Nigeria Tax Act.** On 2026-10-18 the Nigeria Revenue Service's "Nigeria
Tax Act, 2025" page carried only a written overview, with no PDF of the Act. The Lagos State IRS
gazette copy is therefore the cited text. It may be an earlier gazette print than the one the
Service applies (DQ-03), and this can't be checked until an NRS or Federal Government Printer
copy is found.

## Known gaps

- **Disputed text.** The gazette and National Assembly texts of both Acts differ, for example
  on the small-company threshold (₦50m against ₦100m) and on section numbering from section 89
  of the Nigeria Tax Act onward. The corpus cites the gazette (D-09), and answers on disputed
  provisions must say so.
- Personal income tax, PAYE, and unincorporated businesses taxed as individuals.
- State and local government taxes and levies.
- Stamp duties, petroleum taxes, surcharge, double taxation relief and economic development
  incentives (in the Nigeria Tax Act, but outside v1 scope).
- Subsidiary legislation and guidance: the General Transition Guidelines (June 2026), the
  Nigeria Tax Administration Order 2026 on late-payment interest, any VAT Modification Order
  2026, and NRS circulars.
- Periods before 1 January 2026, which are governed by the repealed Acts (Companies Income Tax
  Act, Value Added Tax Act and others). They are not held.
