"""The answer card (interface design, Sections 3.2 to 3.5, 7.3 and 7.5; D-155, D-165).

Pure part (no Streamlit call):

    blocks = answer_blocks(record, answer, chunks)   # the card, built from the gen-log/3 record and the Answer
    parity_ok(record, answer, chunks)                # "\\n\\n".join(block.text) == answer.rendered
    export_markdown(...)                             # the Markdown export of one answer
    unit_chunk_ids(chunk_id, meta)                   # the chunks "Read the whole section" shows

Parity rule (D-155). Each block carries the exact text that `render()` produced for that part, plus the
chunk ids its chips open. Before a card is shown the app checks that the joined block texts equal
`answer.rendered`. If the check fails the card falls back to `answer.rendered` as plain Markdown with no
chips, and the audit row gets the flag `ui_fallback`. The user therefore only ever sees text that the
pipeline rendered and logged. The card is not built by parsing the Markdown, except that the refusal or error
text of a refused or failed answer is, by design, the first block of `rendered`.

The UI part (`render_card` and the pieces under it) draws the blocks; it never formats or recomputes a
figure, a citation or a calculation.
"""
from __future__ import annotations

import functools
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Callable, Mapping

from app import copy as ui
from audit.project import APP_ERROR_TEXT
from embed.load import CHUNKS, read_chunks
from generation import generate as gen

STATUSES_WITH_NOTES_FIRST = ("answered", "not_covered")


@dataclass(frozen=True)
class Block:
    kind: str                       # notes | refusal | decline | claim | removal | calculation | withheld | gaps
                                    # | sources | differences | disclaimer | app_error
    text: str                       # exactly the text render() produced for this part
    chunk_ids: tuple[str, ...] = ()  # the chunks whose chips this block shows


# The card, from the record and the answer (Section 3.2) ---------------------------------------------------


def _citation(chunks: Mapping[str, dict], chunk_id: str) -> str:
    return chunks[chunk_id]["citation"]


def _pages(labels: list[str]) -> str:
    if not labels:
        return ""
    return f" (gazette page{'s' if len(labels) > 1 else ''} {', '.join(labels)})"


def _computation_view(record_: dict) -> SimpleNamespace:
    """What `calculation_lines()` reads from a Computation, taken from its log record."""
    keys = ("what", "from_question", "assumptions", "working", "result_text", "not_computed", "result_kind")
    return SimpleNamespace(**{k: record_[k] for k in keys})


def answer_blocks(record: dict, answer: gen.Answer, chunks: Mapping[str, dict]) -> list[Block]:
    """The answer card as blocks, in the order the pipeline renders them. `chunks` holds
    `corpus_chunks` rows (citation, page_labels, text, ...) for every passage of the answer."""
    status = answer.status
    blocks: list[Block] = []
    note_block = None
    if answer.notes:
        note_block = Block("notes", "Notes\n" + "\n".join(f"- {n.text}" for n in answer.notes))
    if status in STATUSES_WITH_NOTES_FIRST and note_block:
        blocks.append(note_block)

    if status == "answered":
        for claim in answer.claims:
            text = f"{claim.text} [{'; '.join(_citation(chunks, c) for c in claim.chunk_ids)}]"
            blocks.append(Block("claim", text, tuple(claim.chunk_ids)))
        verification = answer.verification
        for line in (verification.removal_lines if verification else []):
            blocks.append(Block("removal", line))
        listed = (record.get("parsed") or {}).get("calculations") or []
        by_id = {r["id"]: r for r in answer.computations if r["shown"]}
        shown_ids = [cid for cid in listed if cid in by_id]
        shown = [_computation_view(by_id[cid]) for cid in shown_ids]
        parts = gen.calculation_lines(answer.computations, shown)
        for i, part in enumerate(parts):
            if i < len(shown):
                blocks.append(Block("calculation", part, tuple(by_id[shown_ids[i]]["sources"])))
            else:
                blocks.append(Block("withheld", part))
    elif status == "not_covered":
        blocks.append(Block("decline", gen.DECLINE_TEXT))
    else:  # refused, error: the refusal or error text is the first block of `rendered` (Section 3.2)
        blocks.append(Block("refusal", answer.rendered.split("\n\n", 1)[0]))
        if note_block:
            blocks.append(note_block)

    if status in STATUSES_WITH_NOTES_FIRST and answer.gaps:
        blocks.append(Block("gaps", "Not covered by the retrieved provisions:\n"
                            + "\n".join(f"- {gap}" for gap in answer.gaps)))
    if status == "answered":
        cited = list(answer.cited_chunk_ids)
        blocks.append(Block("sources", "Sources\n" + "\n".join(
            f"- {_citation(chunks, c)}{_pages(chunks[c]['page_labels'])}" for c in cited), tuple(cited)))
    shown_disclosures = [d for d in answer.disclosures if d.kind != "hidden"]
    if shown_disclosures:
        lines = []
        for d in shown_disclosures:
            refs = ", ".join([d.ref] + d.merged)
            lead = "" if d.applies_to == "question" else f"{d.applies_to}: "
            lines.append(f"- {lead}{d.text} ({refs})")
        blocks.append(Block("differences", "Differences between official texts\n" + "\n".join(lines)))
    blocks.append(Block("disclaimer", gen.DISCLAIMER))
    return blocks


