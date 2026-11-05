"""Verification pass (D-30 to D-33; guardrails design, section 3).

    from generation.verify import verify_answer
    result = verify_answer(question, status, claims, gaps, chunks, label_of, verifier, disputed_patterns)

Every claim is checked against the full text of the chunks it cites, in two steps that it must
both pass:

1. **Rule check (code):** every figure, rate, amount, date and legal reference in the claim must
   appear in a cited chunk (its text, citation or context header), after both sides are
   normalised. A hardening word ("only", "never", ...) that no cited chunk contains is a hint for
   the LLM check, never a failure.
2. **LLM check (Claude Haiku 5.5):** one call per claim, at most 4 at a time, with only that
   claim's cited passages. A `supported` verdict counts only when one of its evidence quotes is
   found verbatim (after whitespace, dashes and quote marks are normalised, ignoring case) in the
   text or context header of a passage the claim cites (norm/5, D-55).

Gaps are filtered for the disputed flag in code, then classified in one call. The action rule
(design section 3.7) then keeps or removes each claim and decides whether the answer is shown,
refused (`unsupported_answer`) or reported as an outage (`verification_unavailable`). A claim
judged `not_a_claim` (a statement about the passages) is dropped and logged as
`moved_not_rendered`: it is never rendered, not even as a gap, and counts neither as checked nor
as removed (D-59).
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from generation.llm import (VERIFIER_EFFORT, VERIFIER_MAX_TOKENS, GenerationError, LLMClient,  # noqa: E402
                            LLMResponse, cost_usd, request_params)
from generation.prompt import (GAP_CLASSES, GAP_PROMPT_VERSION, GAP_SCHEMA, GAP_SCHEMA_VERSION,  # noqa: E402
                               GAP_SYSTEM_PROMPT, REASONS, VERDICTS, VERIFIER_PROMPT_VERSION, VERIFIER_SCHEMA,
                               VERIFIER_SCHEMA_VERSION, VERIFIER_SYSTEM_PROMPT, build_gap_message,
                               build_verifier_message)

NORMALISER_VERSION = "norm/5"  # norm/2: evidence and citation labels read as their leading passage label;
# norm/3: a Schedule chunk's line-start "N." numbering counts as paragraph N;
# norm/4: the evidence check ignores letter case (D-48);
# norm/5: the evidence check ignores whitespace next to a dash and also searches the context header (D-55)
MAX_WORKERS = 4
MOVED_NOT_RENDERED = "moved_not_rendered"  # D-59
MIN_QUOTE_CHARS = 12
NO_PASSAGES_GAP = "No provisions were retrieved for this question."
HARDENING = ("only", "always", "never", "solely", "exclusively", "in all cases")

FAILED_LINE = ("1 statement was removed because the cited provisions did not support it.",
               "{n} statements were removed because the cited provisions did not support them.")
UNVERIFIED_LINE = ("1 statement was removed because it could not be checked against the cited provisions.",
                   "{n} statements were removed because they could not be checked against the cited provisions.")
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


# Normaliser --------------------------------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_NUMBER_WORD = re.compile(
    r"\b(?:" + "|".join(sorted(list(_UNITS) + list(_TENS) + ["hundred", "thousand"], key=len, reverse=True))
    + r")(?:[\s-]+(?:" + "|".join(sorted(list(_UNITS) + list(_TENS) + ["hundred", "thousand"], key=len,
                                        reverse=True)) + r"))*\b")
_ORDINAL_WORDS = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
                  "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth").split()
_ORDINALS = {w: i for i, w in enumerate(_ORDINAL_WORDS, start=1)}
_ORDINALS["thirtenth"] = 13  # as misprinted in the gazette (DQ-14)
_ORDINAL_ALT = "|".join(sorted(_ORDINALS, key=len, reverse=True))
_CARDINAL_ALT = "|".join(sorted(list(_UNITS) + list(_TENS), key=len, reverse=True))
_MONTHS = {m: i for i, m in enumerate("january february march april may june july august september october "
                                      "november december".split(), start=1)}
_MONTH_ALT = "|".join(_MONTHS)
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}

_DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?({_MONTH_ALT}),?\s+(\d{{4}})\b")
_DATE_MDY = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")
_SCHEDULE = re.compile(rf"\b({_ORDINAL_ALT}|\d+(?:st|nd|rd|th))\s+schedules?\b|\bschedules?\s+(\d+)\b")
_SECTION = re.compile(r"\b(?:sections?|ss?\.)\s*(\d+)((?:\s*\([0-9a-z]+\))*)"
                      r"((?:\s*(?:,|and|or|to)\s*\d+(?:\s*\([0-9a-z]+\))*)*)")
_SUBSECTION = re.compile(r"\bsub-?sections?\s*((?:\(\d+[a-z]?\)\s*(?:,|and|or|to)?\s*)+)")
_PARAGRAPH = re.compile(r"\b(?:sub-?)?paragraphs?\s*((?:\((?:[a-z]{1,4}|\d+)\)\s*(?:,|and|or|to)?\s*)+)")
_PARAGRAPH_NUM = re.compile(r"\b(?:sub-?)?paragraphs?\s+(\d+)\b")
_CHAPTER = re.compile(rf"\bchapter\s+(\d+|{_CARDINAL_ALT}|[ivxlc]+)\b")
_PART = re.compile(r"\bpart\s+(\d+|[ivxlc]+)\b")
_PERCENT = re.compile(rf"({_NUM})\s*(?:%|per\s*cent\b|percent\b)")
_AMOUNT_CURRENCY = re.compile(rf"(?:₦|\bngn\s?|\bn(?=\d))\s*({_NUM})(?:\s*(million|billion|thousand|m|bn)\b)?")
_AMOUNT_SCALE = re.compile(rf"({_NUM})\s*(million|billion)\b(?:\s*naira\b)?")
_AMOUNT_NAIRA = re.compile(rf"({_NUM})\s*naira\b")
_PLAIN = re.compile(rf"(?<![a-z])({_NUM})(?:st|nd|rd|th)?(?![a-z0-9])")
_PAREN = re.compile(r"\((\d+[a-z]?|[a-z]{1,4})\)")
_PAREN_RANGE = re.compile(r"\(([a-z])\)\s*(?:-|to)\s*\(([a-z])\)")
_HEADER_SECTION = re.compile(r"\bs(\d+)\b")
_INNER_PAREN = re.compile(r"\(([0-9a-z]+)\)")
_PASSAGE_LABEL = re.compile(r"\s*(P\d+)\b", re.IGNORECASE)
_LINE_NUMBER = re.compile(r"^[ \t]*(\d{1,3})\.(?=\s)", re.MULTILINE)
_LIST_NUM = re.compile(r"(?:,|and|or|to)\s*(\d+)")
_UNIT_AFTER = re.compile(r"(?:\s+[a-z]+)?\s*(?:%|per\s*cent\b|percent\b|(?:calendar\s+)?(?:days?|weeks?|months?|"
                         r"years?|hours?)\b|million\b|billion\b|thousand\b|hundred\b|naira\b|kobo\b|times\b)")


def _clean(text: str) -> str:
    text = text.replace(" ", " ").replace("­", "")
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("−", "-")):
        text = text.replace(a, b)
    return text


def _words_to_number(words: str) -> list[int]:
    """Numbers in a run of number words: "twenty-one" -> [21]; "six twelve" -> [6, 12]."""
    out: list[int] = []
    current: int | None = None
    last = None
    for word in re.split(r"[\s-]+", words):
        if word in _TENS:
            if current is not None and last != "scale":
                out.append(current)
                current = None
            current = (current or 0) + _TENS[word]
            last = "tens"
        elif word in _UNITS:
            if current is not None and last == "tens" and _UNITS[word] < 10:
                current += _UNITS[word]
                last = "unit"
            elif current is not None and last == "scale":
                current += _UNITS[word]
                last = "unit"
            else:
                if current is not None:
                    out.append(current)
                current = _UNITS[word]
                last = "unit"
        else:  # hundred, thousand
            current = (current or 1) * (100 if word == "hundred" else 1000)
            last = "scale"
    if current is not None:
        out.append(current)
    return out


def _numbers_from_words(text: str, *, units_only: bool = False) -> str:
    """Number words to digits. With `units_only` (the claim side), only words followed by a unit
    ("six months", "two consecutive years", "thirty per cent") count, so prose such as "the two
    sides" is not read as a figure."""
    def repl(m: re.Match) -> str:
        if m.group(0) == "one":
            return m.group(0)  # "one" is too often a pronoun ("any one of the following")
        if units_only and not _UNIT_AFTER.match(text, m.end()):
            return m.group(0)
        return " ".join(str(n) for n in _words_to_number(m.group(0)))
    return _NUMBER_WORD.sub(repl, text)


