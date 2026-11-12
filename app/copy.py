"""Every UI text of the app, in one place for one review (interface design, Sections 3, 6.4, 7, 8, 9, 13).

Rules (design Section 13):

- Hints say only how to rephrase or what to do next, never what the law says.
- The disclaimer, the personal-data screen text, its button label and the "service not available" text are
  imported from the code that owns them, never retyped here.
- Texts the pipeline renders (refusal text, decline text, gaps, sources, notes, disclosures) are not here:
  the card shows them exactly as `generate()` rendered them.

Import as `from app import copy as ui` (the module is named `copy` by the design; it is always imported
through the `app` package so it never shadows the standard library module).
"""
from __future__ import annotations

from generation.generate import DISCLAIMER  # noqa: F401  (re-exported: the one disclaimer text)
from interface.screen import EDIT_BUTTON, SCREEN_TEXT  # noqa: F401  (D-160 text and its only button)
from interface.startup import UNAVAILABLE_TEXT  # noqa: F401  (start-up check failed, Section 3.8)

# Page (Section 3.1) -------------------------------------------------------------------------------------

PAGE_TITLE = "Nigerian SME Tax Reference"
TAGLINE = ("Answers from the text of the Nigeria Tax Act 2025 and the Nigeria Tax Administration Act 2025, "
           "with the sections they come from.")
STATUS_STRIP = "Corpus snapshot: {date} · Official Gazette text"
STATUS_STRIP_UNKNOWN = "Corpus snapshot: not available · Official Gazette text"

SCOPE_TITLE = "What this tool covers"
SCOPE_TEXT = (
    "**Covers** companies: company income tax and the development levy, VAT, and the Nigeria Tax "
    "Administration Act 2025 rules on registration, returns, assessment, objections, payment and penalties.\n\n"
    "**Does not cover personal income tax.** That includes sole traders and business names (their business "
    "profits are taxed as the owner's personal income) and PAYE (employees' tax bands and reliefs). Also not "
    "covered: state and local taxes and levies, stamp duties, customs and excise, petroleum taxes, regulations "
    "and other subsidiary legislation, and tax periods before 2026. Questions on these are declined or answered "
    "with a note."
)

PRIVACY_LINE = (
    "Do not enter personal or taxpayer details: no names, Tax Identification Numbers, NIN or BVN, account "
    "numbers, phone numbers or email addresses. Use made-up figures. Your question and the answer are stored "
    "for 90 days so that answers can be checked, then deleted. Questions are sent to Anthropic and OpenAI to "
    "find and check the answer."
)
PRIVACY_LINK = "Privacy details"
PRIVACY_DETAILS = (
    "**What is stored.** Each question, the answer and how it was checked (the statute passages retrieved and "
    "each statement's verification result), a random key for your browser session, and your feedback. If you "
    "sign in, a one-way key derived from your sign-in account, so that you can see your own past questions.\n\n"
    "**What is not stored.** IP addresses, names and email addresses. Streamlit's usage statistics are off and "
    "no analytics script is used.\n\n"
    "**How long.** 90 days on the public service, then the rows are deleted. Signed-in users can delete their "
    "history at any time from My questions. Locally, nothing is deleted automatically.\n\n"
    "**Who else sees the question.** Anthropic (to write and check the answer) and OpenAI (to search the "
    "statutes); their API data policies apply. The hosting providers process it, and so does the sign-in "
    "provider if you sign in."
)
SINGLE_TURN_LINE = "Each question is answered on its own. Earlier questions in this chat are not used."

QUESTION_LABEL = "Your question"
QUESTION_PLACEHOLDER = "Ask one question about the two Acts"
ASK_BUTTON = "Ask"
QUESTION_MAX_CHARS = 2000  # the guardrails limit (max_question_chars)
EMPTY_QUESTION = "Type a question first."

# Examples (Section 7.5) ---------------------------------------------------------------------------------

EXAMPLES_TITLE = "Example questions"
EXAMPLES_HELP = "Click one to put it in the question box. It is not sent until you press Ask."

# Answer card (Section 3.2) ------------------------------------------------------------------------------