def joined(blocks: list[Block]) -> str:
    return "\n\n".join(block.text for block in blocks)


def parity_ok(record: dict, answer: gen.Answer, chunks: Mapping[str, dict]) -> bool:
    """D-155: the joined block texts equal the answer's rendered text. A card that cannot be built is a
    parity failure (the plain-text fallback), never an error shown to the user."""
    try:
        return joined(answer_blocks(record, answer, chunks)) == answer.rendered
    except Exception:  # noqa: BLE001
        return False


def make_parity(load_chunks: Callable[[list[str]], dict[str, dict]]) -> Callable[[dict, gen.Answer], bool]:
    """The `parity` hook of `AppService`: loads the answer's chunks, then checks the rule."""
    def check(record: dict, answer: gen.Answer) -> bool:
        try:
            chunks = load_chunks(list(dict.fromkeys(p.chunk_id for p in answer.passages)))
        except Exception:  # noqa: BLE001
            return False
        return parity_ok(record, answer, chunks)
    return check


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# What a card holds (live answers and history) ------------------------------------------------------------


@dataclass
class CardData:
    reference: str
    query_id: str | None
    question: str
    asked_at: datetime
    status: str                         # answered | not_covered | refused | error | app_error
    refusal_reason: str | None
    text: str                           # what the card displays: rendered, or the app_error text
    blocks: list[Block] | None          # None: plain Markdown, no chips (fallback, or history)
    chunks: dict[str, dict] = field(default_factory=dict)  # chunk_id -> row, for chips and export
    chip_ids: list[str] = field(default_factory=list)      # history: the supporting passages
    disputed: set[str] = field(default_factory=set)
    config_sha256: str | None = None
    ui_fallback: bool = False
    history: bool = False


def card_from_outcome(outcome, question: str, asked_at: datetime, load_chunks: Callable[[list[str]], dict[str, dict]],
                      config_sha256: str | None) -> CardData:
    """The card for a worker outcome that may be shown (`final` or `app_error`)."""
    if outcome.kind == "app_error":
        return CardData(outcome.reference, outcome.query_id, question, asked_at, "app_error", None,
                        APP_ERROR_TEXT, None, config_sha256=config_sha256)
    answer, record = outcome.answer, outcome.record
    ids = list(dict.fromkeys(p.chunk_id for p in answer.passages))
    try:
        chunks = load_chunks(ids) if ids else {}
    except Exception:  # noqa: BLE001 - no chunk text: the plain text still shows
        chunks = {}
    blocks = None if outcome.ui_fallback or not parity_ok(record, answer, chunks) else answer_blocks(record, answer, chunks)
    return CardData(outcome.reference, outcome.query_id, question, asked_at, answer.status, answer.refusal_reason,
                    answer.rendered, blocks, chunks, disputed={p.chunk_id for p in answer.passages if p.disputed},
                    config_sha256=config_sha256, ui_fallback=blocks is None)


def card_from_history(item, load_chunks: Callable[[list[str]], dict[str, dict]]) -> CardData:
    """A stored answer from My questions: the stored rendered text, chips for the supporting passages."""
    ids = list(dict.fromkeys(p["chunk_id"] for p in item.cited_passages))
    try:
        chunks = load_chunks(ids) if ids else {}
    except Exception:  # noqa: BLE001
        chunks = {}
    return CardData(item.reference, item.query_id, item.question, item.received_at, item.status,
                    None, item.rendered or "", None, chunks, chip_ids=ids,
                    disputed={p["chunk_id"] for p in item.cited_passages if p.get("disputed")}, history=True)


def hint_for(status: str, reason: str | None) -> str | None:
    """The UI tip for a status and refusal reason, else the status's general tip, else None."""
    if (status, reason) in ui.HINTS:  # an explicit None means "no tip for this reason"
        return ui.HINTS[(status, reason)]
    return ui.HINTS.get((status, None))


# Export (Section 7.5) ---------------------------------------------------------------------------------------