def _value(number: str) -> str:
    value = float(number.replace(",", ""))
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _scaled(number: str, scale: str | None) -> str:
    mult = {"thousand": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9}.get(scale or "", 1)
    value = float(number.replace(",", "")) * mult
    return str(int(round(value))) if abs(value - round(value)) < 1e-6 else f"{value:g}"


def _roman(text: str) -> int | None:
    if not re.fullmatch(r"[ivxlc]+", text):
        return None
    total = 0
    for i, ch in enumerate(text):
        v = _ROMAN[ch]
        total += -v if i + 1 < len(text) and _ROMAN[text[i + 1]] > v else v
    return total


def _small_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text in _UNITS:
        return _UNITS[text]
    if text in _TENS:
        return _TENS[text]
    return _roman(text)


def _paren_tokens(group: str) -> list[str]:
    out = []
    for item in _INNER_PAREN.findall(group):
        out.append(f"sub:{item}" if item[0].isdigit() else f"para:{item}")
    return out


def extract_tokens(text: str, *, source: bool = False) -> set[str]:
    """Typed tokens in `text` (design section 3.2). `source=True` reads a chunk generously: every
    "(n)" and "(x)" counts, letter ranges are expanded, and every typed value also counts as a plain
    number, so a claim that states a figure in another form still finds it."""
    text = _numbers_from_words(_clean(text).lower(), units_only=not source)
    tokens: set[str] = set()
    chars = list(text)

    def mask(m: re.Match) -> None:
        for i in range(m.start(), m.end()):
            chars[i] = " "

    def current() -> str:
        return "".join(chars)

    for m in list(_DATE_DMY.finditer(current())):
        tokens.add(f"date:{m.group(3)}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}")
        if source:
            tokens.update({f"num:{int(m.group(1))}", f"num:{m.group(3)}"})
        mask(m)
    for m in list(_DATE_MDY.finditer(current())):
        tokens.add(f"date:{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}")
        if source:
            tokens.update({f"num:{int(m.group(2))}", f"num:{m.group(3)}"})
        mask(m)
    for m in list(_SCHEDULE.finditer(current())):
        word = m.group(1) or m.group(2)
        n = _ORDINALS.get(word) or int(re.sub(r"\D", "", word))
        tokens.add(f"sch:{n}")
        mask(m)
    for m in list(_SECTION.finditer(current())):
        tokens.add(f"sec:{int(m.group(1))}")
        tokens.update(_paren_tokens(m.group(2)))
        for n in _LIST_NUM.findall(m.group(3)):
            tokens.add(f"sec:{int(n)}")
        tokens.update(_paren_tokens(m.group(3)))
        mask(m)
    for pattern in (_SUBSECTION, _PARAGRAPH):
        for m in list(pattern.finditer(current())):
            tokens.update(_paren_tokens(m.group(1)))
            mask(m)
    for m in list(_PARAGRAPH_NUM.finditer(current())):
        tokens.add(f"para:{m.group(1)}")
        mask(m)
    for pattern, kind in ((_CHAPTER, "chap"), (_PART, "part")):
        for m in list(pattern.finditer(current())):
            n = _small_number(m.group(1))
            if n is not None:
                tokens.add(f"{kind}:{n}")
                mask(m)
    for m in list(_PERCENT.finditer(current())):
        tokens.add(f"pct:{_value(m.group(1))}")
        mask(m)
    for pattern in (_AMOUNT_CURRENCY, _AMOUNT_SCALE):
        for m in list(pattern.finditer(current())):
            tokens.add(f"amt:{_scaled(m.group(1), m.group(2))}")
            mask(m)
    for m in list(_AMOUNT_NAIRA.finditer(current())):
        tokens.add(f"amt:{_scaled(m.group(1), None)}")
        mask(m)
    if source:
        for m in _PAREN_RANGE.finditer(current()):
            for c in range(ord(m.group(1)), ord(m.group(2)) + 1):
                tokens.add(f"para:{chr(c)}")
        for m in _PAREN.finditer(current()):
            tokens.add(f"sub:{m.group(1)}" if m.group(1)[0].isdigit() else f"para:{m.group(1)}")
        for m in list(_PAREN.finditer(current())):
            mask(m)
    for m in _PLAIN.finditer(current()):
        tokens.add(f"num:{_value(m.group(1))}")
    if source:
        tokens.update(f"num:{t.split(':', 1)[1]}" for t in list(tokens)
                      if t.split(":", 1)[0] in ("pct", "amt", "sec", "sch", "sub")
                      and re.fullmatch(r"\d+(?:\.\d+)?", t.split(":", 1)[1]))
    return tokens


