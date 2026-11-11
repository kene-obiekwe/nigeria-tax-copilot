# Phase 7 tools report

This is a research and reference tool. It is not tax advice and is not a substitute for a licensed
tax professional. It must not be used for an actual filing or compliance decision.

Detail is in the [tools design](tools_design.md). Decisions are D-116 to D-132 in the
[decision log](../00_planning/decision_log.md).

## 1. What the tools do

The generation step can now call two tools, in a short loop (at most three model calls per question).
Everything the tools return still goes through the same checks as before: claims cite retrieved
passages, the verifier removes unsupported claims, and the answer refuses when the corpus does not cover
the question.

- **`lookup_section`.** Fetches a section of the Nigeria Tax Act 2025 or the selected parts of the
  Nigeria Tax Administration Act 2025 when the question or a retrieved provision names it. The text is
  added as extra passages (P9 onward) that claims can cite. A section outside the corpus is reported
  as outside the corpus, not summarised. Design Section 3.
- **`calculate_liability`.** Works out an amount in code, never by the model, for six cases: VAT on a
  supply, company income tax, the development levy, the 10% sum added to tax paid late, the monthly
  penalties for failing to register or file, and the fiscalisation penalty. Figures come from the
  question; rates come from a checked table tied to the gazette text. Where an input is missing, the tool
  declines and says why. Design Sections 4 and 5.
- **How a computed figure is shown.** The system, not the model, renders the figure in a separate
  "Calculation" block, with the working. A figure that the code did not produce cannot appear in a claim or
  gap (the runner figure check). Design Sections 5 and 6.

## 2. The exit check and its result

The exit check is 14 new questions (E01 to E11 calculation, lookup-error and rounding cases; X1 to X3
that must not produce a computed figure), run with a frozen configuration
(`eval/tools/config_p7.json`). The pass rule is that every question passes in the same run (design
Section 9). Phase 7 had at most two runs.

**Result: PASS on run 1 (D-132). Run 2 was not used.**

| Check | Result |
|---|---|
| Status and figures (E01 to E11) | All 11 answered; each shown figure equals the hand-worked one |
| X1 to X3 | No computed item shown; X3 shows the personal income tax scope note |
| Every figure produced by code | Yes, in every computation record |
| Every kept claim supported | All 40 kept claims supported (overseer review) |
| Kene's confirmation | E02, E06 and E11 confirmed on 2026-11-11 |
| Tools as expected | E11 shows the `lookup_section` call and `section_outside_v1` |
| Disclaimer present | Yes, in all 14 answers |

Records: `eval/tools/results/exit_run1.json` (write-once, sha256
`2c5f7fe428d62643273798bda10c1eede77c5167aeb41d1d01bcd344364cd7dd`) and
`eval/tools/results/exit_run1_review.json`. The run record is in design Section 18.

## 3. Spend against the D-116 cap ($2.00)

| Step | Cap | Spent |
|---|---|---|
| Design measurement | $0.30 | $0.0531 |
| Paid checks: `api` tests and regression sample | $0.15 and $0.30 | about $0.1164 |
| Exit-check run 1 | $0.60 | $0.2916 |
| **Phase 7 total** | **$2.00** | **$0.4611 (at most about $0.464)** |

## 4. Scope and limitations text

This is the text for the README and the UI (design Section 10).

> **Section lookup.** The tool can fetch a section of the Nigeria Tax Act 2025 or the selected parts
> of the Nigeria Tax Administration Act 2025 when a question or a retrieved provision names it. It
> returns the Official Gazette text in the corpus dated 18 October 2026, and nothing else. It cannot
> fetch other Acts, regulations, orders or guidance. A section outside the corpus, such as the
> personal income tax sections, is reported as outside the corpus and is not summarised. At most
> three extra passages are added to one answer, and for long sections (for example the
> definitions in section 202) only the parts nearest to the question are returned.

> **Calculations.** The tool works out an amount in code, never by estimate, for six cases only: VAT
> at 7.5% on a stated value or a VAT-inclusive price (sections 148 and 149); company income tax at
> 0% or 30% on stated total profits, with the small-company test (sections 56 and 202); the 4%
> development levy on stated assessable profits (section 59); the 10% sum added to tax paid late
> (Administration Act, section 65(1)(a)); the monthly penalties for failing to register or to file
> returns (sections 100 and 101); and the fiscalisation penalty (section 104). Every figure it uses
> comes from the question. Every rate comes from a checked table tied to the gazette text and must
> also be confirmed in the answer's cited provisions. It does not calculate interest (the rates are
> not in the corpus), the 15% minimum effective tax rate, chargeable gains, capital allowances, net
> VAT, withholding tax, personal income tax, or anything in a foreign currency. It does not decide
> whether a supply is exempt or zero-rated, whether an activity is a professional service, or
> whether a figure is the right base. Where the question does not settle an input, it declines and
> says why. Results are rounded once, to the nearest kobo. A calculation shows how a stated rule
> applies to the figures given. It is not an assessment and must not be used for a filing.

## 5. Known limitations (carried to Phase 8)

1. **Duplicate calculation call (E03).** The model made the same calculation call twice. The second
   result was not listed and was withheld, so the answer shows one figure.
2. **Extra lookup (X2).** After a declined calculation the model looked up Section 202. This is
   reported, not a failure (pass rule 5).
3. **X1 claim 4 scope.** One kept claim states the Schedule 1 Part I paragraph 9 disposal rule without
   its "In this Part" (capital allowance) scope. The citation shows the source.
4. **Claim-count watch (D-131).** With tools on, Q003 kept 2 claims (run 17: 4) and Q025 kept 4
   (run 17: 6). All were supported and none removed. This is a usefulness watch item for Phase 8.
5. **Interest is never computed.** The interest rates are not in the corpus; the tool says so when a
   late-payment question needs interest.
6. **A tool call can be skipped.** The tool choice is automatic, so the model may answer without calling
   a tool the question needed. The exit check found no such case, but nothing forces a call.