def export_markdown(card: CardData) -> str:
    """The Markdown export: the question, the date and time, the reference, the rendered answer verbatim
    (claims with citations, gaps, sources, disclosures, the disclaimer with the snapshot date), the text of
    each cited chunk with its citation and gazette pages, and the configuration sha256."""
    lines = [f"# {ui.PAGE_TITLE}", "",
             f"## {ui.EXPORT_QUESTION}", "", card.question, "",
             f"- {ui.EXPORT_WHEN}: {card.asked_at.strftime('%Y-%m-%d %H:%M %Z').strip()}",
             f"- {ui.EXPORT_REFERENCE}: {card.reference}", "",
             f"## {ui.EXPORT_ANSWER}", "", card.text, ""]
    cited = [c for b in (card.blocks or []) for c in b.chunk_ids] if card.blocks is not None else card.chip_ids
    cited = list(dict.fromkeys(cited))
    if cited:
        lines += [f"## {ui.EXPORT_CITED}", ""]
        for chunk_id in cited:
            chunk = card.chunks.get(chunk_id)
            if chunk is None:
                continue
            lines += [f"### {chunk['citation']}", ""]
            if chunk.get("page_labels"):
                lines += [f"{ui.EXPORT_PAGES}: {', '.join(chunk['page_labels'])}", ""]
            lines += ["~~~text", chunk["text"], "~~~", ""]
    if card.config_sha256:
        lines += [f"{ui.EXPORT_CONFIG}: {card.config_sha256}", ""]
    return "\n".join(lines)


# "Read the whole section" (Section 3.3; D-165) -----------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def chunk_meta() -> tuple[dict, ...]:
    """Order and grouping of the corpus chunks, from the committed v1_chunks.jsonl (file order)."""
    return tuple({"chunk_id": c["chunk_id"], "unit_type": c["unit_type"], "act_id": c["act_id"],
                  "section": c["section"], "schedule": c["schedule"]} for c in read_chunks(CHUNKS))


def _definition_key(chunk_id: str) -> str:
    head, _, rest = chunk_id.partition(":def:")
    return f"{head}:def:{rest.split(':')[0]}"


def _paragraph_token(chunk_id: str) -> str | None:
    return next((p for p in chunk_id.split(":") if re.fullmatch(r"para\d+\w*", p)), None)


def unit_chunk_ids(chunk_id: str, meta=None) -> list[str]:
    """The chunk ids of the whole unit a cited chunk belongs to, in file order (D-165):

    - a definition: every chunk of that definition (the same id up to `:def:<term>`), never all the
      definitions of Section 202;
    - any other chunk: every non-definition chunk with the same Act and section, or (a Schedule chunk) the
      same Schedule, Part and paragraph."""
    meta = chunk_meta() if meta is None else meta
    target = next((m for m in meta if m["chunk_id"] == chunk_id), None)
    if target is None:
        return []
    if target["unit_type"] == "definition":
        key = _definition_key(chunk_id)
        return [m["chunk_id"] for m in meta if m["unit_type"] == "definition"
                and (m["chunk_id"] == key or m["chunk_id"].startswith(key + ":"))]
    others = [m for m in meta if m["unit_type"] != "definition" and m["act_id"] == target["act_id"]]
    if target["section"]:
        return [m["chunk_id"] for m in others if m["section"] == target["section"]]
    if target["schedule"]:
        def key_of(m):
            s = m["schedule"] or {}
            return (s.get("number"), s.get("part"), _paragraph_token(m["chunk_id"]))
        wanted = key_of(target)
        return [m["chunk_id"] for m in others if m["schedule"] and key_of(m) == wanted]
    return [chunk_id]


# The UI (Streamlit) -----------------------------------------------------------------------------------------------


def _md(text: str) -> str:
    """Markdown for display: `$` is escaped so that it is never read as a formula."""
    return text.replace("$", "\\$")


def chunk_view(st, chunk: dict | None, disputed: bool) -> None:
    """The citation label, gazette pages, the difference note and the verbatim chunk text."""
    if chunk is None:
        st.caption(ui.CHUNK_NOT_FOUND)
        return
    st.markdown(f"**{chunk['citation']}**")
    if chunk.get("context_header"):
        st.caption(chunk["context_header"])
    if chunk.get("page_labels"):
        st.caption(ui.CHIP_GAZETTE_PAGES.format(pages=", ".join(chunk["page_labels"])))
    if disputed:
        st.info(ui.CHIP_DISPUTED)
    st.text(chunk["text"])