def source_tokens(chunk: dict) -> set[str]:
    tokens = extract_tokens(chunk["text"], source=True)
    tokens |= extract_tokens(chunk["citation"], source=True)
    tokens |= extract_tokens(chunk["context_header"], source=True)
    tokens.update(f"sec:{n}" for n in _HEADER_SECTION.findall(chunk["context_header"]))
    tokens.update(f"num:{n}" for n in _HEADER_SECTION.findall(chunk["context_header"]))
    if ":sch" in chunk["chunk_id"]:  # norm/3: Schedules number their paragraphs "1.", "2.", ...
        for n in _LINE_NUMBER.findall(chunk["text"]):
            tokens.update({f"para:{int(n)}", f"num:{int(n)}"})
    return tokens


# Rule check --------------------------------------------------------------------------------------


@dataclass
class RuleCheck:
    passed: bool
    tokens: list[str]
    missing: list[str]
    reason: str | None          # figure_not_in_source | reference_not_in_source
    hint: str | None            # a hardening word no cited chunk contains

    def as_log(self) -> dict:
        return {"passed": self.passed, "tokens": self.tokens, "missing": self.missing, "reason": self.reason,
                "hint": self.hint, "normaliser": NORMALISER_VERSION}


def _has_word(text: str, word: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE) is not None


