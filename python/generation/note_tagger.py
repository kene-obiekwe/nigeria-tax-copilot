"""Note tagger (D-77, D-88; G2 fix design 2, sections 2.3(b) and 14).

    from generation.note_tagger import tag_question
    result = tag_question(question, client)   # client: an LLMClient for claude-haiku-5-5
    result.labels                             # e.g. ["stamp_duties"], or [] when no topic is raised

**Majority vote (D-88).** `tag_question` makes `TAGGER_VOTES` (3) tagger calls in parallel, each one
`tag_once`. A label is shown when at least `TAGGER_THRESHOLD` (2) of the successful calls give it,
so with two successful calls both must give it. Fewer than `TAGGER_THRESHOLD` successful calls means
the tagger has failed: the answer shows the pattern notes only, and the reason is logged. The log
keeps every call (status, labels, reason, reasoning, response, usage, cost, latency) beside the final
labels; the request is the same for every call and is logged once. Cost and usage are the sums over
the calls; the latency is the wall time of the vote. An API error in any call trips the run's shared
stop (D-69) through the client wrapper the runner passes; calls already in flight finish, as
parallel verifier calls do.

**One call** (`tag_once`): one Haiku 5.5 call at effort low, with structured output (`reasoning`,
then `labels` from the closed enum in `generation.prompt.TAG_SCHEMA`). The tagger only chooses which
fixed notes apply: it never writes note text, never refuses, and never sees the passages.

**Validation (in code, per call).** The call must stop with `end_turn`, its text must parse as JSON
with exactly the keys `reasoning` (a string) and `labels` (a list of strings), and every label must
be in the closed list; duplicates are dropped. More than `MAX_LABELS` distinct labels counts as
failed (the most seen in 1,028 design-session calls was 2). Any failure (an API or timeout error, a
`max_tokens` stop, invalid output, an unknown label, too many labels) makes that call `failed` with
the reason.

**Mapping.** A topic label maps to the note `scope:<topic>` with the guardrails policy's note text;
`ntaa_commencement` maps to DQ-12 under the same Act rule as the patterns
(`disclosure.names_only_other_acts`). The notes shown are the union of the pattern notes and the
tagger notes (`merge_notes`).
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from generation.disclosure import Disclosure  # noqa: E402
from generation.llm import PRICES, GenerationError, LLMClient, cost_usd, request_params  # noqa: E402
from generation.prompt import (NTAA_COMMENCEMENT, TAG_LABELS, TAG_PROMPT_VERSION, TAG_SCHEMA,  # noqa: E402
                               TAG_SCHEMA_VERSION, TAG_SYSTEM_PROMPT, build_tag_message)

TAGGER_MODEL = "claude-haiku-5-5"
TAGGER_EFFORT = "low"
TAGGER_MAX_TOKENS = 4_000  # a ceiling, not a cost; a max_tokens stop is a failed call
TAGGER_VOTES = 3           # D-88: calls per question, in parallel
TAGGER_THRESHOLD = 2       # D-88: a label is shown when at least this many successful calls give it
MAX_LABELS = 3
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


@dataclass
class TaggerResult:
    status: str                         # ok | failed | off
    labels: list[str] = field(default_factory=list)
    reason: str | None = None           # why it failed
    reasoning: str = ""
    request: dict | None = None
    response: dict | None = None        # one call only; a vote keeps each call's response in `calls`
    usage: dict = field(default_factory=lambda: dict(ZERO_USAGE))
    cost_usd: float = 0.0
    latency_ms: int = 0
    calls: list[TaggerResult] = field(default_factory=list)  # a vote's calls (D-88); empty for one call
    votes: int | None = None
    threshold: int | None = None

    @property
    def scope_topics(self) -> list[str]:
        return [label for label in self.labels if label != NTAA_COMMENCEMENT]

    @property
    def dq12(self) -> bool:
        return NTAA_COMMENCEMENT in self.labels

    def call_log(self) -> dict:
        return {"status": self.status, "labels": self.labels, "reason": self.reason, "reasoning": self.reasoning,
                "response": self.response, "usage": self.usage, "cost_usd": round(self.cost_usd, 6),
                "latency_ms": self.latency_ms}

    def as_log(self) -> dict:
        log = {"status": self.status, "labels": self.labels, "reason": self.reason, "reasoning": self.reasoning,
               "prompt_version": TAG_PROMPT_VERSION, "schema_version": TAG_SCHEMA_VERSION,
               "request": self.request, "response": self.response, "usage": self.usage,
               "cost_usd": round(self.cost_usd, 6), "latency_ms": self.latency_ms}
        if self.votes is not None:
            log |= {"votes": self.votes, "threshold": self.threshold, "calls": [c.call_log() for c in self.calls]}
        return log


OFF = TaggerResult("off")  # shared and never mutated; Answer uses its own copy


def validate(text: str) -> tuple[list[str] | None, str, str | None]:
    """(labels, reasoning, error) for the tagger's output text."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "", "output is not valid JSON"
    if not isinstance(data, dict) or set(data) != {"reasoning", "labels"}:
        return None, "", "output does not match the schema (top-level keys)"
    if not isinstance(data["reasoning"], str) or not isinstance(data["labels"], list) \
            or not all(isinstance(label, str) for label in data["labels"]):
        return None, "", "output does not match the schema (types)"
    unknown = [label for label in data["labels"] if label not in TAG_LABELS]
    if unknown:
        return None, data["reasoning"], f"unknown label {unknown[0]!r}"
    labels = list(dict.fromkeys(data["labels"]))
    if len(labels) > MAX_LABELS:
        return None, data["reasoning"], f"more than {MAX_LABELS} labels ({len(labels)})"
    return labels, data["reasoning"], None


