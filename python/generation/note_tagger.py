"""Note tagger (D-77; G2 fix design 2, section 2.3(b)).

    from generation.note_tagger import tag_question
    result = tag_question(question, client)   # client: an LLMClient for claude-haiku-5-5
    result.labels                             # e.g. ["stamp_duties"], or [] when no topic is raised

One Haiku 5.5 call per question, at effort low, with structured output (`reasoning`, then `labels`
from the closed enum in `generation.prompt.TAG_SCHEMA`). The tagger only chooses which fixed notes
apply: it never writes note text, never refuses, and never sees the passages.

**Validation (in code).** The call must stop with `end_turn`, its text must parse as JSON with
exactly the keys `reasoning` (a string) and `labels` (a list of strings), and every label must be
in the closed list; duplicates are dropped. More than `MAX_LABELS` distinct labels counts as
failed (the most seen in 1,028 design-session calls was 2). Any failure (an API or timeout error, a
`max_tokens` stop, invalid output, an unknown label, too many labels) gives status `failed` with
the reason, and the answer then shows the pattern notes only. An API error is also raised by the
runner's shared stop wrapper first (D-69), so a tuning or decision run stops after that case.

**Mapping.** A topic label maps to the note `scope:<topic>` with the guardrails policy's note text;
`ntaa_commencement` maps to DQ-12 under the same Act rule as the patterns
(`disclosure.names_only_other_acts`). The notes shown are the union of the pattern notes and the
tagger notes (`merge_notes`).
"""
from __future__ import annotations

import json
import sys
import time
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
TAGGER_MAX_TOKENS = 4_000  # a ceiling, not a cost; a max_tokens stop is a tagger failure
MAX_LABELS = 3
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


@dataclass
class TaggerResult:
    status: str                         # ok | failed | off
    labels: list[str] = field(default_factory=list)
    reason: str | None = None           # why it failed
    reasoning: str = ""
    request: dict | None = None
    response: dict | None = None
    usage: dict = field(default_factory=lambda: dict(ZERO_USAGE))
    cost_usd: float = 0.0
    latency_ms: int = 0

    @property
    def scope_topics(self) -> list[str]:
        return [label for label in self.labels if label != NTAA_COMMENCEMENT]

    @property
    def dq12(self) -> bool:
        return NTAA_COMMENCEMENT in self.labels

    def as_log(self) -> dict:
        return {"status": self.status, "labels": self.labels, "reason": self.reason, "reasoning": self.reasoning,
                "prompt_version": TAG_PROMPT_VERSION, "schema_version": TAG_SCHEMA_VERSION,
                "request": self.request, "response": self.response, "usage": self.usage,
                "cost_usd": round(self.cost_usd, 6), "latency_ms": self.latency_ms}


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


def tag_question(question: str, client: LLMClient, *, effort: str = TAGGER_EFFORT,
                 max_tokens: int = TAGGER_MAX_TOKENS) -> TaggerResult:
    """One tagger call. Never raises for a model or API failure: the result is `failed`."""
    params = request_params(TAG_SYSTEM_PROMPT, build_tag_message(question), TAG_SCHEMA, model=client.model,
                            effort=effort, max_tokens=max_tokens)
    result = TaggerResult("failed", request={"prompt_version": TAG_PROMPT_VERSION,
                                             "schema_version": TAG_SCHEMA_VERSION, **params})
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