def rule_check(claim_text: str, chunks: list[dict]) -> RuleCheck:
    claim = extract_tokens(claim_text)
    source: set[str] = set()
    for chunk in chunks:
        source |= source_tokens(chunk)
    missing = sorted(claim - source)
    reason = None
    if missing:
        kinds = {t.split(":", 1)[0] for t in missing}
        reason = "figure_not_in_source" if kinds & {"pct", "amt", "num", "date"} else "reference_not_in_source"
    hint = None
    for word in HARDENING:
        if _has_word(claim_text, word) and not any(_has_word(chunk["text"], word) for chunk in chunks):
            hint = word
            break
    return RuleCheck(not missing, sorted(claim), missing, reason, hint)


def hint_note(word: str) -> str:
    return f'The statement uses "{word}"; the passages do not contain that word.'


# Evidence check ----------------------------------------------------------------------------------


_DASH_SPACE = re.compile(r"\s*-\s*")


def _evidence_form(text: str) -> str:
    """Quote marks and dashes unified, whitespace collapsed, whitespace next to a dash removed on both
    sides (norm/5: a dash at a line end, "of-" then "(a)" on the next line, reads as "of-(a)"), letter
    case folded (norm/4)."""
    return _DASH_SPACE.sub("-", re.sub(r"\s+", " ", _clean(text)).strip()).casefold()


def evidence_found(quote: str, chunk_text: str, context_header: str = "") -> bool:
    """True if `quote` (at least MIN_QUOTE_CHARS after normalising) is in the passage's text or its
    context header (norm/5, D-55: the verifier is shown the header as the passage's location)."""
    quote = _evidence_form(quote)
    if len(quote) < MIN_QUOTE_CHARS:
        return False
    return any(quote in _evidence_form(source) for source in (chunk_text, context_header) if source)


def passage_label(label: str) -> str:
    """The passage label the verifier meant: "P1 s11(1)(a)" or "P2 section 151(2)" -> "P1", "P2"
    (calibration run 1). Anything else is returned unchanged, so it matches no cited passage."""
    m = _PASSAGE_LABEL.match(label)
    return m.group(1).upper() if m else label


# LLM check ---------------------------------------------------------------------------------------


