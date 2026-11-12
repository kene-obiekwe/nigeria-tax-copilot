"""The personal-data screen (interface design, Section 3.6; D-160): a hard block.

    if blocks(question): show SCREEN_TEXT with only an "Edit question" button

It runs in app code before anything is stored or sent, the database included, and before the
pipeline's own pre-check, which it does not touch. A question is blocked if it holds:

- an email address;
- a Nigerian phone number: `+234`, `234` or `0`, then `7`, `8` or `9`, then 9 digits, written
  as one run or with up to three spaces, hyphens or dots between digit groups (`0803 123 4567`,
  `0803-123-4567`, `0803 - 123 - 4567`, `+234 803 123 4567`);
- a run of 10 or more digits without separators that does not directly follow the naira sign
  (a TIN, NIN, BVN or account number, or an amount written as plain digits).

Amounts with commas (`1,000,000,000`) or straight after the naira sign (`₦1000000000`) pass, and the
message tells the user to write amounts that way. The result is a plain yes or no: the screen never
returns, stores or logs the matched text, the pattern that matched or the blocked question.
"""
from __future__ import annotations

import re

NAIRA = "₦"
SCREEN_TEXT = ("Your question may contain personal details (an email address, a phone number or a long ID or "
               "account number). Please remove them. This tool needs only the tax question; use made-up figures. "
               f"Write amounts with commas or the naira sign, for example {NAIRA}1,000,000,000.")
EDIT_BUTTON = "Edit question"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_SEP = r"[ .\-]{0,3}"  # up to three spaces, hyphens or dots between two digits ("0803 - 123 - 4567")
_PHONE = re.compile(rf"(?<![0-9])(?:\+?234{_SEP}|0)[789](?:{_SEP}[0-9]){{9}}(?![0-9])")
_LONG_DIGITS = re.compile(rf"(?<![0-9{NAIRA}])[0-9]{{10,}}")
_PATTERNS = (_EMAIL, _PHONE, _LONG_DIGITS)


def blocks(question: str) -> bool:
    """True when the question must not be sent or stored."""
    return any(p.search(question) for p in _PATTERNS)