def _request(client: LLMClient, question: str, effort: str, max_tokens: int) -> tuple[dict, dict]:
    params = request_params(TAG_SYSTEM_PROMPT, build_tag_message(question), TAG_SCHEMA, model=client.model,
                            effort=effort, max_tokens=max_tokens)
    return params, {"prompt_version": TAG_PROMPT_VERSION, "schema_version": TAG_SCHEMA_VERSION, **params}


def tag_once(question: str, client: LLMClient, *, effort: str = TAGGER_EFFORT,
             max_tokens: int = TAGGER_MAX_TOKENS) -> TaggerResult:
    """One tagger call. Never raises for a model or API failure: the result is `failed`."""
    params, logged = _request(client, question, effort, max_tokens)
    result = TaggerResult("failed", request=logged)
    started = time.monotonic()
    try:
        response = client.create(params)
    except GenerationError as exc:  # APIError (D-69), timeouts and set-up errors
        result.reason = f"call failed: {exc}"
        result.latency_ms = int((time.monotonic() - started) * 1000)
        return result
    result.response = {"request_id": response.request_id, "model": response.model,
                       "stop_reason": response.stop_reason, "stop_details": response.stop_details,
                       "content": response.content, "usage": response.usage, "latency_ms": response.latency_ms}
    result.usage, result.latency_ms = response.usage, response.latency_ms
    result.cost_usd = cost_usd(response.usage, client.model) if client.model in PRICES else 0.0  # fakes: 0
    if response.stop_reason != "end_turn":
        result.reason = f"stop_reason {response.stop_reason}"
        return result
    labels, reasoning, error = validate(response.text)
    result.reasoning = reasoning
    if error is not None:
        result.reason = error
        return result
    result.status, result.labels = "ok", labels
    return result


def _guarded_once(question, client, effort, max_tokens) -> TaggerResult:
    """`tag_once`, with any unexpected exception counted as that call's failure."""
    try:
        return tag_once(question, client, effort=effort, max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001 - tag_once catches model errors; this is a safety net
        return TaggerResult("failed", reason=f"tagger raised {type(exc).__name__}: {exc}")


def majority(calls: list[TaggerResult], threshold: int = TAGGER_THRESHOLD) -> tuple[list[str] | None, str | None]:
    """(labels, error) for a vote: the labels given by at least `threshold` successful calls, in
    the closed list's order; None and the reason when fewer than `threshold` calls succeeded."""
    ok = [c for c in calls if c.status == "ok"]
    if len(ok) < threshold:
        reasons = "; ".join(f"call {i}: {c.reason}" for i, c in enumerate(calls, 1) if c.status != "ok")
        return None, f"{len(ok)} of {len(calls)} calls succeeded, {threshold} needed ({reasons})"
    counts = {label: sum(label in c.labels for c in ok) for label in TAG_LABELS}
    return [label for label in TAG_LABELS if counts[label] >= threshold], None


def tag_question(question: str, client: LLMClient, *, effort: str = TAGGER_EFFORT,
                 max_tokens: int = TAGGER_MAX_TOKENS, votes: int = TAGGER_VOTES,
                 threshold: int = TAGGER_THRESHOLD) -> TaggerResult:
    """The note tagger (D-88): `votes` parallel calls, labels by majority. Never raises for a model
    or API failure: with fewer than `threshold` successful calls the result is `failed`."""
    if not 1 <= threshold <= votes:
        raise ValueError(f"tagger threshold {threshold} must be between 1 and votes {votes}")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=votes) as pool:
        calls = list(pool.map(lambda _: _guarded_once(question, client, effort, max_tokens), range(votes)))
    labels, error = majority(calls, threshold)
    usage = {key: sum((c.usage or {}).get(key) or 0 for c in calls) for key in ZERO_USAGE}
    return TaggerResult("ok" if error is None else "failed", labels=labels or [], reason=error,
                        request=next((c.request for c in calls if c.request is not None), None),
                        usage=usage, cost_usd=sum(c.cost_usd for c in calls),
                        latency_ms=int((time.monotonic() - started) * 1000),
                        calls=calls, votes=votes, threshold=threshold)


def scope_note(topic: str, policy) -> Disclosure:
    """The fixed scope note for `topic` (a guardrails policy topic), as the pre-check builds it."""
    pattern, text = policy.scope_topics[topic]
    return Disclosure("note", f"scope:{topic}", "question", "guardrail", f"{text} {policy.scope_note_suffix}")


def merge_notes(pattern_notes: list[Disclosure], tagger: TaggerResult, policy) -> list[Disclosure]:
    """The union of the pattern notes and the tagger's scope notes. Non-scope notes (references,
    other Acts) keep their order; scope notes follow, in the policy's topic order."""
    others = [n for n in pattern_notes if not n.ref.startswith("scope:")]
    by_ref = {n.ref: n for n in pattern_notes if n.ref.startswith("scope:")}
    for topic in tagger.scope_topics:
        if topic in policy.scope_topics:
            by_ref.setdefault(f"scope:{topic}", scope_note(topic, policy))
    return others + [by_ref[f"scope:{t}"] for t in policy.scope_topics if f"scope:{t}" in by_ref]