@dataclass
class ClaimCheck:
    no: int
    text: str
    labels: list[str]                   # cited labels, accepted by the parser
    chunk_ids: list[str]
    rule: RuleCheck
    verdict: str = "unverified"         # final: supported | unsupported | contradicted | not_a_claim | unverified
    reason: str | None = None
    sub_reason: str | None = None       # call_failed | evidence_not_found (unverified only)
    llm_verdict: str | None = None      # as returned, before the rule and evidence checks
    reasoning: str = ""
    second: dict | None = None          # D-46: the second call on a `contradicted` verdict
    unsupported_part: str = ""
    evidence: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    kept_chunk_ids: list[str] = field(default_factory=list)
    action: str = "removed"             # kept | moved_not_rendered (D-59) | removed
    request: dict | None = None
    response: dict | None = None
    error: str | None = None
    usage: dict = field(default_factory=lambda: dict(ZERO_USAGE))
    cost_usd: float = 0.0
    latency_ms: int = 0

    def as_log(self) -> dict:
        return {"no": self.no, "text": self.text, "labels": self.labels, "chunk_ids": self.chunk_ids,
                "rule": self.rule.as_log(), "verdict": self.verdict, "reason": self.reason,
                "sub_reason": self.sub_reason, "llm_verdict": self.llm_verdict, "reasoning": self.reasoning,
                "second": self.second, "unsupported_part": self.unsupported_part, "evidence": self.evidence, "citations": self.citations,
                "kept_chunk_ids": self.kept_chunk_ids, "action": self.action, "request": self.request,
                "response": self.response, "error": self.error, "usage": self.usage,
                "cost_usd": round(self.cost_usd, 6), "latency_ms": self.latency_ms}


def _response_log(response: LLMResponse, model: str) -> dict:
    return {"request_id": response.request_id, "model": response.model, "stop_reason": response.stop_reason,
            "stop_details": response.stop_details, "content": response.content, "usage": response.usage,
            "latency_ms": response.latency_ms, "cost_usd": round(cost_usd(response.usage, model), 6)}


def _verdict_problem(data) -> str | None:
    if not isinstance(data, dict) or set(data) != {"reasoning", "verdict", "reason", "unsupported_part", "evidence",
                                                   "citations"}:
        return "top-level keys"
    if not isinstance(data["reasoning"], str):
        return "reasoning"
    if data["verdict"] not in VERDICTS or data["reason"] not in REASONS:
        return "verdict or reason"
    if not isinstance(data["unsupported_part"], str):
        return "unsupported_part"
    for item in data["evidence"] if isinstance(data["evidence"], list) else [None]:
        if not isinstance(item, dict) or set(item) != {"label", "quote"} \
                or not all(isinstance(v, str) for v in item.values()):
            return "evidence"
    for item in data["citations"] if isinstance(data["citations"], list) else [None]:
        if not isinstance(item, dict) or set(item) != {"label", "supports"} \
                or not isinstance(item["label"], str) or not isinstance(item["supports"], bool):
            return "citations"
    return None


def _call(client: LLMClient, params: dict) -> tuple[LLMResponse | None, dict | None, str | None]:
    """(response, parsed JSON, error). Any failure returns an error string and no data."""
    try:
        response = client.create(params)
    except GenerationError as exc:
        return None, None, str(exc)
    if response.stop_reason != "end_turn":
        return response, None, f"stop_reason {response.stop_reason}"
    try:
        return response, json.loads(response.text), None
    except json.JSONDecodeError:
        return response, None, "output is not valid JSON"


def _second_look(check: ClaimCheck, verifier: LLMClient, params: dict) -> None:
    """D-46: a `contradicted` verdict refuses the whole answer, so it is asked again with the same
    request. It stands only if the second call also says `contradicted`; any other answer, or a
    failed call, makes the claim `unsupported` (removed, and the answer not refused for it)."""
    response, data, error = _call(verifier, params)
    if error is None and _verdict_problem(data):
        error = f"output does not match the schema ({_verdict_problem(data)})"
    second = {"verdict": None if error else data["verdict"], "reason": None if error else data["reason"],
              "reasoning": None if error else data["reasoning"], "error": error,
              "response": None if response is None else _response_log(response, verifier.model)}
    if response is not None:
        check.usage = {k: check.usage.get(k, 0) + response.usage.get(k, 0) for k in ZERO_USAGE}
        check.cost_usd += cost_usd(response.usage, verifier.model)
        check.latency_ms += response.latency_ms
    check.second = second
    if second["verdict"] != "contradicted":
        check.verdict = "unsupported"  # the reason stays the first call's