STATUS_LINES = {
    "answered": "Answered from the corpus",
    "not_covered": "Not answered from the corpus",
    "refused": "Refused",
    "error": "Could not answer",
    "app_error": "Could not answer",
}
# UI hint by (status, refusal reason); a different style from the pipeline text, labelled as a tip.
HINT_LABEL = "Tip"
HINTS = {
    ("not_covered", None): "The gaps above say what the retrieved provisions do not contain.",
    ("refused", "invalid_question"): "Ask one question about the two Acts, in under 2,000 characters.",
    ("refused", "section_outside_v1"): "See what this tool covers, above.",
    ("refused", "low_relevance"): ("Try naming the tax and what you want to know, for example VAT registration "
                                   "or company income tax rates."),
    ("error", "verification_unavailable"): None,  # the pipeline text already says to try again later
    ("refused", "no_such_section"): None,
    ("refused", "foreign_law"): None,
    ("refused", "unsupported_answer"): None,
    ("error", None): "Please try again later.",
    ("app_error", None): "Please try again later.",
}
REFERENCE = "Reference: {reference}"
PLAIN_TEXT_EXPANDER = "Show as plain text"
THIS_SESSION = "This session's questions and answers"
YOU_ASKED = "You asked"

# Citation chips and the section reader (Section 3.3) -------------------------------------------------------

CHIP_GAZETTE_PAGES = "Gazette pages: {pages}"
CHIP_DISPUTED = "The National Assembly text differs here; see Differences between official texts."
READ_WHOLE_SECTION = "Read the whole section"
READER_NOTE = "This is the corpus text of the Official Gazette as chunked for this tool."
READER_DIALOG_TITLE = "Corpus text"
READER_THIS_PASSAGE = "This is the passage that was cited."
CHUNK_NOT_FOUND = "This passage could not be loaded."

# Export and feedback (Section 7.5) -----------------------------------------------------------------------

EXPORT_BUTTON = "Export (Markdown)"
EXPORT_FILE = "taxy-answer-{reference}.md"
EXPORT_QUESTION = "Question"
EXPORT_WHEN = "Asked"
EXPORT_REFERENCE = "Reference"
EXPORT_ANSWER = "Answer"
EXPORT_CITED = "Cited text"
EXPORT_PAGES = "Gazette pages"
EXPORT_CONFIG = "Configuration sha256"
FEEDBACK_PROMPT = "Was this answer useful?"
FEEDBACK_REASON_LABEL = "What was wrong? (optional)"
FEEDBACK_REASONS = {
    "wrong_citation": "A citation is wrong",
    "missing_information": "Something is missing",
    "should_have_answered": "It should have answered",
    "should_not_have_answered": "It should not have answered",
    "hard_to_read": "It is hard to read",
    "other": "Other",
}
FEEDBACK_SAVE = "Send feedback"
FEEDBACK_THANKS = "Thank you. Your feedback was saved."
FEEDBACK_FAILED = "Your feedback could not be saved."

# Waiting, limits, errors (Sections 8 and 9) --------------------------------------------------------------

STAGE_1 = "Finding the provisions and checking the answer. This usually takes 5 to 15 seconds."
STAGE_2 = "Still checking. Some answers take up to a minute."
STAGE_3 = "This is taking longer than usual."
STAGE_DONE = "Done."
TIMEOUT_TEXT = "This is taking much longer than usual. The answer will not appear here. Reference {reference}."
UNRECORDED_TEXT = ("The service could not record your question, so it was not sent. Please try again in a few "
                   "minutes.")
WITHHELD_TEXT = ("An answer was produced but could not be recorded, so it is not shown. Please try again later. "
                 "Reference {reference}.")
LIMIT_COST_CAP = "The service has reached its usage limit for today. Please try again tomorrow."
LIMIT_SESSION = "You have used the {n} questions available without signing in. Sign in to ask more."
LIMIT_ANONYMOUS_POOL = ("The questions available without signing in have been used up for today. Sign in to ask "
                        "more, or try again tomorrow.")
LIMIT_DAILY = "You have reached today's limit of {n} questions. It resets at midnight West Africa Time."
LIMIT_GENERIC = "You have reached a usage limit. Please try again later."
BLOCKED_NOTICE_TITLE = "Question not sent"

# Sidebar (Sections 3.1, 7.2, 8) ---------------------------------------------------------------------------

SIDEBAR_SNAPSHOT = "Corpus snapshot {date}"
SIDEBAR_DISCLAIMER = "Research and reference tool. Not tax advice."
SIDEBAR_SIGN_IN = "Sign-in is switched on in a later phase. Until then the app is open without sign-in."
SIDEBAR_SIGNED_IN_AS = "Signed in as {display}"
SIDEBAR_TODAY = "Today (West Africa Time)"
SIDEBAR_QUESTIONS = "Questions asked today: {n}"
SIDEBAR_LEFT = "Questions left today: {n}"
SIDEBAR_SPEND = "Spend today: ${amount}"
SIDEBAR_USAGE_UNAVAILABLE = "Usage figures are not available right now."

