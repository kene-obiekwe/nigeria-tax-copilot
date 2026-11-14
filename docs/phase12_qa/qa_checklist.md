# Phase 12: Final QA checklist (live site)

This is a research and reference tool. It is **not tax advice** and is not a substitute for a licensed tax professional.

A short manual check of the live site, run by Kene (D-215). Result: all seven pass (2026-11-14). Open <https://taxy-1db6.onrender.com> on a phone and on a
computer. Budget: about 3 answered questions, about $0.05; cap $0.15. Write the result of each item in the last
column: pass, fail (say what you saw) or skipped.

| ID | Check | What to look for | Result |
|---|---|---|---|
| Q1 | Live page loads (phone and computer) | The wake notice or Render's loading page, then the app page | pass |
| Q2 | Disclaimer and theme | The disclaimer is visible on the main page; the theme menu offers System, Light and Dark | pass |
| Q3 | Example card, then Ask | After the consent tick, an example card question gets an answer with Section citations that ends with the disclaimer | pass |
| Q4 | Personal income tax is declined | A personal income tax or PAYE question is declined, not answered | pass |
| Q5 | Personal-data screen | A question containing a made-up email address is blocked before it is sent | pass |
| Q6 | Sign-in round trip | Sign in with Google: the profile shows in the sidebar; "My questions" lists the question; "Delete my history" works; log out works | pass |
| Q7 | README on GitHub | The Try it link opens the live site; the About box shows the website and the description | pass |

## Deferred (D-215)

The plan's fresh adversarial pass and the full harness rerun on the live URL are consciously deferred. The reason: G1
and G3 are measured, and the Phase 11 exit checks (gate (a) and (b)) showed that the live system equals the evaluated
one. Gate G2 stays **not met**, as stated in the README.

## Sign-off

| | |
|---|---|
| Date run | 2026-11-14 |
| Run by | Kene |
| Live commit | 9b36d40 (README at dc280c0) |
| Answered questions asked / cost | |
| Overall result | Q1 to Q7 all pass |
| Notes | |

Optional follow-up: the Google consent screen for a non-owner account (K5) was not tried.