def check_claim(no: int, text: str, cited: list[tuple[str, dict]], verifier: LLMClient, *,
                effort: str = VERIFIER_EFFORT, max_tokens: int = VERIFIER_MAX_TOKENS,
                always_call: bool = False) -> ClaimCheck:
    """One claim against its cited passages (`cited`: label and chunk pairs). The LLM call is
    skipped when the rule check fails, unless `always_call` (verifier calibration)."""
    chunks = [chunk for _, chunk in cited]
    check = ClaimCheck(no, text, [label for label, _ in cited], [chunk["chunk_id"] for chunk in chunks],
                       rule_check(text, chunks))
    if not check.rule.passed:
        check.verdict, check.reason = "unsupported", check.rule.reason
        if not always_call:
            return check

    note = hint_note(check.rule.hint) if check.rule.hint else None
    params = request_params(VERIFIER_SYSTEM_PROMPT, build_verifier_message(cited, text, note), VERIFIER_SCHEMA,
                            model=verifier.model, effort=effort, max_tokens=max_tokens)
    check.request = {"prompt_version": VERIFIER_PROMPT_VERSION, "schema_version": VERIFIER_SCHEMA_VERSION, **params}
    response, data, error = _call(verifier, params)
    if response is not None:
        check.response = _response_log(response, verifier.model)
        check.usage, check.latency_ms = response.usage, response.latency_ms
        check.cost_usd = cost_usd(response.usage, verifier.model)
    if error is None and _verdict_problem(data):
        error = f"output does not match the schema ({_verdict_problem(data)})"
    if error is not None:
        check.error = error
        if check.rule.passed:
            check.verdict, check.reason, check.sub_reason = "unverified", None, "call_failed"
        return check

    check.llm_verdict = data["verdict"]
    check.reasoning = data["reasoning"]
    check.unsupported_part = data["unsupported_part"]
    by_label = dict(cited)
    evidence = [(passage_label(e["label"]), e["quote"]) for e in data["evidence"]]
    check.evidence = [{"label": label, "quote": quote,
                       "found": label in by_label and evidence_found(quote, by_label[label]["text"],
                                                                     by_label[label].get("context_header", ""))}
                      for label, quote in evidence]
    check.citations = [{"label": passage_label(c["label"]), "supports": c["supports"]} for c in data["citations"]]
    if not check.rule.passed:
        return check  # the rule check decides; the LLM verdict is logged for calibration
    check.verdict, check.reason = data["verdict"], data["reason"]
    if check.verdict == "contradicted":
        _second_look(check, verifier, params)
    if check.verdict == "supported":
        found = [e["label"] for e in check.evidence if e["found"]]
        if not found:
            check.verdict, check.sub_reason = "unverified", "evidence_not_found"
            return check
        dropped = {c["label"] for c in check.citations if c["supports"] is False} - set(found)
        check.kept_chunk_ids = [by_label[label]["chunk_id"] for label in check.labels if label not in dropped]
    return check


# Gaps --------------------------------------------------------------------------------------------


@dataclass
class GapCheck:
    text: str
    origin: str             # model (claims judged not_a_claim are no longer moved here, D-59)
    gap_class: str | None
    kept: bool
    reason: str | None      # disputed_flag_repeat | legal_content | advice | unclassified | check_failed | fixed

    def as_log(self) -> dict:
        return {"text": self.text, "origin": self.origin, "class": self.gap_class, "kept": self.kept,
                "reason": self.reason}


@dataclass
class GapResult:
    gaps: list[GapCheck]
    request: dict | None = None
    response: dict | None = None
    error: str | None = None
    usage: dict = field(default_factory=lambda: dict(ZERO_USAGE))
    cost_usd: float = 0.0
    latency_ms: int = 0

    @property
    def kept(self) -> list[str]:
        return [g.text for g in self.gaps if g.kept]

    def as_log(self) -> dict:
        return {"gaps": [g.as_log() for g in self.gaps], "request": self.request, "response": self.response,
                "error": self.error, "usage": self.usage, "cost_usd": round(self.cost_usd, 6),
                "latency_ms": self.latency_ms}