# My questions (Section 7.3) --------------------------------------------------------------------------------

HISTORY_TITLE = "My questions"
HISTORY_NOT_SIGNED_IN = "Sign in to see your own past questions. Nothing is shown here without signing in."
HISTORY_EMPTY = "You have no saved questions yet."
HISTORY_UNAVAILABLE = "Your questions could not be loaded right now. Please try again later."
HISTORY_ITEM = "{reference} · {when} · {status}"
HISTORY_ANSWER = "Answer"
HISTORY_SOURCES = "Cited passages"
DELETE_BUTTON = "Delete my history"
DELETE_CONFIRM = ("This deletes all your saved questions and answers at once. It cannot be undone, and you will "
                  "be signed out.")
DELETE_CONFIRM_BUTTON = "Yes, delete everything"
DELETE_CANCEL_BUTTON = "Cancel"
DELETE_DONE = "Your history was deleted ({n} questions)."
DELETE_FAILED = "Your history could not be deleted. Please try again later."

# The local log viewer, app/review.py (Section 5.1; never deployed) ------------------------------------------

REVIEW_TITLE = "Query log viewer (local)"
REVIEW_INTRO = ("What was retrieved and how each statement was checked, for one query. Read-only. This page is "
                "never part of the public app.")
REVIEW_NOT_READ_ONLY = ("REVIEW_DATABASE_URL is not set, so the viewer is using the project connection (the owner), "
                        "which is not read-only. It only reads.")
REVIEW_UNAVAILABLE = "The audit log could not be read. Check the database and the connection settings."
REVIEW_FIND = "Find a query"
REVIEW_REF_LABEL = "Reference (Q-xxxxxxxx), query id or record id"
REVIEW_OPEN = "Open"
REVIEW_FILTERS = "Or list queries"
REVIEW_FILTER_DAY = "Day (West Africa Time)"
REVIEW_FILTER_ANY = "Any"
REVIEW_FILTER_STATUS = "Status"
REVIEW_FILTER_REASON = "Refusal reason"
REVIEW_FILTER_TIER = "Tier"
REVIEW_FILTER_FALLBACK = "Plain-text fallback (ui_fallback)"
REVIEW_FILTER_UNFINISHED = "Unfinished (receipt only)"
REVIEW_FILTER_RATING = "Feedback"
REVIEW_RATINGS = {"Any": None, "Thumbs up": 1, "Thumbs down": -1}
REVIEW_LIST_EMPTY = "No queries match."
REVIEW_PICK = "Open this query"
REVIEW_CONFIG_WARNING = ("The configuration sha256 of this query is not the pinned config_p7 value. Do not treat "
                         "this answer as one of the measured system.")
REVIEW_NO_CORPUS_TEXT = ("Chunk text is not available: the reviewer role cannot read the corpus tables. The owner "
                         "applies the grant once (python/audit/grant_review.py, D-169).")
REVIEW_TEXT_CHANGED = "Text changed since this answer (the current corpus text differs from the text at write time)."
REVIEW_HIDDEN = "not shown to the user"
REVIEW_SECTIONS = {
    "header": "1. Header",
    "question": "2. Question",
    "checks": "3. Pre-check and relevance floor",
    "retrieval": "4. Retrieval",
    "generator": "5. Generator output",
    "verification": "6. Verification",
    "tools": "7. Tools",
    "notes": "8. Notes and disclosures",
    "saw": "9. What the user saw",
    "local": "10. Local only",
}
REVIEW_NO_PASSAGES = "No passages: no retrieval was run for this query."
REVIEW_NO_CLAIMS = "No claims were checked."
REVIEW_EVIDENCE_FOUND = "found in the cited chunk text"
REVIEW_EVIDENCE_MISSING = "not found in the cited chunk text"
REVIEW_EVIDENCE_UNKNOWN = "chunk text not available"
REVIEW_SHA_EQUAL = "equals rendered_sha256"
REVIEW_SHA_DIFFERENT = "does not equal rendered_sha256"
REVIEW_SHA_NONE = "no text recorded"
REVIEW_CHUNK_TEXT = "Chunk text"
REVIEW_ERROR_LINE = "Error: {error} · App error: {app_error}"
REVIEW_ROW = "{reference} · {when} · {status} · {question}"