def render_chips(st, card: CardData, chunk_ids, key: str, reader: Callable[[str], None] | None) -> None:
    """One popover per chunk: the verbatim chunk text, and a button that opens the whole section."""
    ids = [c for c in dict.fromkeys(chunk_ids) if c in card.chunks]
    if not ids:
        return
    columns = st.columns(min(len(ids), 3))
    for i, chunk_id in enumerate(ids):
        chunk = card.chunks[chunk_id]
        with columns[i % len(columns)]:
            with st.popover(chunk["citation"], use_container_width=True):
                chunk_view(st, chunk, chunk_id in card.disputed)
                if reader is not None and st.button(ui.READ_WHOLE_SECTION, key=f"{key}-read-{chunk_id}"):
                    reader(chunk_id)


def render_card(st, card: CardData, *, key: str, reader: Callable[[str], None] | None = None,
                feedback: Callable[[CardData, int, str | None], bool] | None = None) -> None:
    """Draw one card: status line, the blocks (or the plain text), the tip, and the footer."""
    st.markdown(f"**{ui.STATUS_LINES.get(card.status, card.status)}**")
    if card.blocks is not None:
        for i, block in enumerate(card.blocks):
            st.markdown(_md(block.text))
            if block.chunk_ids:
                render_chips(st, card, block.chunk_ids, f"{key}-b{i}", reader)
    else:
        st.markdown(_md(card.text))
        if card.history and card.chip_ids:
            st.caption(ui.HISTORY_SOURCES)
            render_chips(st, card, card.chip_ids, f"{key}-h", reader)
    tip = hint_for(card.status, card.refusal_reason)
    if tip and card.status != "answered":
        st.caption(f"{ui.HINT_LABEL}: {tip}")
    render_footer(st, card, key=key, feedback=feedback)


def render_footer(st, card: CardData, *, key: str,
                  feedback: Callable[[CardData, int, str | None], bool] | None) -> None:
    st.caption(ui.REFERENCE.format(reference=card.reference))
    left, right = st.columns([1, 2])
    with left:
        st.download_button(ui.EXPORT_BUTTON, export_markdown(card), file_name=ui.EXPORT_FILE.format(
            reference=card.reference), mime="text/markdown", key=f"{key}-export")
    with st.expander(ui.PLAIN_TEXT_EXPANDER):
        st.text(card.text)
    if feedback is not None and card.query_id and not card.history:
        render_feedback(st, card, key=key, save=feedback)


def render_feedback(st, card: CardData, *, key: str, save: Callable[[CardData, int, str | None], bool]) -> None:
    """Thumbs, then an optional fixed reason. No free text, so feedback cannot carry personal details."""
    saved = st.session_state.setdefault("feedback_saved", {})
    st.caption(ui.FEEDBACK_PROMPT)
    rating = st.feedback("thumbs", key=f"{key}-thumbs")
    if rating is None:
        return
    value = 1 if rating == 1 else -1
    reasons = list(ui.FEEDBACK_REASONS)
    reason = st.selectbox(ui.FEEDBACK_REASON_LABEL, [None, *reasons], key=f"{key}-reason",
                          format_func=lambda r: "" if r is None else ui.FEEDBACK_REASONS[r])
    if st.button(ui.FEEDBACK_SAVE, key=f"{key}-send"):
        if save(card, value, reason):
            saved[card.query_id] = (value, reason)
        else:
            st.warning(ui.FEEDBACK_FAILED)
    if card.query_id in saved:
        st.caption(ui.FEEDBACK_THANKS)


def make_reader(st, load_chunks: Callable[[list[str]], dict[str, dict]]) -> Callable[[str], None]:
    """The `reader` callable of `render_card`: opens the dialog that shows the whole unit of a cited chunk."""
    @st.dialog(ui.READER_DIALOG_TITLE, width="large")
    def dialog(chunk_id: str) -> None:
        ids = unit_chunk_ids(chunk_id)
        rows = {}
        try:
            rows = load_chunks(ids) if ids else {}
        except Exception:  # noqa: BLE001
            rows = {}
        st.caption(ui.READER_NOTE)
        if not rows:
            st.caption(ui.CHUNK_NOT_FOUND)
        for cid in ids:
            chunk = rows.get(cid)
            if chunk is None:
                continue
            with st.container(border=(cid == chunk_id)):
                if cid == chunk_id and len(ids) > 1:
                    st.caption(ui.READER_THIS_PASSAGE)
                st.markdown(f"**{chunk['citation']}**")
                if chunk.get("page_labels"):
                    st.caption(ui.CHIP_GAZETTE_PAGES.format(pages=", ".join(chunk["page_labels"])))
                st.text(chunk["text"])
    return dialog