def check_gaps(question: str, gaps: list[tuple[str, str]], verifier: LLMClient | None, disputed_patterns, *,
               effort: str = VERIFIER_EFFORT, max_tokens: int = VERIFIER_MAX_TOKENS) -> GapResult:
    """Pattern filter, then one classifier call over the rest (design section 3.6). `gaps` holds
    (text, origin) pairs. The fixed no-passages gap is never checked."""
    checks = []
    for text, origin in gaps:
        if text == NO_PASSAGES_GAP:
            checks.append(GapCheck(text, origin, None, True, "fixed"))
        elif any(p.search(text) for p in disputed_patterns):
            checks.append(GapCheck(text, origin, "disputed_flag_repeat", False, "disputed_flag_repeat"))
        else:
            checks.append(GapCheck(text, origin, None, False, None))
    pending = [g for g in checks if g.reason is None]
    result = GapResult(checks)
    if not pending:
        return result
    params = request_params(GAP_SYSTEM_PROMPT, build_gap_message(question, [g.text for g in pending]), GAP_SCHEMA,
                            model=verifier.model, effort=effort, max_tokens=max_tokens)
    result.request = {"prompt_version": GAP_PROMPT_VERSION, "schema_version": GAP_SCHEMA_VERSION, **params}
    response, data, error = _call(verifier, params)
    if response is not None:
        result.response = _response_log(response, verifier.model)
        result.usage, result.latency_ms = response.usage, response.latency_ms
        result.cost_usd = cost_usd(response.usage, verifier.model)
    if error is None and not (isinstance(data, dict) and isinstance(data.get("gaps"), list)):
        error = "output does not match the schema (gaps)"
    if error is not None:
        result.error = error
        for g in pending:
            g.reason = "check_failed"
        return result
    classes = {}
    for item in data["gaps"]:
        if isinstance(item, dict) and isinstance(item.get("index"), int) and item.get("class") in GAP_CLASSES:
            classes.setdefault(item["index"], item["class"])
    for i, g in enumerate(pending):
        g.gap_class = classes.get(i)
        g.kept = g.gap_class == "missing_information"
        g.reason = None if g.kept else (g.gap_class or "unclassified")
    return result


# Action rule -------------------------------------------------------------------------------------


@dataclass
class VerificationResult:
    status: str                         # answered | not_covered | refused | error
    refusal_reason: str | None          # unsupported_answer | verification_unavailable
    kept: list[ClaimCheck]
    gaps: list[str]
    removal_lines: list[str]
    flags: list[str]
    checks: list[ClaimCheck]
    gap_result: GapResult | None
    summary: dict
    effort: str
    verifier_model: str
    usage: dict
    cost_usd: float
    latency_ms: int

    @property
    def kept_chunk_ids(self) -> list[str]:
        out: list[str] = []
        for check in self.kept:
            for chunk_id in check.kept_chunk_ids:
                if chunk_id not in out:
                    out.append(chunk_id)
        return out

    def as_log(self) -> dict:
        return {"verifier_model": self.verifier_model, "prompt_version": VERIFIER_PROMPT_VERSION,
                "schema_version": VERIFIER_SCHEMA_VERSION, "gap_prompt_version": GAP_PROMPT_VERSION,
                "gap_schema_version": GAP_SCHEMA_VERSION, "normaliser": NORMALISER_VERSION, "effort": self.effort,
                "claims": [c.as_log() for c in self.checks],
                "gaps": None if self.gap_result is None else self.gap_result.as_log(),
                "actions": [{"no": c.no, "action": c.action, "verdict": c.verdict} for c in self.checks],
                "summary": self.summary, "usage": self.usage, "cost_usd": round(self.cost_usd, 6),
                "latency_ms": self.latency_ms}


def _plural(lines: tuple[str, str], n: int) -> str:
    return lines[0] if n == 1 else lines[1].format(n=n)


def _refuses(removed: int, n: int, kept: int) -> bool:
    """Answer-level rules 3 and 4 over the claims that were judged (n of them)."""
    return n > 0 and (kept == 0 or removed > n / 2)


def apply_action_rule(model_status: str, checks: list[ClaimCheck]) -> tuple[str, str | None, list[str], list[str]]:
    """(status, refusal reason, removal lines, flags) for the checked claims, by design section
    3.7. Sets each check's action. A claim judged not_a_claim is dropped as `moved_not_rendered`
    and counts neither as checked nor as removed (D-59)."""
    for c in checks:
        c.action = (MOVED_NOT_RENDERED if c.verdict == "not_a_claim" else "kept" if c.verdict == "supported"
                    else "removed")
    judged = [c for c in checks if c.action != MOVED_NOT_RENDERED]
    n = len(judged)
    kept = sum(c.action == "kept" for c in judged)
    failed = sum(c.verdict in ("unsupported", "contradicted") for c in judged)
    unverified = sum(c.verdict == "unverified" for c in judged)
    call_failures = sum(c.sub_reason == "call_failed" for c in judged)
    removed = failed + unverified

    if any(c.verdict == "contradicted" for c in judged):
        return "refused", "unsupported_answer", [], []
    if _refuses(removed, n, kept) and not _refuses(removed - call_failures, n - call_failures, kept):
        return "error", "verification_unavailable", [], []
    if _refuses(removed, n, kept):
        return "refused", "unsupported_answer", [], []
    if kept == 0:  # nothing was judged: the model declined, or every claim was not_a_claim
        return "not_covered", None, [], []
    lines, flags = [], []
    if failed:
        lines.append(_plural(FAILED_LINE, failed))
        flags.append("claims_removed")
    if unverified:
        lines.append(_plural(UNVERIFIED_LINE, unverified))
        flags.append("claims_unverified")
    return "answered", None, lines, flags


def verify_answer(question: str, model_status: str, claims: list, gaps: list[str],
                  chunks: dict[str, dict], label_of: dict[str, str], verifier: LLMClient, disputed_patterns, *,
                  effort: str = VERIFIER_EFFORT, max_tokens: int = VERIFIER_MAX_TOKENS,
                  max_workers: int = MAX_WORKERS, always_call: bool = False) -> VerificationResult:
    """Verify a parsed answer (`model_status` "answered" or "not_covered"). `claims` are parser
    claims (text, chunk_ids); `label_of` maps chunk id to the label it was shown under."""
    def one(item):
        no, claim = item
        cited = [(label_of[c], chunks[c]) for c in claim.chunk_ids]
        return check_claim(no, claim.text, cited, verifier, effort=effort, max_tokens=max_tokens,
                           always_call=always_call)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        checks = list(pool.map(one, enumerate(claims, start=1)))
    status, reason, lines, flags = apply_action_rule(model_status, checks)

    gap_input = [(g, "model") for g in gaps]  # D-59: not_a_claim statements are never rendered
    gap_result = None
    kept_gaps: list[str] = []
    if gap_input and status in ("answered", "not_covered"):
        gap_result = check_gaps(question, gap_input, verifier, disputed_patterns, effort=effort,
                                max_tokens=max_tokens)
        kept_gaps = gap_result.kept
        if any(not g.kept for g in gap_result.gaps):
            flags.append("gaps_removed")

    usage = dict(ZERO_USAGE)
    parts = [c.usage for c in checks] + ([gap_result.usage] if gap_result else [])
    for part in parts:
        for key in usage:
            usage[key] += part.get(key, 0)
    cost = sum(c.cost_usd for c in checks) + (gap_result.cost_usd if gap_result else 0.0)
    latency = int((time.monotonic() - started) * 1000)
    verdicts = {v: sum(c.verdict == v for c in checks) for v in (*VERDICTS, "unverified")}
    outcome = {"answered": "claims_removed" if lines else "passed", "not_covered": "passed",
               "refused": "refused", "error": "unavailable"}[status]
    summary = {"outcome": outcome, "status": status, "refusal_reason": reason, "verdicts": verdicts,
               "claims_checked": sum(c.action != MOVED_NOT_RENDERED for c in checks),
               "claims_kept": sum(c.action == "kept" for c in checks),
               "claims_removed": sum(c.action == "removed" for c in checks),
               "claims_moved": sum(c.action == MOVED_NOT_RENDERED for c in checks),  # moved_not_rendered
               "gaps_kept": len(kept_gaps),
               "gaps_removed": 0 if gap_result is None else sum(not g.kept for g in gap_result.gaps)}
    return VerificationResult(status, reason, [c for c in checks if c.action == "kept"], kept_gaps, lines, flags,
                              checks, gap_result, summary, effort, verifier.model, usage, cost, latency)
